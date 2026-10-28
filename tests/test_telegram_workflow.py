import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import service
from stockman.watchlist import WatchlistManager
from stockman.output import build_brief_telegram_message


class TelegramWorkflowTest(unittest.TestCase):
    def test_service_entry_forwards_to_stockman_cli(self):
        # service.py 与 stockman 命令共用 CLI，参数与返回值原样转发。
        with patch("stockman.cli.main", return_value=0) as cli_main:
            self.assertEqual(service.main(["--config", "stock.conf", "--check"]), 0)
        cli_main.assert_called_once_with(["--config", "stock.conf", "--check"])

    def test_watchlist_manager_add_list_remove(self):
        # WatchlistManager 仍由 premarket_grid_calculator 使用，直接测管理器本身。
        with tempfile.TemporaryDirectory() as directory:
            manager = WatchlistManager(str(Path(directory)/"watchlist.json"))
            info = manager.add("nvda")
            self.assertEqual(info.get("display_name", "NVDA"), "NVDA")
            self.assertIn("NVDA", manager.get_all())
            self.assertTrue(manager.remove("NVDA"))
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
