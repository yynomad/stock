"""
输出模块
─────────────────────────────────────────────────────────────
  · build_console_table     控制台表格输出
  · build_telegram_message  Telegram HTML 消息构建
  · send_telegram           Telegram Bot API 推送
"""

import logging
from datetime import datetime

from .config import TG_BOT_TOKEN, TG_CHAT_ID, YF_PROXY, PA_CONSECUTIVE_DAYS
from .format_utils import fmt_price


# ══════════════════════════════════════════════════════════════════════
#  控制台表格输出
# ══════════════════════════════════════════════════════════════════════

def build_console_table(results: list) -> str:
    W = 110
    now_str = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")

    def hline(l, m, r, c="═"):
        return f"{l}{c * (W - 2)}{r}"

    lines = []
    lines.append(hline("╔", "═", "╗"))
    lines.append(f"║{'📊  盘前 Price Action 策略报告 v3.0':^{W-2}}║")
    lines.append(f"║{'数据源：Yahoo Finance | 运行时间：' + now_str:^{W-5}}║")
    lines.append(hline("╠", "═", "╣"))

    lines.append(
        f"║  {'标的':<20} {'收盘价':>10} {'趋势':>5} "
        f"{'阻力位':>11} {'支撑位':>11} {'信号':>4} {'强度':>2} "
        f"{'形态':<16}  ║"
    )
    lines.append(hline("╠", "═", "╣", "─"))

    for i, r in enumerate(results):
        sep = hline("║", "─", "║", "─") if i < len(results) - 1 else ""

        if r.get("error"):
            lines.append(
                f"║  {r['display_name']:<20}  "
                f"{'⚠️  ' + r['error']:<{W-26}}  ║"
            )
        else:
            pa = r.get("pa", {})
            signal_emoji = {"加仓": "🟢", "减仓": "🔴", "观望": "🟡"}
            sig = signal_emoji.get(pa.get("signal", ""), "⚪")

            trend_map = {"up": "↑", "down": "↓", "sideways": "→"}
            trend_icon = trend_map.get(pa.get("trend", ""), "→")

            patterns = []
            if pa.get("pin_bar") == "bearish":
                patterns.append("看跌Pin")
            elif pa.get("pin_bar") == "bullish":
                patterns.append("看涨Pin")
            if pa.get("engulfing") == "bearish":
                patterns.append("看跌吞没")
            elif pa.get("engulfing") == "bullish":
                patterns.append("看涨吞没")
            if pa.get("consecutive_count", 0) >= PA_CONSECUTIVE_DAYS:
                patterns.append(f"连{pa['consecutive_dir']}{pa['consecutive_count']}天")
            pattern_str = "+".join(patterns) if patterns else "—"

            lines.append(
                f"║  {r['display_name']:<20} "
                f"{pa.get('last_close_fmt',''):>10} "
                f"{trend_icon:>5} "
                f"{pa.get('resistance_fmt','N/A'):>11} "
                f"{pa.get('support_fmt','N/A'):>11} "
                f"{sig + pa.get('signal',''):>5} "
                f"{pa.get('signal_strength',0):>2} "
                f"{pattern_str:<16}  ║"
            )

            # 关键价位行
            if pa.get("key_levels"):
                lines.append(
                    f"║  {'':4}{pa['key_levels']:<{W-6}}║"
                )

        if sep:
            lines.append(sep)

    lines.append(hline("╠", "═", "╣"))
    lines.append(f"║  {'🟢 加仓   🟡 观望   🔴 减仓 | 强度1=弱 2=中 3=强':^{W-4}}  ║")
    lines.append(f"║  {'阻力=近30日最高价 | 支撑=近30日最低价 | 信号=趋势+位置+形态综合评分':^{W-4}}  ║")
    lines.append(hline("╚", "═", "╝"))

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
#  Telegram 消息构建
# ══════════════════════════════════════════════════════════════════════

def _esc(s: str) -> str:
    """HTML 转义辅助。"""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_telegram_message(results: list) -> str:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 段一：数据表格
    table_lines = [
        "📊 <b>盘前 PA 策略报告 v3.0</b>",
        f"🕐 {now_str}  |  数据源：Yahoo Finance",
        "",
        "<pre>",
        f"{'标的':<7} {'收盘':>10} {'趋势':>4} {'信号':>6} {'阻力':>10} {'支撑':>10}",
        "─" * 52,
    ]

    for r in results:
        if r.get("error"):
            table_lines.append(f"{r['symbol']:<7}  ⚠️ {r['error']}")
        else:
            pa = r.get("pa", {})
            trend_map = {"up": "↑", "down": "↓", "sideways": "→"}
            t = trend_map.get(pa.get("trend", ""), "→")
            sig_emoji = {"加仓": "🟢", "减仓": "🔴", "观望": "🟡"}
            sig = sig_emoji.get(pa.get("signal", ""), "⚪") + pa.get("signal", "")
            table_lines.append(
                f"{r['symbol']:<7}"
                f"{pa.get('last_close_fmt',''):>10} "
                f"{t:>4} "
                f"{sig:>6} "
                f"{pa.get('resistance_fmt','N/A'):>10} "
                f"{pa.get('support_fmt','N/A'):>10}"
            )

    table_lines.append("─" * 52)
    table_lines.append("🟢加仓 🟡观望 🔴减仓")
    table_lines.append("</pre>")

    # 段二：逐标的详细诊断
    diag_lines = ["", "📋 <b>今日PA诊断</b>", ""]
    has_reduce = False

    for r in results:
        sym = r["symbol"]
        display = _esc(r["display_name"])
        pa = r.get("pa", {})
        signal = pa.get("signal", "")

        if signal == "减仓":
            has_reduce = True
            diag_lines.append(f"🔴 <b>{display}</b>")
        elif signal == "加仓":
            diag_lines.append(f"🟢 <b>{display}</b>")
        else:
            diag_lines.append(f"🟡 <b>{display}</b>")

        # 诊断文字
        diag = _esc(pa.get("diagnosis", "数据不足"))
        diag_lines.append(f"   {diag}")

        # 关键价位
        if pa.get("key_levels"):
            diag_lines.append(f"   {_esc(pa['key_levels'])}")

        # 建议原因逐条列出
        reasons = pa.get("reasons", [])
        if reasons:
            diag_lines.append("   📎 原因：")
            for reason in reasons:
                diag_lines.append(f"     · {_esc(reason)}")

        # 限价单 + 追踪止损
        trailing_stop = pa.get("trailing_stop")
        limit_entry = pa.get("limit_entry")
        target_price = pa.get("target_price")
        atr_val = pa.get("atr")
        trailing_stop_pct = pa.get("trailing_stop_pct")
        recent_high = pa.get("recent_high")
        live_price = pa.get("live_price")
        live_price_change = pa.get("live_price_change_pct")
        limit_entry_note = pa.get("limit_entry_note")
        pre_market_change = pa.get("pre_market_change_pct")

        if any(v is not None for v in [limit_entry, trailing_stop, target_price, live_price]):
            cs = r.get('currency_sign', '$')
            diag_lines.append("   📊 交易计划：")
            # 盘前价格
            if live_price is not None:
                change_str = f" ({live_price_change:+.2f}%)" if live_price_change is not None else ""
                diag_lines.append(f"     🔴 盘前价 {fmt_price(live_price, cs)}{change_str}")
            if pre_market_change is not None:
                diag_lines.append(f"     盘前涨跌 {pre_market_change:+.2f}%")
            if limit_entry is not None:
                note = f" ({limit_entry_note})" if limit_entry_note else ""
                diag_lines.append(f"     限价买入 {fmt_price(limit_entry, cs)}{note}")
            if target_price is not None:
                diag_lines.append(f"     目标价 {fmt_price(target_price, cs)}")
            if trailing_stop is not None:
                pct_str_ts = f"({trailing_stop_pct:+.2f}%)" if trailing_stop_pct is not None else ""
                diag_lines.append(f"     追踪止损 {fmt_price(trailing_stop, cs)} {pct_str_ts}")
            if atr_val is not None:
                diag_lines.append(f"     ATR(14) {fmt_price(atr_val, cs)}")
            if recent_high is not None:
                diag_lines.append(f"     20日最高 {fmt_price(recent_high, cs)}")

        # Pin Bar 细节
        if pa.get("pin_bar") == "bearish":
            diag_lines.append("   ⚡ 昨日出现看跌Pin Bar（长上影线，多头动能衰竭）")
        elif pa.get("pin_bar") == "bullish":
            diag_lines.append("   ⚡ 昨日出现看涨Pin Bar（长下影线，空头动能衰竭）")

        # Engulfing 细节
        if pa.get("engulfing") == "bearish":
            diag_lines.append("   ⚡ 昨日看跌吞没形态")
        elif pa.get("engulfing") == "bullish":
            diag_lines.append("   ⚡ 昨日看涨吞没形态")

        diag_lines.append("")

    if has_reduce:
        diag_lines.insert(
            2,
            "⚠️ <b>提醒：检测到减仓信号，建议分批止盈或暂停加仓。</b>\n"
        )

    return "\n".join(table_lines + diag_lines)


# ══════════════════════════════════════════════════════════════════════
#  Telegram 推送
# ══════════════════════════════════════════════════════════════════════

def send_telegram(message: str) -> bool:
    """使用 requests 同步发送 Telegram HTML 消息。"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️  [Telegram] 未配置 TG_BOT_TOKEN 或 TG_CHAT_ID，跳过推送。")
        logging.warning("TG_BOT_TOKEN 或 TG_CHAT_ID 未配置")
        return False
    try:
        import requests
        proxies = None
        if YF_PROXY:
            proxies = {"http": YF_PROXY, "https": YF_PROXY}
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id":    TG_CHAT_ID,
            "text":       message,
            "parse_mode": "HTML",
        }
        resp = requests.post(url, json=payload, timeout=15, proxies=proxies)
        if resp.status_code == 200:
            print("✅ [Telegram] 消息推送成功。")
            return True
        else:
            err_msg = f"HTTP {resp.status_code}：{resp.text[:300]}"
            print(f"❌ [Telegram] 推送失败，{err_msg}")
            logging.error(f"Telegram 推送失败：{err_msg}")
            return False
    except Exception as e:
        print(f"❌ [Telegram] 推送异常：{e}")
        logging.error(f"Telegram 推送异常：{e}")
        return False
