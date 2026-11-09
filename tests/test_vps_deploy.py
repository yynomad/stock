import importlib.util
import hashlib
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('stockman_vps', ROOT / 'scripts/vps.py')
vps = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vps)


class VPSPackageTest(unittest.TestCase):
    def fixture(self, root):
        for name in vps.ROOT_FILES:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('source')
        source = root / 'src/stockman/harness.py'
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text('CURRENT_WORKING_COPY = True')

    def test_package_keeps_working_source_and_excludes_credentials_and_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            for name in ('.env', 'stock.conf', 'runtime/holdings.sqlite', '.git/config',
                         'src/stockman/.env', 'scripts/deploy.private.conf'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('PRIVATE_CANARY')
            info = vps.package(root / 'source.tar.gz', root)
            with tarfile.open(info['archive']) as archive:
                names = archive.getnames()
                self.assertIn('src/stockman/harness.py', names)
                self.assertIn('scripts/vps_install.sh', names)
                self.assertTrue(all(not name.startswith(('/', '..')) for name in names))
                for member in archive.getmembers():
                    self.assertTrue(member.isfile())
                    self.assertNotIn(b'PRIVATE_CANARY', archive.extractfile(member).read())
            with self.assertRaises(FileExistsError):
                vps.package(root / 'source.tar.gz', root)

    def test_package_rejects_source_symlink_before_writing_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            (root / 'src/stockman/harness.py').unlink()
            (root / 'src/stockman/harness.py').symlink_to(root / '.env')
            (root / '.env').write_text('PRIVATE_CANARY')
            with self.assertRaises(ValueError):
                vps.package(root / 'source.tar.gz', root)
            self.assertFalse((root / 'source.tar.gz').exists())

    def test_deploy_streams_the_existing_archive_with_its_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            archive = root / 'source.tar.gz'
            vps.package(archive, root)
            expected = archive.read_bytes()
            identity = root / 'ssh-key'
            identity.touch()

            def inspect_ssh(args, *, stdin, check):
                self.assertEqual(stdin.read(), expected)
                self.assertIn(hashlib.sha256(expected).hexdigest(), args[-1])
                self.assertIn('StrictHostKeyChecking=yes', args)
                self.assertEqual(args[-2], 'ubuntu@example.com')
                self.assertTrue(check)

            with patch.object(vps, 'package', side_effect=AssertionError('repackaged')), \
                 patch.object(vps.subprocess, 'run', side_effect=inspect_ssh) as ssh, \
                 patch('builtins.print'):
                vps.deploy('ubuntu@example.com', identity, 22, archive)
            ssh.assert_called_once()
