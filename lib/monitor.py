"""
盘中价格监控模块
─────────────────────────────────────────────────────────────
盘前分析完成后，在后台定期轮询实时价格，当价格突破关键价位时主动推送通知。

用法：
  monitor = PriceMonitor(chat_id)
  monitor.update_from_results(results)   # 传入分析结果
  monitor.start()                        # 启动后台线程
  monitor.stop()                         # 停止

通知规则：
  · 实时价格突破支撑/阻力位（含 2% 缓冲确认）才发通知
  · 同一价位同一方向只提醒一次（恢复后再突破可再次提醒）
  · 每个标的每日最多发 N 次提醒
"""

import time
import logging
import threading
from datetime import datetime, date
from typing import Optional

from .config import (
    MONITOR_INTERVAL,
    MONITOR_BUFFER_PCT,
    MONITOR_MAX_DAILY_ALERTS,
    YF_REQUEST_DELAY,
)
from .yahoo_data import fetch_premarket_price
from .format_utils import fmt_price
from .output import send_telegram

logger = logging.getLogger("stockman.monitor")

# ── 价位的"状态"定义 ──────────────────────────────────────────────────
STATUS_NORMAL = "normal"       # 在走廊内
STATUS_BELOW_SUPPORT = "below_support"  # 在支撑下方
STATUS_ABOVE_RESISTANCE = "above_resistance"  # 在阻力上方


class PriceMonitor:
    """价格监控器：后台轮询实时价格，突破关键位时通知。"""

    def __init__(self, chat_id: int):
        self.chat_id = chat_id

        # 关键价位表 {symbol: info}
        self._levels: dict[str, dict] = {}

        # 每个标的上次检查时的状态（用于检测状态变化，避免重复提醒）
        # {symbol: {support: STATUS_*, resistance: STATUS_*}}
        self._states: dict[str, dict] = {}

        # 每日提醒计数 {symbol: int}
        self._daily_count: dict[str, int] = {}

        # 当前日期（每日重置计数）
        self._today = date.today()

        # 线程控制
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    # ── 公开接口 ──────────────────────────────────────────────────────

    def update_levels(self, symbol: str, support: Optional[float],
                      resistance: Optional[float],
                      display_name: str, currency_sign: str,
                      signal: str = "", signal_strength: int = 0,
                      pa_detail: dict = None,
                      shares: Optional[float] = None,
                      avg_cost: Optional[float] = None) -> None:
        """更新或添加一个标的的关键价位。"""
        with self._lock:
            self._levels[symbol] = {
                "support": support,
                "resistance": resistance,
                "display_name": display_name,
                "currency_sign": currency_sign,
                "signal": signal,
                "signal_strength": signal_strength,
                "pa_detail": pa_detail or {},
                "shares": shares,
                "avg_cost": avg_cost,
            }
            # 初始化状态
            if symbol not in self._states:
                self._states[symbol] = {
                    "support": STATUS_NORMAL,
                    "resistance": STATUS_NORMAL,
                }
        logger.info(f"[监控] 更新 {symbol} 价位: 支撑={support}, 阻力={resistance}")

    def update_from_results(self, results: list) -> None:
        """从 PA 分析结果批量更新所有标的的价位。"""
        for r in results:
            if r.get("error"):
                continue
            pa = r.get("pa", {})
            symbol = r["symbol"]
            self.update_levels(
                symbol=symbol,
                support=pa.get("support"),
                resistance=pa.get("resistance"),
                display_name=r.get("display_name", symbol),
                currency_sign=r.get("currency_sign", "$"),
                signal=pa.get("signal", ""),
                signal_strength=pa.get("signal_strength", 0),
                pa_detail=pa,
                shares=r.get("shares"),
                avg_cost=r.get("avg_cost"),
            )

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def monitored_count(self) -> int:
        return len(self._levels)

    def start(self) -> None:
        """启动后台监控线程。"""
        if self._running:
            logger.warning("[监控] 已在运行，忽略重复启动")
            return
        if not self._levels:
            logger.warning("[监控] 无可监控的标的（价位表为空）")
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True,
                                         name="PriceMonitor")
        self._thread.start()

        symbol_list = ", ".join(self._levels.keys())
        msg = (
            f"📡 <b>实时监控已启动</b>\n"
            f"监控标的：{symbol_list}\n"
            f"间隔：{MONITOR_INTERVAL} 分钟 | 缓冲：{MONITOR_BUFFER_PCT*100:.0f}%\n"
            f"每标的每日最多提醒：{MONITOR_MAX_DAILY_ALERTS} 次\n\n"
            f"价格在支撑/阻力之间正常波动时不会打扰您。"
        )
        send_telegram(msg)
        logger.info(f"[监控] 已启动，监控 {len(self._levels)} 个标的")

    def stop(self) -> None:
        """停止监控线程。"""
        self._running = False
        logger.info("[监控] 已停止")

    def context_summary(self) -> str:
        """生成当前持仓和 PA 分析的上下文文本，供 Gemini 回复时参考。"""
        if not self._levels:
            return ""
        lines = [
            "以下是用户当前的持仓和 PA 分析结果，回答股票/仓位相关问题时参考：",
            "─" * 40,
        ]
        for symbol, level in self._levels.items():
            name = level["display_name"]
            cs = level["currency_sign"]
            sig = level.get("signal", "")
            strength = level.get("signal_strength", 0)
            support = level.get("support")
            resistance = level.get("resistance")
            shares = level.get("shares")
            avg_cost = level.get("avg_cost")
            pd = level.get("pa_detail", {})

            trend = pd.get("trend", "")
            last_close = pd.get("last_close")
            reasons = pd.get("reasons", [])
            diagnosis = pd.get("diagnosis", "")
            atr = pd.get("atr")
            trailing_stop = pd.get("trailing_stop")
            limit_entry = pd.get("limit_entry")
            pin_bar = pd.get("pin_bar", "")
            engulfing = pd.get("engulfing", "")
            consec = pd.get("consecutive_count", 0)
            consec_dir = pd.get("consecutive_dir", "")
            live_price = pd.get("live_price")

            lines.append(f"{name} ({symbol}):")
            if shares is not None or avg_cost is not None:
                parts = []
                if shares is not None:
                    parts.append(f"持仓 {shares} 股")
                if avg_cost is not None:
                    parts.append(f"均价 {fmt_price(avg_cost, cs)}")
                if parts:
                    lines.append(f"  仓位信息: {'，'.join(parts)}")
            if last_close is not None:
                lines.append(f"  昨收盘: {fmt_price(last_close, cs)}")
            if live_price is not None:
                pct = pd.get("live_price_change_pct")
                pct_str = f" ({pct:+.2f}%)" if pct is not None else ""
                lines.append(f"  最新价: {fmt_price(live_price, cs)}{pct_str}")
            lines.append(f"  趋势: {trend}")
            if support is not None:
                lines.append(f"  支撑位: {fmt_price(support, cs)}")
            if resistance is not None:
                lines.append(f"  阻力位: {fmt_price(resistance, cs)}")
            lines.append(f"  PA 信号: {sig}（强度{strength}）")
            if diagnosis:
                lines.append(f"  诊断: {diagnosis}")
            if reasons:
                lines.append(f"  原因: {'，'.join(reasons)}")
            if pin_bar:
                lines.append(f"  形态: {'看涨' if pin_bar == 'bullish' else '看跌'}Pin Bar")
            if engulfing:
                lines.append(f"  形态: {'看涨' if engulfing == 'bullish' else '看跌'}吞没")
            if consec >= 3:
                lines.append(f"  连{consec_dir}{consec}天")
            if atr is not None:
                lines.append(f"  ATR(14): {fmt_price(atr, cs)}")
            if trailing_stop is not None:
                lines.append(f"  追踪止损: {fmt_price(trailing_stop, cs)}")
            if limit_entry is not None:
                lines.append(f"  限价买入: {fmt_price(limit_entry, cs)}")
            lines.append("")

        lines.append("请基于以上数据回答用户的持仓相关问题，给出具体的操作建议（买入/卖出/持有、价位、仓位比例等）。")
        lines.append("如果用户问的是不相关的话题，正常聊天即可。")
        return "\n".join(lines)

    def _run_loop(self) -> None:
        """后台线程主循环。"""
        while self._running:
            try:
                self._check_and_alert()
            except Exception as e:
                logger.error(f"[监控] 检查异常: {e}", exc_info=True)

            # 等待下一轮
            for _ in range(MONITOR_INTERVAL * 60):
                if not self._running:
                    break
                time.sleep(1)

    def _check_and_alert(self) -> None:
        """一轮价格检查：遍历所有标的，拉实时价，比对关键位。"""
        # 每日重置
        today = date.today()
        if today != self._today:
            self._today = today
            self._daily_count.clear()
            # 状态不重置——跨日的突破应该重新提醒

        for symbol, level in list(self._levels.items()):
            if not self._running:
                break

            try:
                self._check_one(symbol, level)
            except Exception as e:
                logger.warning(f"[监控] {symbol} 检查失败: {e}")

            # 避免 Yahoo 限流
            time.sleep(max(YF_REQUEST_DELAY, 2))

    def _check_one(self, symbol: str, level: dict) -> None:
        """检查一个标的的实时价格。"""
        data = fetch_premarket_price(symbol)
        if not data:
            return

        # 选择最合适的实时价格：盘前 > 盘后 > 盘中
        price = (
            data.get("pre_market_price")
            or data.get("post_market_price")
            or data.get("regular_market_price")
        )
        if not price:
            return

        support = level.get("support")
        resistance = level.get("resistance")
        name = level["display_name"]
        cs = level.get("currency_sign", "$")

        with self._lock:
            state = self._states.get(symbol, {})
            if state is None:
                state = {"support": STATUS_NORMAL, "resistance": STATUS_NORMAL}
                self._states[symbol] = state

            prev_change = data.get("pre_market_change_pct")

            # ── 检查支撑（下跌破位） ──────────────────────────────
            if support and support > 0:
                threshold = support * (1 - MONITOR_BUFFER_PCT)
                if price < threshold:
                    if state["support"] != STATUS_BELOW_SUPPORT:
                        # 状态变化：之前不在支撑下方 → 现在跌破了
                        pct = (price - support) / support * 100
                        if self._mark_alerted(symbol):
                            self._send_break_alert(
                                symbol, name, cs, "跌破支撑",
                                price, support, pct, prev_change, level
                            )
                        state["support"] = STATUS_BELOW_SUPPORT
                else:
                    state["support"] = STATUS_NORMAL

            # ── 检查阻力（上涨突破） ──────────────────────────────
            if resistance and resistance > 0:
                threshold = resistance * (1 + MONITOR_BUFFER_PCT)
                if price > threshold:
                    if state["resistance"] != STATUS_ABOVE_RESISTANCE:
                        # 状态变化：之前不在阻力上方 → 现在突破了
                        pct = (price - resistance) / resistance * 100
                        if self._mark_alerted(symbol):
                            self._send_break_alert(
                                symbol, name, cs, "突破阻力",
                                price, resistance, pct, prev_change, level
                            )
                        state["resistance"] = STATUS_ABOVE_RESISTANCE
                else:
                    state["resistance"] = STATUS_NORMAL

    def _mark_alerted(self, symbol: str) -> bool:
        """检查是否允许发送提醒，允许则计数+1。返回 True 表示可以发。"""
        if self._daily_count.get(symbol, 0) >= MONITOR_MAX_DAILY_ALERTS:
            logger.info(f"[监控] {symbol} 今日提醒已达上限 ({MONITOR_MAX_DAILY_ALERTS})，跳过")
            return False
        self._daily_count[symbol] = self._daily_count.get(symbol, 0) + 1
        return True

    def _send_break_alert(self, symbol: str, name: str, cs: str,
                          event: str, price: float, key_level: float,
                          pct: float, prev_change: Optional[float],
                          level: dict) -> None:
        """发送突破提醒消息。"""
        signal = level.get("signal", "")
        strength = level.get("signal_strength", 0)
        price_str = fmt_price(price, cs)
        level_str = fmt_price(key_level, cs)

        # 涨跌箭头
        arrow = "▲" if pct > 0 else "▼"
        pct_str = f"{pct:+.2f}%"

        # 信号标签
        signal_label = ""
        if signal:
            emoji = {"加仓": "🟢", "减仓": "🔴", "观望": "🟡"}
            sig_emoji = emoji.get(signal, "⚪")
            signal_label = f"\n盘前信号：{sig_emoji} {signal}（强度{strength}）"

        # 盘前涨跌幅
        pre_change_str = ""
        if prev_change is not None:
            pm_arrow = "▲" if prev_change > 0 else "▼"
            pre_change_str = f"\n盘前涨跌：{pm_arrow} {abs(prev_change):.2f}%"

        # 建议
        suggestion = ""
        if "跌破" in event:
            suggestion = "\n💡 建议：注意风控，考虑减仓或设止损"
        elif "突破" in event:
            suggestion = "\n💡 建议：观察能否站稳，可考虑分批加仓"

        msg = (
            f"🔔 <b>价格{event}</b>  {datetime.now().strftime('%H:%M')}\n"
            f"─" * 20 + "\n"
            f"<b>{name}</b>  {price_str}  {arrow}{pct_str}\n"
            f"关键位：{level_str}"
            f"{signal_label}"
            f"{pre_change_str}"
            f"{suggestion}\n"
            f"今日提醒：{self._daily_count.get(symbol, 0)}/{MONITOR_MAX_DAILY_ALERTS}"
        )

        send_telegram(msg)
        logger.info(f"[监控] → {name} {event}，当前价={price_str}，关键位={level_str}")
