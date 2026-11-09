#!/usr/bin/env python3
"""Package the current source or install and test it through an existing SSH login."""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import uuid


ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = (
    'README.md', 'pyproject.toml', 'requirements.txt', 'service.py',
    'premarket_grid_calculator.py', 'stock.conf.example', 'briefing.sources.json',
    'env.template', 'evals/cases-v1.json', 'evals/datasets/v1.json',
    'scripts/vps_install.sh',
)


def package(output, root=ROOT):
    paths = {root / name for name in ROOT_FILES}
    for name in ('src/stockman', 'scripts', 'tests'):
        paths.update((root / name).rglob('*.py'))
    for path in paths:
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('source must contain ordinary files within the project')
    output = Path(output).resolve()
    if output in {path.resolve() for path in paths}:
        raise ValueError('archive would replace a source file')
    with tarfile.open(output, 'x:gz') as archive:
        for path in sorted(paths):
            archive.add(path, arcname=str(path.relative_to(root)), recursive=False)
    return {'archive': str(output), 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
            'files': len(paths), 'source': 'current working tree, including uncommitted source'}


def deploy(host, identity, port, archive=None):
    if not re.fullmatch(r'[a-z_][a-z0-9_-]*@[A-Za-z0-9][A-Za-z0-9.-]*', host):
        raise ValueError('host must be user@hostname or user@IPv4')
    identity = Path(identity).expanduser().resolve(strict=True)
    release = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8]
    with ExitStack() as stack:
        if archive is None:
            directory = stack.enter_context(tempfile.TemporaryDirectory(prefix='stockman-deploy-'))
            info = package(Path(directory) / 'source.tar.gz')
            print(json.dumps(info), flush=True)
            archive = Path(info['archive'])
        source = stack.enter_context(Path(archive).open('rb'))
        digest = hashlib.sha256(source.read()).hexdigest()
        source.seek(0)
        # The remote destination is fixed under the SSH user's home. A fresh release
        # gets its own interpreter, logs and tests, so failed installs stay isolated.
        command = f'''set -eu
umask 077
release="$HOME/.local/share/stockman/releases/{release}"
mkdir -p "$HOME/.local/share/stockman/releases"
mkdir "$release"
cat > "$release/source.tar.gz"
cd "$release"
echo '{digest}  source.tar.gz' | sha256sum -c -
tar -xzf source.tar.gz
bash scripts/vps_install.sh
'''
        subprocess.run(['ssh', '-T', '-i', str(identity), '-p', str(port),
                        '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
                        '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10',
                        '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes',
                        host, command], stdin=source, check=True)
        print(json.dumps({'status': 'installed_and_offline_verified', 'release': release,
                          'location': '~/.local/share/stockman/releases/' + release}))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    pack = commands.add_parser('package')
    pack.add_argument('--output', required=True)
    remote = commands.add_parser('deploy')
    remote.add_argument('--host', required=True)
    remote.add_argument('--identity', required=True)
    remote.add_argument('--port', type=int, default=22, choices=range(1, 65536), metavar='PORT')
    remote.add_argument('--archive', type=Path, help='upload an existing verified source archive')
    args = parser.parse_args(argv)
    try:
        if args.action == 'package':
            print(json.dumps(package(args.output)))
        else:
            deploy(args.host, args.identity, args.port, args.archive)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__}))
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
