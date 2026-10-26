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
