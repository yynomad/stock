from argparse import Namespace
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from lib.storage import Store
from lib.briefing.config import BriefingConfig
from lib.stock_briefing.cli import execute,status
from lib.stock_briefing.demo import end_to_end

class BriefingCLITest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'stockman.sqlite')
        self.args=Namespace(send=False,session='123',snapshot=None,public_symbols=['AAPL'],data_dir=self.temp.name)
    def tearDown(self):self.store.close();self.temp.cleanup()
    def test_public_preview_never_uses_broker_or_telegram_and_source_is_explicit(self):
        service=Mock();service.generate.return_value={'text':'preview'}
        portfolio=Mock();transport=Mock(chat=123)
        result=execute(self.args,self.store,BriefingConfig(),service,portfolio,transport,now=1000)
        self.assertEqual(result['text'],'preview');portfolio.load.assert_not_called();transport.deliver.assert_not_called()
        snapshot=service.generate.call_args.args[1]
        self.assertIn('not actual holdings',snapshot['source']);self.assertIsNone(snapshot['positions']['AAPL']['shares'])
    def test_unauthorized_send_fails_before_sources_models_or_transport(self):
        service=Mock();portfolio=Mock();transport=Mock(chat=123)
        self.args.send=True
        with patch.dict(os.environ,{'TG_CHAT_ID':'124','TG_ALLOWED_USERS':'124'}):
            with self.assertRaises(PermissionError):execute(self.args,self.store,BriefingConfig(),service,portfolio,transport,1000)
        service.generate.assert_not_called();portfolio.load.assert_not_called();transport.deliver.assert_not_called()
    def test_read_only_status_does_not_create_missing_database(self):
        missing=Path(self.temp.name)/'missing'
        self.assertEqual(status(missing)['reports'],[]);self.assertFalse(missing.exists())
    def test_stock_end_to_end_uses_no_network(self):
        with tempfile.TemporaryDirectory() as d,patch('socket.socket.connect',side_effect=AssertionError('network')):
            result=end_to_end(d)
        self.assertTrue(result['synthetic']);self.assertEqual(result['deliveries'],2)
        self.assertEqual(result['unconfirmed_delivery'],'unknown');self.assertEqual(result['relationship_hops'],1)
    def test_shared_modules_do_not_import_stock_domain(self):
        import ast
        base=Path(__file__).resolve().parents[1]/'lib'/'briefing'
        for path in base.glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node,ast.ImportFrom):self.assertNotIn((node.module or '').split('.')[0],('stock_briefing','portfolio','ibkr','research'))
