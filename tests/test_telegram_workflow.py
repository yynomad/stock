import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
import bot
from lib.watchlist import WatchlistManager
from lib.output import build_brief_telegram_message


class TelegramWorkflowTest(unittest.TestCase):
    def test_missing_key_returns_configuration_message(self):
        with patch.object(bot, "GEMINI_API_KEY", ""), patch("requests.post") as send:
            self.assertIn("未配置", bot._chat_with_gemini("检查持仓"))
            send.assert_not_called()

    def test_watchlist_tool_add_list_remove(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = WatchlistManager(str(Path(directory)/"watchlist.json"))
            with patch.object(bot, "watchlist", manager):
                result = bot._execute_function(SimpleNamespace(name="add_to_watchlist", args={"symbol":"nvda"}))
                self.assertIn("NVDA", result)
                self.assertIn("NVDA", bot._execute_function(SimpleNamespace(name="list_watchlist", args={})))
                bot._execute_function(SimpleNamespace(name="remove_from_watchlist", args={"symbol":"NVDA"}))
                self.assertEqual(len(manager), 0)

    def test_brief_message_keeps_position_decision(self):
        message = build_brief_telegram_message([{
            "display_name":"NVDA", "currency_sign":"$",
            "pa":{"signal":"减仓", "signal_strength":2, "trend":"up", "live_price":145.2},
            "decision":{"headline":"止盈 20.0% 当前仓位", "pnl_pct":21.0,
                        "plan":["压力/目标 $148.00", "风控线 $132.50"], "notes":["接近阻力"]}}])
        self.assertIn("止盈 20.0% 当前仓位", message)
        self.assertIn("价格：$145.20", message)
        self.assertIn("风控线 $132.50", message)

    def test_tools_are_lazy_and_buildable(self):
        self.assertEqual(len(bot._watchlist_tools()[0].function_declarations), 3)
