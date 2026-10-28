from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
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
    def test_concurrent_tick_claims_exactly_one_generation(self):
        with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(lambda _:self.scheduler.tick(at('2026-10-22T08:00:00')),range(4)))
        self.assertEqual(len(self.sent),1);self.assertEqual(len(self.built),1)
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
        factory=lambda store:PortfolioService(store,ibkr=lambda account:{'AAPL':{'shares':1}})
        with patch.dict(os.environ,{'BRIEFING_ENABLED':'true','TG_ALLOWED_USERS':'123'}):
            app=Application(Path(self.temp.name)/'app',OfflineModel(),portfolio_factory=factory,transport=transport)
        try:
            transport.deliver.assert_not_called()
            app.runtime.store.snapshot('image','123',{'MSFT':{'shares':99}},asof=1000)
            app.briefing.generate=Mock(return_value={'text':'synthetic'})
            app.build_daily('123',1001,'day')
            snapshot=app.briefing.generate.call_args.args[1]
            self.assertEqual(snapshot['source'],'ibkr')
            self.assertEqual(set(snapshot['positions']),{'AAPL'})
        finally:app.runtime.close()

    def test_configured_timezone_uses_its_own_calendar_day(self):
        from dataclasses import replace
        config=replace(self.config,timezone='America/New_York')
        scheduler=Scheduler(self.store,config,'123',{'123'},self.build,lambda r:self.sent.append(r) or 'sent')
        # Shanghai Oct 23 01:00 = New York Oct 22 afternoon.
        result=scheduler.tick(at('2026-10-23T01:00:00'))
        self.assertEqual(result['day'],'2026-10-22');self.assertTrue(result['delayed'])
