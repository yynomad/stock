import importlib.util
import unittest
from pathlib import Path


class RemovedLegacyBotTest(unittest.TestCase):
    def test_bot_file_is_removed(self):
        self.assertFalse((Path(__file__).resolve().parents[1] / "bot.py").exists())

    def test_legacy_price_monitor_module_removed(self):
        # stockman.monitor.PriceMonitor 只被旧 bot.py 死代码引用，已删除；
        # 现行价格监控是 stockman.monitoring.MonitoringService，不要混淆。
        self.assertIsNone(importlib.util.find_spec("stockman.monitor"))
        self.assertIsNotNone(importlib.util.find_spec("stockman.monitoring"))


if __name__ == "__main__":
    unittest.main()
