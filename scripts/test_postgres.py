#!/usr/bin/env python3
"""Run a command against a disposable PostgreSQL server on a private Unix socket."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == '--':
        args.pop(0)
    if not args:
        raise SystemExit('usage: scripts/test_postgres.py -- command [args ...]')

    if os.getenv('STOCKMAN_OFFLINE') == '1':
        socket_dir = os.getenv('STOCKMAN_TEST_PG_SOCKET', '')
        if socket_dir and Path(socket_dir).is_absolute() and (Path(socket_dir) / '.s.PGSQL.5432').is_socket():
            return subprocess.call(args)

    bin_dir = os.getenv('STOCKMAN_POSTGRES_BIN')
    if not bin_dir and shutil.which('pg_config'):
        probe = subprocess.run(['pg_config', '--bindir'], capture_output=True, text=True)
        if probe.returncode == 0 and (Path(probe.stdout.strip()) / 'initdb').is_file():
            bin_dir = probe.stdout.strip()
    initdb = str(Path(bin_dir) / 'initdb') if bin_dir else shutil.which('initdb')
    pg_ctl = str(Path(bin_dir) / 'pg_ctl') if bin_dir else shutil.which('pg_ctl')
    if not initdb or not pg_ctl:
        raise SystemExit('PostgreSQL initdb and pg_ctl are required. Set STOCKMAN_POSTGRES_BIN or add them to PATH.')

    # Keep the path short enough for a Unix-domain socket on macOS.
    with tempfile.TemporaryDirectory(prefix='stockman-pg-', dir='/tmp') as directory:
        home = Path(directory)
        data = home / 'data'
        env = {key:value for key,value in os.environ.items() if not key.startswith('PG')}
        env.update(STOCKMAN_OFFLINE='1', STOCKMAN_TEST_PG_SOCKET=directory,
                   STOCKMAN_DATABASE_URL='', LANGSMITH_TRACING='false')
        subprocess.run([initdb, '-D', str(data), '-U', 'stockman_test', '-A', 'trust',
                        '--locale=C', '--encoding=UTF8'], check=True, env=env, stdout=subprocess.DEVNULL)
        subprocess.run([pg_ctl, '-D', str(data), '-l', str(home / 'server.log'),
                        '-o', f"-h '' -k {directory} -p 5432 -F", '-w', 'start'],
                       check=True, env=env, stdout=subprocess.DEVNULL)
        try:
            return subprocess.call(args, env=env)
        finally:
            subprocess.run([pg_ctl, '-D', str(data), '-w', '-m', 'immediate', 'stop'],
                           check=True, env=env, stdout=subprocess.DEVNULL)


if __name__ == '__main__':
    raise SystemExit(main())
