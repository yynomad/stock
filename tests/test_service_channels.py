import logging
from pathlib import Path
import requests
import tempfile
import unittest
from unittest.mock import Mock,patch
from dataclasses import replace
from stockman.channels import create_channel
from stockman.channels.telegram import decode
from stockman.service_demo import fixture

class ServiceChannelsTest(unittest.TestCase):
    def test_normalized_text_unsupported_media_and_invalid_events(self):
        event=decode({'update_id':1,'message':{'from':{'id':123},'chat':{'id':123},'text':'  研究 AAPL  '}})
        self.assertEqual(event.text,'研究 AAPL');self.assertEqual(event.channel,'telegram')
        event=decode({'update_id':2,'message':{'photo':[{'file_id':'small'},{'file_id':'large'}]}})
        self.assertEqual(event.text,'');self.assertFalse(hasattr(event,'image_id'))
        for identity in [True,-1,'1',None]:
            with self.assertRaises(ValueError):decode({'update_id':identity})
    def test_configured_channel_uses_only_explicit_destination_and_capabilities(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=fixture(d);channel=create_channel(cfg.channel)
        self.assertEqual(channel.chat,123);self.assertIsNone(channel.proxies)
        self.assertEqual(channel.capabilities,frozenset({'send','text'}))
        with self.assertRaises(ValueError):create_channel(replace(cfg.channel,provider='unsupported'))
    def test_transport_redacts_token_and_distinguishes_rejection_from_unknown(self):
        from stockman.telegram import TelegramReceiveError
        with tempfile.TemporaryDirectory() as directory:
            channel = create_channel(fixture(directory).channel)
        sensitive_url = channel.base + '/getUpdates'
        with patch('stockman.telegram.requests.get', side_effect=requests.Timeout(sensitive_url)):
            with self.assertRaises(TelegramReceiveError) as error:
                channel.updates(0)
        self.assertNotIn(sensitive_url, str(error.exception))
        logger = logging.getLogger('urllib3.connectionpool')
        with self.assertLogs(logger, level='DEBUG') as logs:
            logger.debug('GET %s', sensitive_url)
            logger.debug('ordinary diagnostic')
        self.assertEqual(len(logs.output), 1)
        self.assertNotIn(sensitive_url, str(logs.output))
        for status, expected in [(403, 'failed'), (429, 'failed'), (408, 'unknown'), (500, 'unknown')]:
            with self.subTest(status=status), patch('stockman.telegram.requests.post', return_value=Mock(status_code=status)):
                self.assertEqual(channel.deliver({'text':'synthetic'}), expected)

    def test_transport_keeps_escape_partial_and_unknown_outcomes(self):
        with tempfile.TemporaryDirectory() as d:channel=create_channel(fixture(d).channel)
        responses=[Mock(status_code=200,json=lambda:{'ok':True}),Mock(status_code=500)]
        with patch('stockman.telegram.requests.post',side_effect=responses) as post:
            self.assertEqual(channel.deliver({'text':'<script>'*250}),'unknown')
            self.assertEqual(post.call_args_list[0].kwargs['json']['chat_id'],123)
            self.assertIn('&lt;script&gt;',post.call_args_list[0].kwargs['json']['text'])
