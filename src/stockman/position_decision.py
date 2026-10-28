"""
仓位决策层
─────────────────────────────────────────────────────────────
把 PA 技术信号转成更明确的仓位动作：
  · add_on_pullback  回踩加仓
  · take_profit      分批止盈
  · risk_reduce      风控减仓
  · avoid_chase      不追高
  · hold             持有观察

这层会使用持仓数据（股数 / 成本）和 PA 输出，避免把「减仓」一律解释为止盈。
"""

from .config import (
    POSITION_PROBE_ADD_PCT,
    POSITION_NORMAL_ADD_PCT,
    POSITION_STRONG_ADD_PCT,
    POSITION_TAKE_PROFIT_PCT,
    POSITION_STRONG_TAKE_PROFIT_PCT,
    POSITION_RISK_REDUCE_PCT,
)
from .format_utils import fmt_price


def _round_pct(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value * 100, 1)


def _has_position(shares) -> bool:
    return shares is not None and shares > 0


def build_position_decision(row: dict) -> dict:
    """根据单个标的的 PA 结果和持仓数据，生成明确仓位动作。"""
    pa = row.get("pa", {})
    if row.get("error") or pa.get("pa_error"):
        return {
            "action": "wait_data",
            "label": "等待数据",
            "headline": "数据不足，暂不操作",
            "size_pct": None,
            "pnl_pct": None,
            "notes": ["行情或 PA 数据不足"],
        }

    shares = row.get("shares")
    avg_cost = row.get("avg_cost")
    currency_sign = row.get("currency_sign", "$")
    has_position = _has_position(shares)
    price = pa.get("live_price") or pa.get("last_close")
    pnl_pct = None
    if has_position and avg_cost and price:
        pnl_pct = (price - avg_cost) / avg_cost

    signal = pa.get("signal")
    strength = pa.get("signal_strength", 0)
    trend = pa.get("trend")
    near_support = pa.get("is_near_support")
    near_resistance = pa.get("is_near_resistance")
    trailing_stop = pa.get("trailing_stop")
    limit_entry = pa.get("limit_entry")
    target_price = pa.get("target_price")

    notes = []
    if pnl_pct is not None:
        notes.append(f"浮盈亏 {_round_pct(pnl_pct):+.1f}%")
    if near_support:
        notes.append("接近支撑")
    if near_resistance:
        notes.append("接近阻力")
    if trend == "up":
        notes.append("趋势向上")
    elif trend == "down":
        notes.append("趋势向下")

    action = "hold"
    label = "持有观察"
    headline = "观望，不主动交易"
    size_pct = None

    if signal == "加仓":
        if has_position:
            if trend == "up" and near_support and strength >= 3:
                action = "add_on_pullback"
                label = "回踩加仓"
                size_pct = POSITION_STRONG_ADD_PCT
                headline = f"可加仓 {_round_pct(size_pct):.1f}% 计划仓位"
            elif trend == "up" and strength >= 2:
                action = "add_on_pullback"
                label = "小幅加仓"
                size_pct = POSITION_NORMAL_ADD_PCT
                headline = f"可小幅加仓 {_round_pct(size_pct):.1f}% 计划仓位"
            else:
                action = "hold"
                label = "持有观察"
                headline = "已有仓位，先观察确认"
        else:
            if trend == "down":
                action = "probe_add"
                label = "试探建仓"
                size_pct = POSITION_PROBE_ADD_PCT
                headline = f"仅试探 {_round_pct(size_pct):.1f}% 计划仓位"
            elif strength >= 3 and near_support:
                action = "add_on_pullback"
                label = "回踩建仓"
                size_pct = POSITION_NORMAL_ADD_PCT
                headline = f"可建仓 {_round_pct(size_pct):.1f}% 计划仓位"
            else:
                action = "probe_add"
                label = "轻仓试探"
                size_pct = POSITION_PROBE_ADD_PCT
                headline = f"轻仓试探 {_round_pct(size_pct):.1f}% 计划仓位"

    elif signal == "减仓":
        if has_position:
            if pnl_pct is not None and pnl_pct > 0:
                action = "take_profit"
                label = "分批止盈"
                size_pct = (
                    POSITION_STRONG_TAKE_PROFIT_PCT
                    if strength >= 3 and (near_resistance or pnl_pct >= 0.30)
                    else POSITION_TAKE_PROFIT_PCT
                )
                headline = f"止盈 {_round_pct(size_pct):.1f}% 当前仓位"
            else:
                action = "risk_reduce"
                label = "风控减仓"
                size_pct = POSITION_RISK_REDUCE_PCT if strength >= 2 else POSITION_TAKE_PROFIT_PCT
                headline = f"非止盈，风控减仓 {_round_pct(size_pct):.1f}% 当前仓位"
        else:
            action = "avoid_chase"
            label = "不追高"
            headline = "无仓不追，等待回踩"

    elif signal == "观望":
        if has_position:
            action = "hold"
            label = "持有观察"
            headline = "持有，不加不减"
        else:
            action = "wait"
            label = "等待"
            headline = "无仓等待更清晰位置"

    price_note = None
    if price is not None:
        price_note = f"参考价 {fmt_price(price, currency_sign)}"
    plan = []
    if limit_entry is not None and action in ("add_on_pullback", "probe_add"):
        plan.append(f"买入限价 {fmt_price(limit_entry, currency_sign)}")
    if target_price is not None and action in ("take_profit", "avoid_chase", "hold"):
        plan.append(f"压力/目标 {fmt_price(target_price, currency_sign)}")
    if trailing_stop is not None and has_position:
        plan.append(f"风控线 {fmt_price(trailing_stop, currency_sign)}")

    return {
        "action": action,
        "label": label,
        "headline": headline,
        "size_pct": _round_pct(size_pct),
        "pnl_pct": _round_pct(pnl_pct),
        "price_note": price_note,
        "plan": plan,
        "notes": notes[:4],
    }
