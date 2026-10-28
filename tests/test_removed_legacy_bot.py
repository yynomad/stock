import importlib.util
import unittest
import bot


class RemovedLegacyBotTest(unittest.TestCase):
    def test_bot_no_longer_exposes_legacy_chat_surface(self):
        # 2026-10-28 清理双启动路线：旧 .env 直连聊天实现必须不可达，
        # 避免新改动误落到已废弃路线上。
        for name in ("_chat_with_gemini", "_execute_function", "_watchlist_tools",
                     "tg_get_updates", "tg_send_message", "_run_analysis", "watchlist"):
            self.assertFalse(hasattr(bot, name), name)

    def test_legacy_price_monitor_module_removed(self):
        # stockman.monitor.PriceMonitor 只被旧 bot.py 死代码引用，已删除；
        # 现行价格监控是 stockman.monitoring.MonitoringService，不要混淆。
        self.assertIsNone(importlib.util.find_spec("stockman.monitor"))
        self.assertIsNotNone(importlib.util.find_spec("stockman.monitoring"))


if __name__ == "__main__":
    unittest.main()
