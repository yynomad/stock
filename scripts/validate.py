#!/usr/bin/env python3
"""Run offline checks without loading real credentials or sending requests."""
import ast
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]

def main():
    for path in list(ROOT.glob("*.py"))+list((ROOT/"src").rglob("*.py"))+list((ROOT/"tests").rglob("*.py"))+list((ROOT/"scripts").glob("*.py")):
        ast.parse(path.read_text(),filename=str(path))
    for name in ["Product.md","Plan.md","AGENTS.md","Session_Handoff.md","README.md","Learning.md"]:
        text=(ROOT/name).read_text()
        assert text.startswith("# ") and text.count("```")%2==0,name
        for target in re.findall(r"\]\(([^)]+)\)",text):
            if not target.startswith(("https://","http://","#")):
                assert (ROOT/target.split("#")[0]).is_file(),target
    assert 'gemini-3.5-flash-lite' in (ROOT/'src/stockman/config.py').read_text()
    env=dict(os.environ,STOCKMAN_OFFLINE="1",PYTHONDONTWRITEBYTECODE="1",PYTHONPATH=str(ROOT/'src')+os.pathsep+str(ROOT),
             GEMINI_API_KEY="",GOOGLE_API_KEY="",TG_BOT_TOKEN="",TG_CHAT_ID="",LANGSMITH_TRACING="false",BRIEFING_ENABLED="false")
    with tempfile.TemporaryDirectory() as directory:
        env['STOCKMAN_DATA_DIR']=directory
        result=subprocess.run([sys.executable,"-B","-c", "import socket,sys,unittest; socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('offline network attempt')); suite=unittest.defaultTestLoader.discover(sys.argv[1]); result=unittest.TextTestRunner(verbosity=2).run(suite); sys.exit(not result.wasSuccessful())",str(ROOT/'tests')],cwd=directory,env=env)
    if result.returncode:
        return result.returncode
    return subprocess.run([sys.executable,"-m","pip","check"],env=env).returncode

if __name__=="__main__":
    raise SystemExit(main())
