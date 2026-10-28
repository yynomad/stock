import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class ImportSafetyTest(unittest.TestCase):
    def run_child(self, code, **settings):
        env = dict(os.environ, STOCKMAN_OFFLINE="1", PYTHONDONTWRITEBYTECODE="1",
                   GEMINI_API_KEY="", TG_BOT_TOKEN="", TG_CHAT_ID="")
        env.update(settings)
        with tempfile.TemporaryDirectory() as directory:
            process = subprocess.run([sys.executable,"-B","-c", f"import sys; sys.path.insert(0,{str(ROOT)!r});"+code],
                                     cwd=directory,env=env,capture_output=True,text=True)
            self.assertEqual(list(Path(directory).iterdir()), [])
        return process

    def test_imports_do_not_connect_write_or_start_threads(self):
        p = self.run_child("import socket,threading; socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network')); threading.Thread.start=lambda *a,**k: (_ for _ in ()).throw(AssertionError('thread')); import bot,premarket_grid_calculator; assert bot._chat_with_gemini('hi').startswith('⚠️')")
        self.assertEqual(p.returncode,0,p.stderr)

    def test_invalid_operational_config_is_rejected(self):
        for name,value in [("MONITOR_INTERVAL","0"),("YF_REQUEST_DELAY","nan"),("MONITOR_BUFFER_PCT","-1")]:
            p = self.run_child("import lib.config",**{name:value})
            self.assertNotEqual(p.returncode,0)
            self.assertIn(name,p.stderr)

    def test_missing_google_sdk_is_controlled(self):
        p = self.run_child("import builtins; original=builtins.__import__; builtins.__import__=lambda name,*a,**k: (_ for _ in ()).throw(ImportError('SDK missing')) if name.startswith('google') else original(name,*a,**k); import bot; bot.GEMINI_API_KEY='synthetic'; assert '出错' in bot._chat_with_gemini('hi')")
        self.assertEqual(p.returncode,0,p.stderr)
