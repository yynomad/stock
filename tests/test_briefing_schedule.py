from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo
from stockman.storage import Store
from stockman.briefing.config import BriefingConfig
from stockman.briefing.schedule import Scheduler
from stockman.briefing.history import History
from stockman.briefing.locking import DirectoryLock
from stockman.briefing.pipeline import preview
from stockman.briefing.demo import SyntheticSource

def at(value):return datetime.fromisoformat(value).replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()

class BriefingScheduleTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'db');self.sent=[];self.built=[]
        self.config=BriefingConfig(enabled=True)
        def build(owner,now,day):
            self.built.append(day);return preview(self.store,[SyntheticSource()],owner,day,now)
        self.build=build
        self.scheduler=Scheduler(self.store,self.config,'123',{'123'},build,lambda r:self.sent.append(r) or 'sent')
    def tearDown(self):self.store.close();self.temp.cleanup()
    def test_time_boundary_weekend_and_calendar_timezone(self):
        self.assertEqual(self.scheduler.tick(at('2026-10-24T07:59:59'))['status'],'not_due')
        self.assertEqual(self.scheduler.tick(at('2026-10-24T08:00:00'))['status'],'sent')
        self.assertFalse(self.scheduler.tick(at('2026-10-24T08:00:01'))['delayed'] if 'delayed' in self.scheduler.tick(at('2026-10-24T08:00:01')) else False)
        self.assertEqual(len(self.sent),1);self.assertEqual(self.built,['2026-10-24'])
    def test_today_only_catchup_and_restart_dedup(self):
        result=self.scheduler.tick(at('2026-10-26T12:00:00'))
        self.assertTrue(result['delayed']);self.assertIn('仅当天',self.sent[0]['text'])
        self.store.close();self.store=Store(Path(self.temp.name)/'db')
        scheduler=Scheduler(self.store,self.config,'123',{'123'},self.build,lambda r:self.fail('replayed'))
        scheduler.recover();self.assertEqual(scheduler.tick(at('2026-10-26T13:00:00'))['status'],'sent')
        self.assertEqual(len(self.store.execute('SELECT * FROM brief_daily')),1)
    def test_unknown_partial_or_interrupted_send_never_replays(self):
        self.scheduler.send=lambda r:'unknown'
        self.assertEqual(self.scheduler.tick(at('2026-10-22T08:00:00'))['status'],'unknown')
        self.scheduler.send=lambda r:self.fail('replayed')
        self.scheduler.recover();self.assertEqual(self.scheduler.tick(at('2026-10-22T09:00:00'))['status'],'unknown')
        self.store.execute("UPDATE brief_daily SET status='sending'");self.scheduler.recover()
        self.assertEqual(self.scheduler.tick(at('2026-10-22T10:00:00'))['status'],'unknown')
    def interrupt_child(self, phase, timestamp):
        program = """
import os
from pathlib import Path
import sys
from stockman.storage import Store
from stockman.briefing.budget import Budget
from stockman.briefing.config import BriefingConfig
from stockman.briefing.demo import SyntheticSource
from stockman.briefing.pipeline import preview
from stockman.briefing.schedule import Scheduler
directory, phase, timestamp = sys.argv[1:]
store = Store(Path(directory) / 'db')
config = BriefingConfig(enabled=True)

def build(owner, now, day):
    if phase in ('build', 'exhausted'):
        budget = Budget(store, config)
        for _ in range(config.max_calls if phase == 'exhausted' else 1):
            budget.reserve(day, 100)
        os._exit(71)
    return preview(store, [SyntheticSource()], owner, day, now)

def send(result):
    with (Path(directory) / 'outbound').open('a') as handle:
        handle.write(result['report_id'] + chr(10))
    if phase == 'send':
        os._exit(71)
    return 'sent'

Scheduler(store, config, '123', {'123'}, build, send).tick(float(timestamp))
os._exit(71)
"""
        child = subprocess.run([sys.executable, '-B', '-c', program, self.temp.name, phase, str(timestamp)],
                               capture_output=True, text=True, timeout=15)
        self.assertEqual(child.returncode, 71, child.stderr)

    def test_process_interruption_before_send_retries_with_remaining_budget(self):
        now = at('2026-10-22T08:00:00')
        self.interrupt_child('build', now)
        self.scheduler.recover()
        waiting = self.scheduler.tick(now + 60)
        self.assertEqual(waiting['error_type'], 'generation_interrupted')
        self.assertEqual(waiting['attempts'], 1)
        self.assertEqual(waiting['next_try'], now + 900)
        self.assertFalse(self.sent)
        result = self.scheduler.tick(now + 900)
        self.assertEqual((result['status'], result['attempts']), ('sent', 2))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.store.execute('SELECT id FROM brief_budget')), 1)

    def test_exhausted_budget_after_generation_interrupt_stops_recovery(self):
        now = at('2026-10-22T08:00:00')
        self.interrupt_child('exhausted', now)
        self.scheduler.recover()
        result = self.scheduler.tick(now + 900)
        self.assertEqual((result['status'], result['error_type']), ('failed', 'budget_exhausted'))
        self.assertIsNone(result['next_try'])
        self.assertFalse(self.built)

    def test_process_interruption_during_and_after_send_never_replays(self):
        for phase, day, expected in [('send', '22', 'unknown'), ('sent', '23', 'sent')]:
            now = at(f'2026-10-{day}T08:00:00')
            with self.subTest(phase=phase):
                self.interrupt_child(phase, now)
                self.scheduler.recover()
                result = self.scheduler.tick(now + 900)
                self.assertEqual(result['status'], expected)
                report = self.store.execute('SELECT status FROM brief_reports WHERE id=%s', (result['report_id'],))[0]
                self.assertEqual(report['status'], expected)
        self.assertFalse(self.sent)
        self.assertEqual(len((Path(self.temp.name) / 'outbound').read_text().splitlines()), 2)

    def test_legacy_generation_is_unknown_and_terminal_report_reconciles(self):
        first = at('2026-10-22T08:00:00')
        self.store.execute("INSERT INTO brief_daily VALUES('123','2026-10-22',%s,%s,NULL,'generating',1,0,NULL)", (first, first))
        self.scheduler.recover()
        self.assertEqual(self.scheduler.tick(first + 900)['status'], 'unknown')
        second = at('2026-10-23T08:00:00')
        self.assertEqual(self.scheduler.tick(second)['status'], 'sent')
        self.store.execute("UPDATE brief_daily SET status='sending' WHERE day='2026-10-23'")
        self.scheduler.recover()
        self.assertEqual(self.scheduler.tick(second + 900)['status'], 'sent')
        self.assertEqual(len(self.sent), 1)

    def test_concurrent_tick_claims_exactly_one_generation(self):
        other_store = Store(Path(self.temp.name)/'db')
        try:
            other = Scheduler(other_store,self.config,'123',{'123'},self.build,self.scheduler.send)
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda scheduler:scheduler.tick(at('2026-10-22T08:00:00')),
                              [self.scheduler, other, self.scheduler, other]))
            self.assertEqual(len(self.sent),1);self.assertEqual(len(self.built),1)
        finally:
            other_store.close()
    def test_no_network_disabled_unauthorized_or_before_due(self):
        scheduler=Scheduler(self.store,BriefingConfig(),'123',{'123'},lambda *a:self.fail('build'),lambda *a:self.fail('send'))
        self.assertEqual(scheduler.tick(at('2026-10-22T12:00:00'))['status'],'disabled')
        for owner,allowed in [('124',{'123'}),('-123',{'-123'})]:
            with self.assertRaises(PermissionError):Scheduler(self.store,self.config,owner,allowed,self.build,lambda r:'sent')
    def test_generation_failure_retries_bounded_and_error_is_redacted(self):
        self.scheduler.build=lambda *a:(_ for _ in ()).throw(TimeoutError('SECRET'))
        self.assertEqual(self.scheduler.tick(at('2026-10-22T08:00:00'))['status'],'failed')
        self.assertEqual(self.scheduler.tick(at('2026-10-22T08:01:00'))['status'],'failed')
        self.assertEqual(self.store.execute('SELECT attempts FROM brief_daily')[0]['attempts'],1)
        self.scheduler.build=self.build
        self.assertEqual(self.scheduler.tick(at('2026-10-22T08:15:00'))['status'],'sent')
        self.assertNotIn('SECRET',str(self.store.execute('SELECT * FROM brief_daily')))
    def test_runtime_directory_lock_blocks_second_process_handle(self):
        first=DirectoryLock(self.temp.name)
        try:
            with self.assertRaises(RuntimeError):DirectoryLock(self.temp.name)
        finally:first.close()
        DirectoryLock(self.temp.name).close()

    def test_application_uses_broker_and_constructor_never_sends(self):
        import os
        from unittest.mock import patch,Mock
        from stockman.application import Application
        from stockman.agents import OfflineModel
        from stockman.portfolio import PortfolioService
        transport=Mock(chat=123)
        factory=lambda store:PortfolioService(store,reader=lambda account:{'AAPL':{'shares':1}})
        with patch.dict(os.environ,{'BRIEFING_ENABLED':'true','TG_ALLOWED_USERS':'123'}):
            app=Application(Path(self.temp.name)/'app',OfflineModel(),portfolio_factory=factory,transport=transport)
        try:
            transport.deliver.assert_not_called()
            app.runtime.store.snapshot('image','123',{'MSFT':{'shares':99}},asof=1000)
            app.briefing.generate=Mock(return_value={'text':'synthetic'})
            app.build_daily('123',1001,'day')
            snapshot=app.briefing.generate.call_args.args[1]
            self.assertEqual(snapshot['source'],'snaptrade')
            self.assertEqual(set(snapshot['positions']),{'AAPL'})
        finally:app.runtime.close()

    def test_configured_timezone_uses_its_own_calendar_day(self):
        from dataclasses import replace
        config=replace(self.config,timezone='America/New_York')
        scheduler=Scheduler(self.store,config,'123',{'123'},self.build,lambda r:self.sent.append(r) or 'sent')
        # Shanghai Oct 23 01:00 = New York Oct 22 afternoon.
        result=scheduler.tick(at('2026-10-23T01:00:00'))
        self.assertEqual(result['day'],'2026-10-22');self.assertTrue(result['delayed'])
