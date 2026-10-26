import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from lib.service_config import load_config,ConfigError
from lib.service_demo import fixture,ENV

class ServiceConfigTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.config=fixture(self.temp.name);self.path=self.config.path;self.original=self.path.read_text()
    def tearDown(self):self.temp.cleanup()
    def test_explicit_paths_and_secrets_never_appear_in_repr(self):
        with patch('socket.socket.connect',side_effect=AssertionError('network')):
            config=load_config(self.path,{**ENV,'BRIEFING_TIME':'22:00','IBKR_PORT':'9999'})
        self.assertEqual(config.directory,(Path(self.temp.name)/'runtime').resolve());self.assertEqual(config.briefing.at,'08:00');self.assertEqual(config.broker.port,4001)
        self.assertFalse(config.directory.exists())
        for value in ENV.values():
            if value!='123':self.assertNotIn(value,repr(config))
    def test_invalid_fields_and_values_are_rejected_without_echoing_values(self):
        for old,new in [('provider = ibkr','provider = unknown-secret'),('port = 4001','port = -1'),('daily_budget_usd = 0.20','daily_budget_usd = nan'),('time = 08:00','time = 99:99'),('interactive = true','interactive = maybe'),('client_id = 17','client_id = 17\nclient_id = 18')]:
            self.path.write_text(self.original.replace(old,new))
            with self.assertRaises(ConfigError) as caught:load_config(self.path,ENV)
            self.assertNotIn('unknown-secret',str(caught.exception))
        self.path.write_text(self.original+'\n[evil]\nkey=secret\n')
        with self.assertRaises(ConfigError):load_config(self.path,ENV)
    def test_missing_environment_unauthorized_owner_and_defaults_fail_early(self):
        for change in [{'GEMINI_API_KEY':''},{'TG_ALLOWED_USERS':'124'},{'TG_CHAT_ID':'-123'},{'IBKR_ACCOUNT':'another-secret'}]:
            with self.assertRaises(ConfigError):load_config(self.path,{**ENV,**change})
        self.path.write_text('[DEFAULT]\nx=secret\n'+self.original)
        with self.assertRaises(ConfigError):load_config(self.path,ENV)
    def test_cli_check_has_no_network_or_runtime_files(self):
        from service import main
        with patch.dict(os.environ,ENV),patch('builtins.print') as output,patch('socket.socket.connect',side_effect=AssertionError('network')):
            self.assertEqual(main(['--config',str(self.path),'--check']),0)
        self.assertFalse(self.config.directory.exists());self.assertIn('config_valid',output.call_args.args[0])

    def test_registry_json_is_checked_before_runtime(self):
        invalid=Path(self.temp.name)/'broken.json';invalid.write_text('private-invalid-json')
        self.path.write_text(self.original.replace(str(self.config.sources),str(invalid)))
        with self.assertRaises(ConfigError) as caught:load_config(self.path,ENV)
        self.assertNotIn('private-invalid-json',str(caught.exception))

    def test_explicit_service_import_ignores_invalid_legacy_environment(self):
        import subprocess,sys
        root=Path(__file__).resolve().parents[1]
        env=dict(os.environ,STOCKMAN_EXPLICIT_CONFIG='1',STOCKMAN_OFFLINE='1',PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(root),MONITOR_INTERVAL='invalid-legacy-value',YF_REQUEST_DELAY='nan')
        result=subprocess.run([sys.executable,'-B','-c','import lib.service; from lib import config; assert config.MONITOR_INTERVAL==5; assert config.YF_REQUEST_DELAY==5'],cwd=self.temp.name,env=env,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
