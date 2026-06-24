import sys
import types
import unittest


def _install_dotenv_stub():
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *args, **kwargs: None
    sys.modules.setdefault("dotenv", dotenv)


_install_dotenv_stub()

from lib.position_decision import build_position_decision


class PositionDecisionTest(unittest.TestCase):
    def test_profitable_reduce_signal_becomes_take_profit(self):
        row = {
            "shares": 10,
            "avg_cost": 100,
            "currency_sign": "$",
            "pa": {
                "signal": "减仓",
                "signal_strength": 3,
                "trend": "up",
                "is_near_support": False,
                "is_near_resistance": True,
                "live_price": 140,
                "last_close": 138,
                "target_price": 145,
                "trailing_stop": 125,
            },
        }

        decision = build_position_decision(row)

        self.assertEqual(decision["action"], "take_profit")
        self.assertEqual(decision["label"], "分批止盈")
        self.assertEqual(decision["size_pct"], 35.0)
        self.assertEqual(decision["pnl_pct"], 40.0)
        self.assertIn("压力/目标 $145.00", decision["plan"])
        self.assertIn("风控线 $125.00", decision["plan"])

    def test_losing_reduce_signal_becomes_risk_reduce_not_take_profit(self):
        row = {
            "shares": 10,
            "avg_cost": 150,
            "currency_sign": "$",
            "pa": {
                "signal": "减仓",
                "signal_strength": 2,
                "trend": "down",
                "is_near_support": False,
                "is_near_resistance": False,
                "live_price": 120,
                "last_close": 121,
                "target_price": 130,
                "trailing_stop": 118,
            },
        }

        decision = build_position_decision(row)

        self.assertEqual(decision["action"], "risk_reduce")
        self.assertEqual(decision["label"], "风控减仓")
        self.assertEqual(decision["size_pct"], 25.0)
        self.assertEqual(decision["pnl_pct"], -20.0)
        self.assertIn("非止盈", decision["headline"])

    def test_no_position_reduce_signal_avoids_chasing(self):
        row = {
            "currency_sign": "$",
            "pa": {
                "signal": "减仓",
                "signal_strength": 2,
                "trend": "up",
                "is_near_support": False,
                "is_near_resistance": True,
                "last_close": 210,
                "target_price": 215,
            },
        }

        decision = build_position_decision(row)

        self.assertEqual(decision["action"], "avoid_chase")
        self.assertEqual(decision["headline"], "无仓不追，等待回踩")
        self.assertIn("压力/目标 $215.00", decision["plan"])

    def test_strong_add_signal_near_support_builds_position(self):
        row = {
            "currency_sign": "$",
            "pa": {
                "signal": "加仓",
                "signal_strength": 3,
                "trend": "up",
                "is_near_support": True,
                "is_near_resistance": False,
                "last_close": 100,
                "limit_entry": 98,
                "target_price": 110,
            },
        }

        decision = build_position_decision(row)

        self.assertEqual(decision["action"], "add_on_pullback")
        self.assertEqual(decision["label"], "回踩建仓")
        self.assertEqual(decision["size_pct"], 10.0)
        self.assertIn("买入限价 $98.00", decision["plan"])

    def test_data_error_waits(self):
        row = {"error": "K线数据获取失败", "pa": {}}

        decision = build_position_decision(row)

        self.assertEqual(decision["action"], "wait_data")
        self.assertEqual(decision["headline"], "数据不足，暂不操作")


if __name__ == "__main__":
    unittest.main()
