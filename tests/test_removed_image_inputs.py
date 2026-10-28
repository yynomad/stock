"""Regression coverage for the intentionally removed screenshot input feature."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from stockman.agents import OfflineModel
from stockman.application import Application
from stockman.briefing.config import BriefingConfig

ROOT=Path(__file__).resolve().parents[1]

class RemovedImageInputsTest(unittest.TestCase):
    def test_removed_cli_options_fail_before_runtime_or_logging_is_created(self):
        for script,option in [('scripts/chat.py','--image'),('scripts/briefing.py','--snapshot'),('premarket_grid_calculator.py','--image')]:
            with self.subTest(script=script),tempfile.TemporaryDirectory() as d:
                env=dict(os.environ,STOCKMAN_OFFLINE='1',PYTHONDONTWRITEBYTECODE='1',GEMINI_API_KEY='',TG_BOT_TOKEN='',TG_CHAT_ID='')
                result=subprocess.run([sys.executable,'-B',str(ROOT/script),option,'synthetic'],cwd=d,env=env,capture_output=True,text=True)
                self.assertEqual(result.returncode,2,result.stdout+result.stderr)
                self.assertIn('unrecognized arguments',result.stderr)
                self.assertEqual(list(Path(d).iterdir()),[])

    def test_legacy_image_source_is_rejected_before_creating_runtime(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'BRIEFING_PORTFOLIO_SOURCE':'image'}):
            target=Path(d)/'runtime'
            with self.assertRaisesRegex(ValueError,'截图来源已移除'):Application(target,OfflineModel())
            self.assertFalse(target.exists())

    def test_legacy_broker_source_setting_remains_compatible(self):
        self.assertEqual(BriefingConfig.load({'BRIEFING_PORTFOLIO_SOURCE':'ibkr'}),BriefingConfig.load({}))

    def test_photo_from_unauthorized_user_does_not_create_tasks_or_files(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as d:
            transport=Mock(chat=123);app=Application(d,OfflineModel(),transport=transport);app.allowed={123}
            try:
                update={'update_id':9,'message':{'from':{'id':999},'chat':{'id':123},'photo':[{'file_id':'secret'}]}}
                self.assertEqual(app.handle_update(update),'unauthorized')
                self.assertEqual(app.handle_update(update),'duplicate')
                transport.photo.assert_not_called();transport.deliver.assert_not_called()
                self.assertFalse(app.runtime.store.execute('SELECT * FROM tasks'))
                self.assertFalse((Path(d)/'screenshots').exists())
            finally:app.runtime.close()
