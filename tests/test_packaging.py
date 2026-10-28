"""Guard the src layout and the two forwarding entry points."""
import importlib
from pathlib import Path
import builtins
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class PackagingTest(unittest.TestCase):
    def test_src_layout_and_scripts_need_no_path_patches(self):
        self.assertFalse((ROOT / "lib").exists())
        self.assertFalse((ROOT / "bot.py").exists())
        self.assertTrue((ROOT / "src/stockman/__init__.py").is_file())
        for directory in (ROOT / "src", ROOT / "scripts"):
            paths = list(directory.rglob("*.py"))
            self.assertTrue(paths, str(directory))
            for path in paths:
                self.assertNotIn("sys.path.insert", path.read_text(), str(path))

    def test_project_metadata_registers_stockman(self):
        text = (ROOT / "pyproject.toml").read_text()
        self.assertRegex(text, r'name\s*=\s*"stockman"')
        self.assertIn("[project.scripts]", text)
        self.assertIn('stockman = "stockman.cli:main"', text)

    def test_cli_and_root_service_forward_arguments_and_result(self):
        cli = importlib.import_module("stockman.cli")
        service = importlib.import_module("service")
        self.assertTrue(callable(cli.main))
        self.assertTrue(callable(service.main))
        argv = ["--offline", "--scenario", "end-to-end"]
        with patch("stockman.cli.main", return_value=7) as main:
            self.assertEqual(service.main(argv), 7)
        main.assert_called_once_with(argv)
        original_import = builtins.__import__
        attempts = []

        def missing_once(name, *args, **kwargs):
            if name == "stockman.cli":
                attempts.append(name)
                if len(attempts) == 1:
                    raise ImportError("package not installed")
            return original_import(name, *args, **kwargs)

        with patch.object(sys, "path", list(sys.path)), patch("builtins.__import__", side_effect=missing_once), patch("stockman.cli.main", return_value=9) as main:
            self.assertEqual(service.main(argv), 9)
            self.assertEqual(sys.path[0], str(ROOT / "src"))
        self.assertEqual(len(attempts), 2)
        main.assert_called_once_with(argv)
