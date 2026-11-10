from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
import os
import tempfile
import time
import unittest
from unittest.mock import Mock,patch
from stockman.service_demo import fixture,build_synthetic,service_demo
from stockman.channels.telegram import decode


def daily_logs(records, event):
    prefix = event + " "
    return [json.loads(record.getMessage()[len(prefix):]) for record in records if record.getMessage().startswith(prefix)]

class ServiceRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();cfg=fixture(self.temp.name)
        self.config=replace(cfg,briefing=replace(cfg.briefing,at='00:00'))
    def tearDown(self):self.temp.cleanup()
    def test_service_log_distinguishes_clean_empty_from_stale_and_source_failure(self):
        from requests import HTTPError, Response
        from stockman.briefing.models import SourcePolicy
        now = datetime.fromisoformat('2026-11-10T08:00:00+08:00').timestamp()
        app = build_synthetic(self.config)
        existing = app.sources[0]
        sensitive = 'PRIVATE_TICKER_872'
        policy = SourcePolicy('sec:'+sensitive, 'https://example.com/policy', True, True, True)
        older = replace(existing.fetch(now)[0], source=policy.source, published=now-90000)
        source = Mock(policy=policy, kind='sec')
        source.fetch.return_value = [older]
        app.sources = [source]
        try:
            with self.assertLogs(app.logger, level='INFO') as logs:
                with patch('time.time', return_value=now):
                    self.assertEqual(app.tick(now)['status'], 'sent')
                    app.tick(now+1)
                response = Response()
                response.status_code = 429
                source.fetch.side_effect = HTTPError('PRIVATE_API_KEY https://example.com/secret', response=response)
                with patch('time.time', return_value=now+86400):
                    self.assertEqual(app.tick(now+86400)['status'], 'sent')
            sources = daily_logs(logs.records, 'daily_source')
            self.assertEqual([row['freshness'] for row in sources], ['stale','unknown'])
            self.assertEqual([row['last_success'] for row in sources], [now,now])
            self.assertEqual(sources[0]['source_ref'], sources[1]['source_ref'])
            self.assertEqual(sources[1]['reason'], 'rate_limited')
            runs = daily_logs(logs.records, 'daily_run')
            self.assertEqual([row['analysis'] for row in runs], ['no_new_evidence','insufficient_sources'])
            self.assertEqual([row['task_status'] for row in runs], ['completed','partial'])
            self.assertEqual(runs[0]['budget']['calls'], 0)
            for value in (sensitive, 'PRIVATE_API_KEY', 'https://example.com/secret',
                          self.config.broker.account, self.config.channel.token, 'AAPL'):
                self.assertNotIn(value, str(logs.output))
        finally:
            app.close()

    def test_terminal_failure_alert_logs_rejection_and_never_replays_after_restart(self):
        now = datetime.fromisoformat('2026-11-10T08:00:00+08:00').timestamp()
        config = replace(self.config, briefing=replace(self.config.briefing, alerts_enabled=True))
        app = build_synthetic(config)
        channel = app.transport
        channel.outcome = 'failed'
        app.daily.build = Mock(side_effect=TimeoutError('SECRET_BROKER_ERROR'))
        try:
            with self.assertLogs(app.logger, level='INFO') as logs:
                with patch('time.time', return_value=now):
                    for offset in (0, 30, 900, 1200):
                        self.assertEqual(app.tick(now+offset)['status'], 'failed')
                    self.assertEqual(channel.sent, [])
                    self.assertEqual(app.tick(now+1800)['status'], 'failed')
                    app.tick(now+1900)
            self.assertEqual(len(channel.sent), 1)
            self.assertIn('请查看 StockMan 服务日志', channel.sent[0]['text'])
            self.assertEqual([row['status'] for row in daily_logs(logs.records, 'daily_run')],
                             ['retry_pending','retry_pending','failed'])
            alerts = daily_logs(logs.records, 'daily_alert')
            self.assertEqual([(row['kind'],row['reason']) for row in alerts], [('daily_failed','alert_rejected')])
            self.assertNotIn('SECRET_BROKER_ERROR', str(logs.output))
            app.close()
            app = build_synthetic(config, channel)
            self.assertEqual(app.tick_daily(now+2000)['status'], 'failed')
            self.assertEqual(len(channel.sent), 1)
        finally:
            app.close()

    def test_unknown_primary_delivery_alerts_once_and_never_resends_either_message(self):
        now = datetime.fromisoformat('2026-11-10T08:00:00+08:00').timestamp()
        config = replace(self.config, briefing=replace(self.config.briefing, alerts_enabled=True))
        app = build_synthetic(config)
        channel = app.transport
        channel.outcome = 'unknown'
        try:
            with self.assertLogs(app.logger, level='INFO') as logs:
                with patch('time.time', return_value=now):
                    self.assertEqual(app.tick(now)['status'], 'unknown')
                    app.tick(now+30)
            self.assertEqual(len(channel.sent), 2)
            self.assertIn('是否已有当日原日报', channel.sent[1]['text'])
            self.assertEqual(daily_logs(logs.records, 'daily_alert')[0]['status'], 'unknown')
            app.close()
            app = build_synthetic(config, channel)
            self.assertEqual(app.tick_daily(now+60)['status'], 'unknown')
            self.assertEqual(len(channel.sent), 2)
        finally:
            app.close()

    def test_restart_retains_ambiguous_legacy_deliveries_and_alert_claim(self):
        now = datetime.fromisoformat('2026-11-10T08:00:00+08:00').timestamp()
        config = replace(self.config, briefing=replace(self.config.briefing, alerts_enabled=True))
        app = build_synthetic(config)
        channel = app.transport
        channel.outcome = 'unknown'
        store = app.runtime.store
        store.execute("INSERT INTO updates VALUES(900,'processing')")
        store.execute("INSERT INTO updates VALUES(901,'handling')")
        store.execute("INSERT INTO events VALUES('legacy','AAPL','2026-11-10','pending','{}',?)", (now,))
        store.execute("INSERT INTO events VALUES('current','AAPL','2026-11-10','analyzing','{}',?)", (now,))

        def abort_during_alert():
            if len(channel.sent) == 2:
                raise SystemExit(71)

        channel.after_send = abort_during_alert
        try:
            with patch('time.time', return_value=now):
                with self.assertRaises(SystemExit):
                    app.tick(now)
            self.assertEqual(store.execute('SELECT status FROM brief_alerts')[0]['status'], 'sending')
            app.close()
            channel.after_send = None
            with self.assertLogs('stockman.service', level='INFO') as logs:
                app = build_synthetic(config, channel)
                app.tick_daily(now+60)
            self.assertEqual(app.runtime.store.execute('SELECT status FROM brief_alerts')[0]['status'], 'unknown')
            self.assertEqual(daily_logs(logs.records, 'daily_alert')[0]['reason'], 'interrupted')
            self.assertEqual(len(channel.sent), 2)
            self.assertEqual([row['status'] for row in app.runtime.store.execute('SELECT status FROM updates ORDER BY id')],
                             ['unknown','interrupted'])
            self.assertEqual([row['status'] for row in app.runtime.store.execute('SELECT status FROM events ORDER BY id')],
                             ['interrupted','unknown'])
        finally:
            app.close()

    def test_automatic_report_and_on_demand_text_are_separate(self):
        with patch('socket.socket.connect',side_effect=AssertionError('network')):result=service_demo('service')
        self.assertEqual(result['automatic_reports_without_input'],1);self.assertEqual(result['on_demand_responses'],1)
    def test_config_overrides_legacy_environment_limits(self):
        with patch.dict(os.environ,{'STOCKMAN_MAX_MODEL_CALLS':'1','BRIEFING_ENABLED':'false','SNAPTRADE_ACCOUNT_ID':'22222222-2222-4222-8222-222222222222'}):app=build_synthetic(self.config)
        try:
            result=app.handle('检查持仓','123')
            self.assertGreater(result['model_calls'],1);self.assertEqual(app.broker.adapter.account,self.config.broker.account)
            self.assertTrue(app.briefing_config.enabled)
        finally:app.close()
    def test_broker_failure_is_visible_and_recovery_does_not_use_empty_positions(self):
        app=build_synthetic(self.config);app.broker.adapter.fail=True;now=time.time()
        try:
            result=app.tick(now);self.assertEqual(result['status'],'failed');self.assertFalse(app.transport.sent)
            self.assertEqual(app.broker.health()['status'],'failed')
            self.assertNotIn('synthetic-secret',str(app.runtime.store.execute('SELECT * FROM service_health')))
            app.broker.adapter.fail=False
            self.assertEqual(app.tick(now+901)['status'],'sent')
        finally:app.close()
    def test_real_worker_lifecycle_runs_without_im_input_and_closes(self):
        config=replace(self.config,channel=replace(self.config.channel,interactive=False))
        app=build_synthetic(config);app.transport.after_send=app.stop_event.set
        try:
            with patch('socket.socket.connect',side_effect=AssertionError('network')):app.run()
            self.assertEqual(len(app.transport.sent),1);self.assertTrue(app.closed)
            self.assertFalse(any(w.is_alive() for w in app.workers))
        finally:app.close()
    def test_incoming_error_does_not_prevent_background_tick_or_change_owner(self):
        app=build_synthetic(self.config)
        try:
            app.transport.fail_receive=True
            with self.assertRaises(TimeoutError):app.receive_once(0)
            self.assertEqual(app.tick()['status'],'sent')
            event=decode({'update_id':1,'message':{'from':{'id':124},'chat':{'id':123},'text':'检查持仓'}})
            self.assertEqual(app.handle_event(event),'unauthorized');self.assertEqual(len(app.transport.sent),1)
        finally:app.close()

    def test_read_only_status_does_not_create_or_lock_runtime(self):
        from stockman.service import read_status
        self.assertEqual(read_status(self.config)['status'],'not_started');self.assertFalse(self.config.directory.exists())
        app=build_synthetic(self.config)
        try:
            app.tick()
            status=read_status(self.config)
            self.assertEqual(status['broker'][0]['status'],'ok')
            self.assertNotIn(self.config.broker.account,str(status));self.assertNotIn(self.config.model.key,str(status))
        finally:app.close()
    def test_concurrent_automatic_report_ignores_photo_update(self):
        import json
        from concurrent.futures import ThreadPoolExecutor
        app=build_synthetic(self.config)
        event=decode({'update_id':2,'message':{'from':{'id':123},'chat':{'id':123},'photo':[{'file_id':'synthetic'}]}})
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                auto=pool.submit(app.tick);image=pool.submit(app.handle_event,event)
                self.assertEqual(auto.result()['status'],'sent');self.assertEqual(image.result(),'ignored')
            report=app.runtime.store.execute('SELECT meta FROM brief_reports')[0]
            self.assertEqual(json.loads(report['meta'])['holdings']['symbols'],['AAPL'])
            self.assertEqual(set(app.broker.load()['positions']),{'AAPL'})
        finally:app.close()
    def test_wrong_channel_cannot_replay_ids_or_invoke_model(self):
        app=build_synthetic(self.config)
        try:
            event=decode({'update_id':1,'message':{'from':{'id':123},'chat':{'id':123},'text':'检查持仓'}})
            self.assertEqual(app.handle_event(replace(event,channel='other')),'unauthorized')
            self.assertFalse(app.runtime.store.execute('SELECT * FROM tasks'))
        finally:app.close()
    def test_complete_demo_keeps_unknown_without_replay(self):
        result=service_demo('end-to-end')
        self.assertEqual(result['unconfirmed_delivery'],'retained_without_replay')

    def test_worker_keeps_running_when_real_receive_loop_fails(self):
        import threading
        from stockman.service import read_status
        app=build_synthetic(self.config);received=threading.Event()
        def receive(offset):received.set();raise TimeoutError('private-network-detail')
        app.transport.receive=receive
        def after_send():
            if not received.wait(2):raise AssertionError('incoming loop was not exercised')
            app.stop_event.set()
        app.transport.after_send=after_send
        try:
            app.run()
            status=read_status(self.config)
            incoming=next(row for row in status['jobs'] if row['job']=='incoming')
            self.assertEqual(incoming['status'],'failed');self.assertEqual(len(app.transport.sent),1)
            self.assertNotIn('private-network-detail',str(status));self.assertTrue(app.closed)
        finally:app.close()
    def test_photo_rejection_does_not_affect_following_text_request(self):
        app=build_synthetic(self.config)
        try:
            media=decode({'update_id':1,'message':{'from':{'id':123},'chat':{'id':123},'photo':[{'file_id':'synthetic'}]}})
            text=decode({'update_id':2,'message':{'from':{'id':123},'chat':{'id':123},'text':'检查持仓'}})
            with patch('google.genai.Client') as model:
                self.assertEqual(app.handle_event(media),'ignored');model.assert_not_called()
            self.assertFalse(app.runtime.store.execute('SELECT * FROM tasks'))
            self.assertEqual(app.handle_event(text),'sent')
            self.assertIn('AAPL',app.transport.sent[-1]['text'])
            self.assertNotIn('NVDA',app.transport.sent[-1]['text'])
        finally:app.close()

    def test_concurrent_text_and_automatic_report_share_broker_scope(self):
        import json
        from concurrent.futures import ThreadPoolExecutor
        app=build_synthetic(self.config)
        event=decode({'update_id':3,'message':{'from':{'id':123},'chat':{'id':123},'text':'检查持仓'}})
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                auto=pool.submit(app.tick);text=pool.submit(app.handle_event,event)
                self.assertEqual(auto.result()['status'],'sent');self.assertEqual(text.result(),'sent')
            report=app.runtime.store.execute('SELECT meta FROM brief_reports')[0]
            self.assertEqual(json.loads(report['meta'])['holdings']['symbols'],['AAPL'])
            self.assertEqual(len(app.transport.sent),2)
        finally:app.close()
