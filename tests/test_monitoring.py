import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from stockman.storage import Store
from stockman.monitoring import MonitoringService
from stockman.agents import CURRENT,Invocation
from stockman.telegram import message_chunks,Telegram
from stockman.application import Application
from stockman.agents import OfflineModel

class MonitorTest(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.store=Store(Path(self.directory.name)/'db.sqlite')
        self.service=MonitoringService(self.store,None,in_session=lambda now:True)
        self.store.execute('INSERT INTO rules(symbol,spec) VALUES(?,?)',('AAPL',json.dumps({'support':100,'resistance':120,'buffer':0.02})))
    def tearDown(self):self.store.close();self.directory.cleanup()
    def quote(self,price,stamp=1000):return {'price':price,'asof':stamp,'kind':'regular','valid':True}
    def test_breakout_then_same_state_does_not_repeat_after_restart(self):
        self.assertIsNotNone(self.service.check('AAPL',self.quote(125),1001))
        self.assertIsNone(self.service.check('AAPL',self.quote(126,1002),1003))
        self.store.close();self.store=Store(Path(self.directory.name)/'db.sqlite')
        self.service=MonitoringService(self.store,None,in_session=lambda now:True)
        self.assertIsNone(self.service.check('AAPL',self.quote(127,1004),1005))
    def test_stale_out_of_order_and_missing_times(self):
        self.assertIsNone(self.service.check('AAPL',{'price':125,'kind':'regular','valid':True},1001))
        self.assertIsNone(self.service.check('AAPL',self.quote(125),3000))
        self.service.check('AAPL',self.quote(110),1001)
        self.assertIsNone(self.service.check('AAPL',self.quote(125,999),1002))
    def test_off_session_does_not_trigger(self):
        self.service.in_session=lambda now:False
        self.assertIsNone(self.service.check('AAPL',self.quote(125),1001))
    def test_daily_cap_is_persistent(self):
        with patch.dict('os.environ',{'MONITOR_MAX_DAILY_ALERTS':'1'}):
            self.assertIsNotNone(self.service.check('AAPL',self.quote(125),1001))
            self.service.check('AAPL',self.quote(110,1002),1003)
            self.assertIsNone(self.service.check('AAPL',self.quote(125,1004),1005))
    def test_delegate_cannot_invent_monitor_write_permission(self):
        token=CURRENT.set(Invocation('task','owner',request='研究 AAPL'))
        try:
            result=json.loads(self.service.command('停止监控 AAPL'))
            self.assertEqual(result['status'],'read_only_status')
            self.assertEqual(self.store.execute('SELECT enabled FROM rules')[0]['enabled'],1)
        finally:CURRENT.reset(token)
    def test_delegate_cannot_change_user_authorized_target(self):
        self.store.execute('INSERT INTO rules(symbol,spec) VALUES(?,?)',('NVDA',json.dumps({'support':100,'resistance':120,'buffer':0.02})))
        token=CURRENT.set(Invocation('task','owner',request='停止监控 AAPL'))
        try:self.service.command('停止监控 NVDA')
        finally:CURRENT.reset(token)
        enabled={r['symbol']:r['enabled'] for r in self.store.execute('SELECT symbol,enabled FROM rules')}
        self.assertEqual(enabled,{'AAPL':0,'NVDA':1})

    def test_boolean_quote_does_not_trigger(self):
        self.assertIsNone(self.service.check('AAPL',self.quote(125,True),2))
        self.assertIsNone(self.service.check('AAPL',self.quote(True),1001))

    def test_explicit_stop_works(self):
        token=CURRENT.set(Invocation('task','owner',request='停止监控 AAPL'))
        try:self.assertEqual(json.loads(self.service.command('停止监控 AAPL'))['status'],'stopped')
        finally:CURRENT.reset(token)
        self.assertIsNone(self.service.check('AAPL',self.quote(125),1001))
    def test_no_model_calls_without_event_and_unknown_delivery_is_recorded(self):
        self.service.market=Mock();self.service.market.quote.return_value=self.quote(110)
        runtime=Mock();send=Mock(return_value='unknown')
        self.assertEqual(self.service.tick(runtime,send,now=1001),[]);runtime.run.assert_not_called()
        self.service.market.quote.return_value=self.quote(125,1002)
        self.service.tick(runtime,send,now=1003)
        self.assertEqual(self.store.execute('SELECT status FROM events')[0]['status'],'unknown')

class TelegramTest(unittest.TestCase):
    def test_chunks_escape_each_piece_and_preserve_visible_text(self):
        import html
        text='😀<&>'*2000
        chunks=message_chunks(text)
        self.assertEqual(''.join(html.unescape(c) for c in chunks),text)
        self.assertTrue(all(len(html.unescape(c).encode('utf-16-le'))//2<=4096 for c in chunks))
    def test_unauthorized_and_duplicate_updates_never_invoke_agent(self):
        with tempfile.TemporaryDirectory() as d:
            transport=Mock();transport.chat=123
            app=Application(d,OfflineModel(),transport=transport)
            app.allowed={123}
            with patch.object(app.runtime,'run') as invoke:
                update={'update_id':1,'message':{'from':{'id':999},'chat':{'id':123},'text':'研究 AAPL'}}
                self.assertEqual(app.handle_update(update),'unauthorized')
                self.assertEqual(app.handle_update(update),'duplicate');invoke.assert_not_called()
            app.runtime.close()

    def test_real_transport_preserves_unknown_delivery_state(self):
        import requests
        with patch('stockman.telegram.requests.post',side_effect=requests.Timeout()):
            transport=Telegram(token='synthetic',chat='123')
            self.assertEqual(transport.deliver({'text':'synthetic'}),'unknown')

    def test_authorized_text_runs_once_and_duplicate_is_not_replayed(self):
        with tempfile.TemporaryDirectory() as d:
            transport=Mock();transport.chat=123;transport.deliver.return_value='sent'
            app=Application(d,OfflineModel(),transport=transport);app.allowed={123}
            update={'update_id':2,'message':{'from':{'id':123},'chat':{'id':123},'text':'研究 AAPL'}}
            try:
                with patch.object(app.runtime,'run',return_value={'text':'AAPL evidence'}) as run:
                    self.assertEqual(app.handle_update(update),'sent')
                    self.assertEqual(app.handle_update(update),'duplicate')
                    run.assert_called_once_with('研究 AAPL','123')
                transport.deliver.assert_called_once();transport.photo.assert_not_called()
            finally:app.runtime.close()
