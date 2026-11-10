import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from stockman.service_config import load_config,ConfigError
from stockman.service_demo import fixture,ENV

class ServiceConfigTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.config=fixture(self.temp.name);self.path=self.config.path;self.original=self.path.read_text()
    def tearDown(self):self.temp.cleanup()

    def test_daily_alerts_are_disabled_for_legacy_config_and_require_opt_in(self):
        self.assertFalse(self.config.briefing.alerts_enabled)
        prefix = self.original.replace('alerts_enabled = false\n', '')
        self.path.write_text(prefix)
        self.assertFalse(load_config(self.path, ENV).briefing.alerts_enabled)
        self.path.write_text(prefix.replace('[briefing]', '[briefing]\nalerts_enabled = true'))
        self.assertTrue(load_config(self.path, ENV).briefing.alerts_enabled)
        self.path.write_text(prefix.replace('[briefing]', '[briefing]\nalerts_enabled = always'))
        with self.assertRaises(ConfigError):
            load_config(self.path, ENV)

    def test_database_url_requires_direct_tls_and_hides_values(self):
        self.assertIn('db.invalid', self.config.database_url)
        self.assertNotIn('synthetic-database-secret', repr(self.config))
        for dsn in ('', 'sqlite:///private-secret', 'postgresql://user:private-secret@host/db?sslmode=disable'):
            with self.subTest(dsn=dsn), self.assertRaises(ConfigError) as error:
                load_config(self.path, {**ENV, 'STOCKMAN_DATABASE_URL':dsn})
            self.assertNotIn('private-secret', str(error.exception))

    def test_context_budget_is_optional_and_validated(self):
        prefix = self.original.replace('context_bytes = 49152\n', '')
        self.path.write_text(prefix)
        self.assertEqual(load_config(self.path, ENV).model.context_bytes, 49152)
        self.path.write_text(prefix.replace('max_tools = 12', 'max_tools = 12\ncontext_bytes = 65536'))
        self.assertEqual(load_config(self.path, ENV).model.context_bytes, 65536)
        self.path.write_text(prefix.replace('max_tools = 12', 'max_tools = 12\ncontext_bytes = 4095'))
        with self.assertRaises(ConfigError):
            load_config(self.path, ENV)

    def test_explicit_paths_and_secrets_never_appear_in_repr(self):
        with patch('socket.socket.connect',side_effect=AssertionError('network')):
            config=load_config(self.path,{**ENV,'BRIEFING_TIME':'22:00','SNAPTRADE_UNUSED':'9999'})
        self.assertEqual(config.directory,(Path(self.temp.name)/'runtime').resolve());self.assertEqual(config.briefing.at,'08:00');self.assertEqual(config.broker.provider,'snaptrade')
        self.assertFalse(config.directory.exists())
        for value in ENV.values():
            if value!='123':self.assertNotIn(value,repr(config))
    def test_invalid_fields_and_values_are_rejected_without_echoing_values(self):
        for old,new in [('provider = snaptrade','provider = unknown-secret'),('daily_budget_usd = 0.20','daily_budget_usd = nan'),('time = 08:00','time = 99:99'),('interactive = true','interactive = maybe')]:
            self.path.write_text(self.original.replace(old,new))
            with self.assertRaises(ConfigError) as caught:load_config(self.path,ENV)
            self.assertNotIn('unknown-secret',str(caught.exception))
        self.path.write_text(self.original+'\n[evil]\nkey=secret\n')
        with self.assertRaises(ConfigError):load_config(self.path,ENV)
    def test_missing_environment_unauthorized_owner_and_defaults_fail_early(self):
        for change in [{'GEMINI_API_KEY':''},{'TG_ALLOWED_USERS':'124'},{'TG_CHAT_ID':'-123'},{'SNAPTRADE_ACCOUNT_ID':'another-secret'}]:
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
        env=dict(os.environ,STOCKMAN_EXPLICIT_CONFIG='1',STOCKMAN_OFFLINE='1',PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(root/'src'),MONITOR_INTERVAL='invalid-legacy-value',YF_REQUEST_DELAY='nan')
        result=subprocess.run([sys.executable,'-B','-c','import stockman.service; from stockman import config; assert config.MONITOR_INTERVAL==5; assert config.YF_REQUEST_DELAY==5'],cwd=self.temp.name,env=env,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)


    def test_task_budget_configuration_is_optional_and_rates_are_paired(self):
        config=load_config(self.path,ENV)
        self.assertIsNone(config.model.task_budget_usd)
        self.path.write_text(self.original.replace("max_tools = 12","max_tools = 12\ntask_budget_usd = 0.05\ninput_rate = 1.5\noutput_rate = 2.5\nprice_model = gemini-test"))
        config=load_config(self.path,ENV)
        self.assertEqual((config.model.task_budget_usd,config.model.price_model,config.model.input_rate,config.model.output_rate),(0.05,"gemini-test",1.5,2.5))
        for extra in ["task_budget_usd = nan","task_budget_usd = 0","input_rate = 1","input_rate = 1\noutput_rate = -1"]:
            self.path.write_text(self.original.replace("max_tools = 12","max_tools = 12\n"+extra))
            with self.assertRaises(ConfigError):load_config(self.path,ENV)
