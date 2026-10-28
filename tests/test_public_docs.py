"""Documentation validation must work without private development notes."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("stockman_validate_docs", ROOT / "scripts/validate.py")
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


class PublicDocumentationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "src/stockman").mkdir(parents=True)
        (self.root / "src/stockman/config.py").write_text('MODEL = "gemini-3.5-flash-lite"\n')
        (self.root / "stock.conf.example").write_text("[service]\n")
        (self.root / "README.md").write_text("# StockMan\n\n[Config](stock.conf.example)\n\n```sh\nstockman --help\n```\n")

    def test_clean_checkout_needs_only_public_readme(self):
        with patch.object(validator, "ROOT", self.root), patch.object(validator.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(validator.main(), 0)
        self.assertEqual(run.call_count, 2)
        self.assertIn("unittest", run.call_args_list[0].args[0][3])
        self.assertEqual(run.call_args_list[1].args[0][-2:], ["pip", "check"])

    def test_missing_readme_is_still_rejected(self):
        (self.root / "README.md").unlink()
        with patch.object(validator, "ROOT", self.root), patch.object(validator.subprocess, "run") as run:
            with self.assertRaises(FileNotFoundError):
                validator.main()
        run.assert_not_called()

    def test_broken_links_and_unclosed_fences_are_still_rejected(self):
        for text in ("# StockMan\n[Missing](missing.conf)\n", "# StockMan\n```sh\nstockman --help\n"):
            with self.subTest(text=text):
                (self.root / "README.md").write_text(text)
                with patch.object(validator, "ROOT", self.root), patch.object(validator.subprocess, "run") as run:
                    with self.assertRaises(AssertionError):
                        validator.main()
                run.assert_not_called()
