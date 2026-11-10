#!/usr/bin/env bash
set -euo pipefail
umask 077

cd "$(dirname "$0")/.."
echo "StockMan test release: $PWD"
python3 -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ required"'

# Dependencies are reconstructed on the target OS using the existing pins.
# Log progress in the new release so failures can be inspected without activation.
mkdir acceptance
python3 -m venv venv
venv/bin/python -m pip install --no-cache-dir -r requirements.txt > acceptance/install.log 2>&1
venv/bin/python -m pip install --no-cache-dir -e . >> acceptance/install.log 2>&1
venv/bin/python -m pip freeze > acceptance/installed.txt

export STOCKMAN_OFFLINE=1 LANGSMITH_TRACING=false BRIEFING_ENABLED=false
export GEMINI_API_KEY='' GOOGLE_API_KEY='' TG_BOT_TOKEN='' TG_CHAT_ID=''
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
export PYTHONPATH="$PWD/src:$PWD:$PWD/scripts"

# Fail closed if any probe attempts an external connection. Source/credentials
# are separate, and every probe uses synthetic data in temporary directories.
venv/bin/python scripts/validate.py > acceptance/validate.log 2>&1
venv/bin/python scripts/test_postgres.py -- venv/bin/python - <<'PY' > acceptance/scenarios.log 2>&1
import runpy
import socket
import sys

def deny(*args, **kwargs):
    raise AssertionError('offline probe attempted a connection')

socket.socket.connect = deny
socket.socket.connect_ex = deny
for script in ('service.py', 'scripts/briefing.py'):
    sys.argv = [script, '--offline', '--scenario', 'end-to-end']
    try:
        runpy.run_path(script, run_name='__main__')
    except SystemExit as exc:
        if exc.code not in (None, 0):
            raise
PY

venv/bin/python - <<'PY'
from datetime import datetime, timezone
import json
from pathlib import Path
import platform

result = {'status': 'installed_and_offline_verified', 'python': platform.python_version(),
          'platform': platform.platform(), 'release': str(Path.cwd()),
          'verified_at': datetime.now(timezone.utc).isoformat(), 'live_service_started': False}
Path('acceptance/result.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result))
PY
