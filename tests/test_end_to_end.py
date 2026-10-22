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

    def test_failed_image_is_a_failed_traceable_task(self):
        with tempfile.TemporaryDirectory() as d:
            app=create_offline_app(d)
            app.portfolio.image=lambda path:None
            try:
                with self.assertRaises(ValueError):app.handle('检查截图持仓',image_path='synthetic.png')
                task=app.runtime.store.execute('SELECT * FROM tasks')[0]
                self.assertEqual(task['status'],'failed')
                self.assertTrue(any(t['tool']=='image_ingest' and t['phase']=='failed' for t in app.runtime.store.traces(task['id'])))
            finally:app.runtime.close()

    def test_image_generation_counts_towards_shared_model_budget(self):
        from lib.agents import CURRENT,Invocation,CallLimit
        from lib.positions import fetch_positions_from_image
        client=Mock();client.__enter__=Mock(return_value=client);client.__exit__=Mock(return_value=False)
        client.models.generate_content.return_value.text='{"positions":[{"symbol":"AAPL","shares":0.5,"avg_cost":null}]}'
        with tempfile.TemporaryDirectory() as d,patch('lib.positions.GEMINI_API_KEY','synthetic'),patch('google.genai.Client',return_value=client),patch.dict('os.environ',{'STOCKMAN_MAX_MODEL_CALLS':'1'}):
            path=Path(d)/'synthetic.png';path.write_bytes(b'synthetic')
            invocation=Invocation('task','local');token=CURRENT.set(invocation)
            try:
                self.assertEqual(fetch_positions_from_image(str(path))['AAPL']['shares'],0.5)
                self.assertEqual(invocation.models,1)
                with self.assertRaises(CallLimit):fetch_positions_from_image(str(path))
                client.models.generate_content.assert_called_once()
            finally:CURRENT.reset(token)

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
