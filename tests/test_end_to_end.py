import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch,Mock
import requests
from lib.offline import end_to_end,create_offline_app
from lib.telegram import Telegram
from lib.research import SECClient


class EndToEndTest(unittest.TestCase):
    def test_full_workflow_cannot_connect_to_network(self):
        with tempfile.TemporaryDirectory() as d,patch.object(socket.socket,'connect',side_effect=AssertionError('offline network attempt')):
            result=end_to_end(d)
            self.assertTrue(result['synthetic']);self.assertEqual(result['deliveries'],1)
            self.assertTrue((Path(d)/'stockman.sqlite').is_file())

    def test_photos_and_image_documents_never_download_invoke_or_write(self):
        for media in ({'photo':[{'file_id':'synthetic'}]}, {'document':{'file_id':'synthetic','mime_type':'image/png'}}):
            with self.subTest(media=media),tempfile.TemporaryDirectory() as d:
                transport=Mock();transport.chat=123
                app=create_offline_app(d,transport);app.allowed={123}
                update={'update_id':2,'message':{'from':{'id':123},'chat':{'id':123},'caption':'检查持仓',**media}}
                before={p.relative_to(d) for p in Path(d).rglob('*')}
                try:
                    with patch.object(app.runtime,'run') as run,patch('lib.telegram.requests.get') as get,patch('google.genai.Client') as model:
                        self.assertEqual(app.handle_update(update),'ignored')
                        self.assertEqual(app.handle_update(update),'duplicate')
                        run.assert_not_called();get.assert_not_called();model.assert_not_called()
                    transport.assert_not_called();transport.deliver.assert_not_called();transport.photo.assert_not_called()
                    self.assertEqual(app.runtime.store.execute('SELECT status FROM updates')[0]['status'],'ignored')
                    self.assertEqual(app.runtime.store.execute('SELECT * FROM tasks'),[])
                    self.assertEqual(app.runtime.store.execute('SELECT * FROM snapshots'),[])
                    self.assertEqual({p.relative_to(d) for p in Path(d).rglob('*')},before)
                finally:app.runtime.close()

    def test_text_task_model_budget_still_applies(self):
        from lib.agents import CallLimit
        with tempfile.TemporaryDirectory() as d,patch.dict('os.environ',{'STOCKMAN_MAX_MODEL_CALLS':'1'}):
            app=create_offline_app(d)
            try:
                with self.assertRaises(CallLimit):app.handle('研究 AAPL')
                task=app.runtime.store.execute('SELECT * FROM tasks')[0]
                self.assertEqual(task['status'],'failed')
            finally:app.runtime.close()

    def test_start_monitoring_all_broker_holdings_without_an_explicit_symbol(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            app=create_offline_app(d)
            app.runtime.store.snapshot('image','owner',{'NVDA':{'shares':99}})
            try:
                result=app.handle('开始监控持仓','owner')
                self.assertEqual(result['status'],'completed',result)
                rules=app.runtime.store.execute('SELECT symbol,spec FROM rules')
                self.assertEqual([r['symbol'] for r in rules],['AAPL'])
                self.assertEqual(json.loads(rules[0]['spec'])['source'],'ibkr')
            finally:app.runtime.close()

    def test_unsupported_update_is_not_left_processing(self):
        with tempfile.TemporaryDirectory() as d:
            transport=Mock();transport.chat=123
            app=create_offline_app(d,transport);app.allowed={123}
            try:
                status=app.handle_update({'update_id':10,'message':{'from':{'id':123},'chat':{'id':123},'sticker':{}}})
                self.assertEqual(status,'ignored')
                self.assertEqual(app.runtime.store.execute('SELECT status FROM updates')[0]['status'],'ignored')
                transport.deliver.assert_not_called()
            finally:app.runtime.close()

    def test_partial_transport_delivery_is_unknown(self):
        responses=[Mock(status_code=200,json=lambda:{'ok':True}),Mock(status_code=500)]
        with patch('lib.telegram.requests.post',side_effect=responses):
            self.assertEqual(Telegram('synthetic','123').deliver({'text':'x'*2000}),'unknown')
        with patch('lib.telegram.requests.post',return_value=Mock(status_code=200,json=lambda:{'ok':False})):
            self.assertEqual(Telegram('synthetic','123').deliver({'text':'x'}),'failed')

    def test_transient_sec_errors_retry_at_most_twice(self):
        response=Mock(status_code=200);response.json.return_value={'ok':True}
        with patch.dict('os.environ',{'SEC_USER_AGENT':'Synthetic contact@example.com'}),patch('lib.research.time.sleep'),patch('lib.research.requests.get',side_effect=[requests.Timeout(),requests.ConnectionError(),response]) as get:
            self.assertEqual(SECClient().get('https://www.sec.gov/example'),{'ok':True})
            self.assertEqual(get.call_count,3)
        with patch.dict('os.environ',{'SEC_USER_AGENT':'Synthetic contact@example.com'}),patch('lib.research.time.sleep'),patch('lib.research.requests.get',side_effect=requests.Timeout()) as get:
            with self.assertRaises(requests.Timeout):SECClient().get('https://www.sec.gov/example')
            self.assertEqual(get.call_count,3)

    def test_non_retryable_sec_error_fails_once(self):
        response=Mock(status_code=403);response.raise_for_status.side_effect=requests.HTTPError()
        with patch.dict('os.environ',{'SEC_USER_AGENT':'Synthetic contact@example.com'}),patch('lib.research.time.sleep'),patch('lib.research.requests.get',return_value=response) as get:
            with self.assertRaises(requests.HTTPError):SECClient().get('https://www.sec.gov/example')
            self.assertEqual(get.call_count,1)
