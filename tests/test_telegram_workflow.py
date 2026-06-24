import sys
import types
import unittest


def _install_dependency_stubs():
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *args, **kwargs: None
    sys.modules.setdefault("dotenv", dotenv)

    google = types.ModuleType("google")
    genai = types.ModuleType("google.genai")
    genai_types = types.ModuleType("google.genai.types")
    genai.types = genai_types
    google.genai = genai
    sys.modules.setdefault("google", google)
    sys.modules.setdefault("google.genai", genai)
    sys.modules.setdefault("google.genai.types", genai_types)

    requests = types.ModuleType("requests")
    requests.get = lambda *args, **kwargs: None
    requests.post = lambda *args, **kwargs: None
    sys.modules.setdefault("requests", requests)


_install_dependency_stubs()

from bot import _parse_single_symbol_command
from lib.output import build_brief_telegram_message


class TelegramWorkflowTest(unittest.TestCase):
    def test_parse_single_symbol_with_position(self):
        symbols = _parse_single_symbol_command("/test nvda 10 120")

        self.assertEqual(list(symbols.keys()), ["NVDA"])
        self.assertEqual(symbols["NVDA"]["currency_sign"], "$")
        self.assertEqual(symbols["NVDA"]["shares"], 10.0)
        self.assertEqual(symbols["NVDA"]["avg_cost"], 120.0)

    def test_parse_single_symbol_currency_suffixes(self):
        tokyo = _parse_single_symbol_command("/test 1321.T")
        swiss = _parse_single_symbol_command("/stock XFAB.SW")

        self.assertEqual(tokyo["1321.T"]["currency_sign"], "¥")
        self.assertEqual(swiss["XFAB.SW"]["currency_sign"], "€")

    def test_parse_single_symbol_rejects_bad_numbers(self):
        self.assertEqual(_parse_single_symbol_command("/test AAPL bad"), {})
        self.assertEqual(_parse_single_symbol_command("/test"), {})
        self.assertIsNone(_parse_single_symbol_command("/help"))

    def test_brief_telegram_message_contains_concise_decision(self):
        message = build_brief_telegram_message([
            {
                "display_name": "NVDA",
                "currency_sign": "$",
                "pa": {
                    "signal": "减仓",
                    "signal_strength": 2,
                    "trend": "up",
                    "live_price": 145.2,
                },
                "decision": {
                    "headline": "止盈 20.0% 当前仓位",
                    "pnl_pct": 21.0,
                    "plan": ["压力/目标 $148.00", "风控线 $132.50"],
                    "notes": ["浮盈亏 +21.0%", "接近阻力", "趋势向上"],
                },
            }
        ])

        self.assertIn("结论：<b>止盈 20.0% 当前仓位</b>", message)
        self.assertIn("PA：减仓 强度2｜趋势 up", message)
        self.assertIn("价格：$145.20", message)
        self.assertIn("计划：压力/目标 $148.00；风控线 $132.50", message)


if __name__ == "__main__":
    unittest.main()
