"""
Price Action 分析引擎（v3.0 纯K线版）
─────────────────────────────────────────────────────────────
基础指标：
  · compute_ma           简单移动平均
  · compute_atr          Average True Range
  · find_swing_highs/lows 局部高低点

K线形态：
  · detect_pin_bar
  · detect_engulfing
  · count_consecutive

综合分析：
  · compute_trailing_stop  ATR 追踪止损 + 限价单
  · analyze_pa             综合趋势/位置/形态评分 → 信号
"""

from .config import (
    PA_TREND_MA_SHORT, PA_TREND_MA_LONG, PA_SWING_LOOKBACK,
    PA_NEAR_LEVEL_PCT, PA_PIN_UPPER_RATIO, PA_PIN_LOWER_RATIO,
    PA_ENGULFING_MIN_BODY, PA_CONSECUTIVE_DAYS,
    ATR_PERIOD, ATR_STOP_MULTIPLIER, LIMIT_ENTRY_PCT,
)
from .format_utils import fmt_price, pct_str


# ══════════════════════════════════════════════════════════════════════
#  基础指标
# ══════════════════════════════════════════════════════════════════════

def compute_ma(bars: list[dict], period: int) -> list:
    """计算简单移动平均线。"""
    closes = [b["close"] for b in bars]
    result = []
    for i in range(len(closes)):
        if i < period - 1:
            result.append(None)
        else:
            result.append(sum(closes[i - period + 1:i + 1]) / period)
    return result


def find_swing_highs(bars, lookback=PA_SWING_LOOKBACK):
    """找出 swing high（局部最高点）。"""
    swings = []
    for i in range(lookback, len(bars) - 1):
        window = bars[i - lookback:i + 1]
        if bars[i]["high"] == max(b["high"] for b in window):
            # 避免重复：与上一个swing间距>=3根K线
            if not swings or i - swings[-1][0] >= 3:
                swings.append((i, bars[i]["high"]))
    return swings


def find_swing_lows(bars, lookback=PA_SWING_LOOKBACK):
    """找出 swing low（局部最低点）。"""
    swings = []
    for i in range(lookback, len(bars) - 1):
        window = bars[i - lookback:i + 1]
        if bars[i]["low"] == min(b["low"] for b in window):
            if not swings or i - swings[-1][0] >= 3:
                swings.append((i, bars[i]["low"]))
    return swings


def compute_atr(bars: list[dict], period: int = ATR_PERIOD) -> float:
    """计算 Average True Range。"""
    if len(bars) < period + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        high = bars[i]["high"]
        low = bars[i]["low"]
        prev_close = bars[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


# ══════════════════════════════════════════════════════════════════════
#  K线形态
# ══════════════════════════════════════════════════════════════════════

def detect_pin_bar(bar: dict) -> str:
    """检测单根K线是否为 Pin Bar。返回 "bearish" / "bullish" / "" """
    bar_range = bar["high"] - bar["low"]
    if bar_range < 1e-9:
        return ""

    body_size = abs(bar["close"] - bar["open"])
    min_body = bar_range * 0.03
    if body_size < min_body:
        return ""

    upper_wick = bar["high"] - max(bar["open"], bar["close"])
    lower_wick = min(bar["open"], bar["close"]) - bar["low"]
    close_pos = (bar["close"] - bar["low"]) / bar_range

    if upper_wick >= body_size * PA_PIN_UPPER_RATIO and close_pos < 0.45:
        return "bearish"
    if lower_wick >= body_size * PA_PIN_LOWER_RATIO and close_pos > 0.55:
        return "bullish"

    return ""


def detect_engulfing(bars: list[dict], idx: int) -> str:
    """检测吞没形态。返回 "bullish" / "bearish" / "" """
    if idx < 1:
        return ""

    prev = bars[idx - 1]
    curr = bars[idx]

    prev_body = abs(prev["close"] - prev["open"])
    prev_range = prev["high"] - prev["low"]

    if prev_range < 1e-9 or prev_body < prev_range * PA_ENGULFING_MIN_BODY:
        return ""

    # 看涨吞没
    if prev["close"] < prev["open"] and curr["close"] > curr["open"]:
        if curr["open"] <= prev["close"] and curr["close"] >= prev["open"]:
            return "bullish"

    # 看跌吞没
    if prev["close"] > prev["open"] and curr["close"] < curr["open"]:
        if curr["open"] >= prev["close"] and curr["close"] <= prev["open"]:
            return "bearish"

    return ""


def count_consecutive(bars: list[dict]) -> tuple:
    """统计最近的连续同向K线数量和方向。"""
    if len(bars) < 2:
        return (0, "")

    direction = "up" if bars[-1]["close"] > bars[-2]["close"] else "down"
    count = 1

    for i in range(len(bars) - 2, 0, -1):
        if direction == "up" and bars[i]["close"] > bars[i - 1]["close"]:
            count += 1
        elif direction == "down" and bars[i]["close"] < bars[i - 1]["close"]:
            count += 1
        else:
            break

    return (count, direction)


# ══════════════════════════════════════════════════════════════════════
#  ATR 追踪止损
# ══════════════════════════════════════════════════════════════════════

def compute_trailing_stop(
    bars: list[dict],
    currency_sign: str,
    trend: str = "up",
    support: float = None,
) -> dict:
    """
    Trend-aware 止损 / 限价计算。

    根据趋势方向输出不同性质的止损线：
      · trend == "up"   ─ Chandelier Exit（保护利润）
          stop = recent_high - ATR_STOP_MULTIPLIER × ATR
          适用：顺势做多 + 持仓有浮盈，价格创新高时止损跟随上抬。
      · trend == "down" ─ 破位止损（割肉线）
          stop = support × (1 - LIMIT_ENTRY_PCT)
          适用：逆势套牢，跌破最近一档支撑确认下行延续。
      · trend == "side" ─ 横盘震荡不输出 trailing_stop（语义不成立）。

    返回字段：
      · trailing_stop      止损价（None 表示未输出）
      · trailing_stop_pct  相对现价百分比（向下为负，向上为正）
      · stop_kind          "chandelier" | "break_support" | None
      · stop_note          人类可读说明
    """
    result = {
        "atr": None,
        "recent_high": None,
        "trailing_stop": None,
        "limit_entry": None,
        "target_price": None,
        "trailing_stop_pct": None,
        "stop_kind": None,
        "stop_note": None,
    }

    if not bars or len(bars) < ATR_PERIOD + 1:
        return result

    atr = compute_atr(bars, ATR_PERIOD)
    if atr is None:
        return result

    result["atr"] = round(atr, 2)

    lookback = min(20, len(bars))
    recent_high = max(b["high"] for b in bars[-lookback:])
    result["recent_high"] = recent_high

    last_close = bars[-1]["close"]

    trailing_stop = None
    if trend == "up":
        # 上升趋势：Chandelier Exit 保护利润
        trailing_stop = recent_high - ATR_STOP_MULTIPLIER * atr
        result["stop_kind"] = "chandelier"
        result["stop_note"] = (
            f"Chandelier 止损 = 近{lookback}日高 {recent_high:.2f} "
            f"- {ATR_STOP_MULTIPLIER}×ATR({atr:.2f})"
        )
    elif trend == "down" and support and support > 0:
        # 下降趋势：跌破支撑止损
        trailing_stop = support * (1 - LIMIT_ENTRY_PCT)
        result["stop_kind"] = "break_support"
        result["stop_note"] = (
            f"破位止损 = 支撑 {support:.2f} × (1 - {LIMIT_ENTRY_PCT*100:.1f}%)"
        )
    else:
        # 横盘 / 信息不足：不输出 trailing_stop
        result["stop_note"] = "横盘或信息不足，未输出止损建议"
        return result

    result["trailing_stop"] = round(trailing_stop, 2)
    if last_close > 0:
        result["trailing_stop_pct"] = round(
            (trailing_stop - last_close) / last_close * 100, 2
        )

    # Sanity check：止损方向异常（应该在现价同侧但跑反了）警告但不丢弃
    if trend == "up" and trailing_stop > last_close:
        result["stop_note"] += " ⚠️ 上升趋势止损反高于现价，可能 ATR 过小或刚启动"
    elif trend == "down" and trailing_stop > last_close:
        result["stop_note"] += " ⚠️ 现价已破支撑下方"

    return result


# ══════════════════════════════════════════════════════════════════════
#  综合分析引擎
# ══════════════════════════════════════════════════════════════════════

def analyze_pa(bars: list[dict], currency_sign: str) -> dict:
    """
    纯K线 Price Action 分析引擎（v3.0）。

    综合判断 → 加仓 / 减仓 / 观望
    """
    result = {
        "last_close":         None,
        "trend":              "sideways",
        "ma_short":           None,
        "ma_long":            None,
        "resistance":         None,
        "support":            None,
        "dist_to_resist":     None,
        "dist_to_support":    None,
        "is_near_resistance": False,
        "is_near_support":    False,
        "pin_bar":            "",
        "engulfing":          "",
        "consecutive_count":  0,
        "consecutive_dir":    "",
        "signal":             "观望",
        "signal_strength":    0,
        "alert_level":        0,
        "diagnosis":          "数据不足，无法分析",
        "key_levels":         "",
        "pa_error":           None,
    }

    if not bars or len(bars) < PA_TREND_MA_LONG + 5:
        result["pa_error"] = f"历史K线数量不足（{len(bars) if bars else 0} 根，需 {PA_TREND_MA_LONG + 5} 根）"
        result["diagnosis"] = "⚪ 历史数据不足，跳过PA分析"
        return result

    last_close = bars[-1]["close"]
    result["last_close"] = last_close

    # ── 1. 趋势判断 ──────────────────────────────────────────────────
    ma_short_list = compute_ma(bars, PA_TREND_MA_SHORT)
    ma_long_list = compute_ma(bars, PA_TREND_MA_LONG)

    ma_short = ma_short_list[-1]
    ma_long = ma_long_list[-1]
    prev_ma_short = ma_short_list[-2]
    prev_ma_long = ma_long_list[-2]

    result["ma_short"] = round(ma_short, 2) if ma_short else None
    result["ma_long"] = round(ma_long, 2) if ma_long else None

    trend_score = 0

    if ma_short and ma_long:
        if prev_ma_short and prev_ma_long:
            if prev_ma_short <= prev_ma_long and ma_short > ma_long:
                trend_score += 2
            elif prev_ma_short >= prev_ma_long and ma_short < ma_long:
                trend_score -= 2

        if ma_short > ma_long:
            trend_score += 1
        elif ma_short < ma_long:
            trend_score -= 1

        if last_close > ma_short:
            trend_score += 1
        elif last_close < ma_short:
            trend_score -= 1
        if last_close > ma_long:
            trend_score += 1
        elif last_close < ma_long:
            trend_score -= 1

    if trend_score >= 3:
        result["trend"] = "up"
    elif trend_score <= -3:
        result["trend"] = "down"
    elif trend_score >= 1:
        result["trend"] = "up"
    elif trend_score <= -1:
        result["trend"] = "down"
    else:
        result["trend"] = "sideways"


    # ── 2. 支撑/阻力位 ──────────────────────────────────────────────
    recent_bars = bars[-30:]
    resistance = max(b["high"] for b in recent_bars)
    support = min(b["low"] for b in recent_bars)

    # 用swing点修正
    swing_highs = find_swing_highs(recent_bars, min(PA_SWING_LOOKBACK, 5))
    swing_lows = find_swing_lows(recent_bars, min(PA_SWING_LOOKBACK, 5))

    # 取最近的、在当前价格上方的swing high作为阻力
    for _, price in reversed(swing_highs):
        if price > last_close:
            resistance = min(resistance, price)
            break

    # 取最近的、在当前价格下方的swing low作为支撑
    for _, price in reversed(swing_lows):
        if price < last_close:
            support = max(support, price)
            break

    dist_to_resist = (resistance - last_close) / resistance if resistance > 0 else 1.0
    dist_to_support = (last_close - support) / last_close if last_close > 0 else 0.0

    is_near_resistance = dist_to_resist <= PA_NEAR_LEVEL_PCT
    is_near_support = dist_to_support <= PA_NEAR_LEVEL_PCT

    result.update({
        "resistance":         resistance,
        "support":            support,
        "dist_to_resist":     dist_to_resist,
        "dist_to_support":    dist_to_support,
        "is_near_resistance": is_near_resistance,
        "is_near_support":    is_near_support,
    })

    # ── 3. K线形态 ───────────────────────────────────────────────────
    # Pin Bar（昨日）
    yest = bars[-1]
    pin_bar = detect_pin_bar(yest)
    result["pin_bar"] = pin_bar

    # Engulfing（昨日 vs 前日）
    engulfing = detect_engulfing(bars, len(bars) - 1)
    result["engulfing"] = engulfing

    # 连续K线
    consec_count, consec_dir = count_consecutive(bars)
    result["consecutive_count"] = consec_count
    result["consecutive_dir"] = consec_dir

    # ── 4. 综合信号判断 ──────────────────────────────────────────────
    #
    # 评分制：正分=加仓倾向，负分=减仓倾向
    #   趋势：       上升趋势 +2，下降趋势 -2，横盘 0
    #   位置：       接近支撑 +2，接近阻力 -2
    #   Pin Bar：    看涨Pin +2，看跌Pin -2
    #   Engulfing：  看涨吞没 +2，看跌吞没 -2
    #   连续K线：    连涨≥3天后 -1（超买），连跌≥3天后 +1（超卖）
    #

    score = 0
    reasons = []

    # 趋势
    if result["trend"] == "up":
        score += 2
        reasons.append("上升趋势")
    elif result["trend"] == "down":
        score -= 2
        reasons.append("下降趋势")
    else:
        reasons.append("横盘震荡")

    # 位置
    if is_near_support:
        score += 2
        reasons.append("接近支撑位")
    elif is_near_support is False and dist_to_support < 0.05:
        score += 1
        reasons.append("距支撑较近")

    if is_near_resistance:
        score -= 2
        reasons.append("接近阻力位")
    elif is_near_resistance is False and dist_to_resist < 0.05:
        score -= 1
        reasons.append("距阻力较近")

    # Pin Bar
    if pin_bar == "bullish":
        score += 2
        reasons.append("看涨Pin Bar")
    elif pin_bar == "bearish":
        score -= 2
        reasons.append("看跌Pin Bar（动能衰竭）")

    # Engulfing
    if engulfing == "bullish":
        score += 2
        reasons.append("看涨吞没")
    elif engulfing == "bearish":
        score -= 2
        reasons.append("看跌吞没")

    # 连续K线（反直觉：连涨多天→过热→倾向减仓，连跌多天→超卖→倾向加仓）
    if consec_count >= PA_CONSECUTIVE_DAYS:
        if consec_dir == "up":
            score -= 1
            reasons.append(f"连涨{consec_count}天（短线过热）")
        elif consec_dir == "down":
            score += 1
            reasons.append(f"连跌{consec_count}天（短线超卖）")

    # ── 生成信号 ─────────────────────────────────────────────────────
    if score >= 3:
        signal = "加仓"
        signal_strength = 3
    elif score >= 1:
        signal = "加仓"
        signal_strength = 2 if score >= 2 else 1
    elif score <= -3:
        signal = "减仓"
        signal_strength = 3
    elif score <= -1:
        signal = "减仓"
        signal_strength = 2 if score <= -2 else 1
    else:
        signal = "观望"
        signal_strength = 0

    # alert_level: 0=正常, 1=注意, 2=强烈预警
    if signal == "减仓" and signal_strength >= 2:
        alert_level = 2
    elif signal == "减仓":
        alert_level = 1
    elif signal == "加仓" and signal_strength >= 3:
        alert_level = 0  # 强加仓信号是好事
    else:
        alert_level = 0

    # ── 诊断文字 ─────────────────────────────────────────────────────
    trend_emoji = {"up": "📈", "down": "📉", "sideways": "↔️"}
    signal_emoji = {"加仓": "🟢", "减仓": "🔴", "观望": "🟡"}

    diagnosis = (
        f"{signal_emoji.get(signal, '⚪')} {signal}（强度{signal_strength}）| "
        f"{trend_emoji.get(result['trend'], '↔️')} {result['trend'].upper()} | "
        + "，".join(reasons)
    )

    key_levels = (
        f"阻力 {fmt_price(resistance, currency_sign)}（{pct_str(dist_to_resist)}）| "
        f"支撑 {fmt_price(support, currency_sign)}（-{dist_to_support*100:.2f}%）"
    )

    result.update({
        "signal":          signal,
        "signal_strength": signal_strength,
        "alert_level":     alert_level,
        "diagnosis":       diagnosis,
        "key_levels":      key_levels,
        "reasons":         reasons,
        "atr":              None,
        "trailing_stop":    None,
        "trailing_stop_pct": None,
        "limit_entry":      None,
        "target_price":     None,
        "recent_high":      None,
    })

    # ── 5. ATR 追踪止损 + 限价单 ─────────────────────────────────────
    ts = compute_trailing_stop(
        bars, currency_sign,
        trend=result["trend"],
        support=support,
    )
    result["atr"] = ts["atr"]
    result["trailing_stop"] = ts["trailing_stop"]
    result["trailing_stop_pct"] = ts["trailing_stop_pct"]
    result["recent_high"] = ts["recent_high"]
    result["stop_kind"] = ts.get("stop_kind")
    result["stop_note"] = ts.get("stop_note")

    # 限价买入（只在加仓/观望时设置）
    if signal in ("加仓", "观望"):
        limit_entry = round(support * (1 + LIMIT_ENTRY_PCT), 2)
        result["limit_entry"] = limit_entry

    # 目标价 = 阻力位
    result["target_price"] = resistance

    return result
