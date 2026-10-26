from dataclasses import replace
from pathlib import Path
import os
import tempfile
import time
import unittest
from unittest.mock import patch
from lib.service_demo import fixture,build_synthetic,service_demo
from lib.channels.telegram import decode

class ServiceRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();cfg=fixture(self.temp.name)
        self.config=replace(cfg,briefing=replace(cfg.briefing,at='00:00'))
    def tearDown(self):self.temp.cleanup()
    def test_automatic_report_and_on_demand_image_are_separate(self):
        with patch('socket.socket.connect',side_effect=AssertionError('network')):result=service_demo('service')
        self.assertEqual(result['automatic_reports_without_input'],1);self.assertEqual(result['on_demand_responses'],2)
    def test_config_overrides_legacy_environment_limits(self):
        with patch.dict(os.environ,{'STOCKMAN_MAX_MODEL_CALLS':'1','BRIEFING_ENABLED':'false','IBKR_ACCOUNT':'U9999999'}):app=build_synthetic(self.config)
        try:
            result=app.handle('检查持仓','123')
            self.assertGreater(result['model_calls'],1);self.assertEqual(app.broker.adapter.account,self.config.broker.account)
            self.assertTrue(app.briefing_config.enabled)
        finally:app.close()
    def test_broker_failure_is_visible_and_recovery_does_not_use_empty_or_image(self):
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
        from lib.service import read_status
        self.assertEqual(read_status(self.config)['status'],'not_started');self.assertFalse(self.config.directory.exists())
        app=build_synthetic(self.config)
        try:
            app.tick()
            status=read_status(self.config)
            self.assertEqual(status['broker'][0]['status'],'ok')
            self.assertNotIn(self.config.broker.account,str(status));self.assertNotIn(self.config.model.key,str(status))
        finally:app.close()
    def test_concurrent_automatic_and_image_tasks_do_not_mix_snapshots(self):
        import json
        from concurrent.futures import ThreadPoolExecutor
        app=build_synthetic(self.config)
        event=decode({'update_id':2,'message':{'from':{'id':123},'chat':{'id':123},'photo':[{'file_id':'synthetic'}]}})
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                auto=pool.submit(app.tick);image=pool.submit(app.handle_event,event)
                self.assertEqual(auto.result()['status'],'sent');self.assertEqual(image.result(),'sent')
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
        from lib.service import read_status
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
    def test_image_sdk_receives_explicit_config_credentials_and_model(self):
        from unittest.mock import MagicMock
        from lib.service import BackendService
        from lib.service_demo import FakeBroker,FakeChannel
        from lib.agents import OfflineModel
        from lib.offline import SyntheticMarket,SyntheticSEC,SyntheticNews
        from lib.research import ResearchService
        market=SyntheticMarket();client=MagicMock()
        client.models.generate_content.return_value.text='{"positions":[{"symbol":"NVDA","shares":2,"avg_cost":null}]}'
        app=BackendService(self.config,OfflineModel(),broker=FakeBroker(self.config.broker.account),channel=FakeChannel(),market=market,research=ResearchService(market,SyntheticSEC(),SyntheticNews()),sources=[])
        try:
            event=decode({'update_id':1,'message':{'from':{'id':123},'chat':{'id':123},'photo':[{'file_id':'synthetic'}]}})
            with patch('lib.positions.GEMINI_API_KEY','legacy-key'),patch('lib.positions.GEMINI_MODEL','legacy-model'),patch('google.genai.Client',return_value=client) as create:
                self.assertEqual(app.handle_event(event),'sent')
            self.assertEqual(create.call_args.kwargs['api_key'],self.config.model.key)
            self.assertEqual(client.models.generate_content.call_args.kwargs['model'],self.config.model.name)
            self.assertEqual(client.models.generate_content.call_count,1)
        finally:app.close()
