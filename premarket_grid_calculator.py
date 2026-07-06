"""
盘前 Price Action 策略计算器
─────────────────────────────────────────────────────────────
入口文件：仅负责串联流程，业务逻辑都在 lib/ 模块下。

模块结构：
  lib/config.py       配置与监控标的
  lib/format_utils.py 价格/百分比格式化
  lib/yahoo_data.py   Yahoo Finance 数据抓取
  lib/positions.py    持仓获取（IBKR + Vision + 静态降级）
  lib/pa_strategy.py  Price Action 分析引擎
  lib/output.py       控制台表格 + Telegram 推送

运行：python premarket_grid_calculator.py
"""

import sys
import time
import logging

from lib.config import YF_REQUEST_DELAY, LIMIT_ENTRY_PCT
from lib.format_utils import fmt_price
from lib.yahoo_data import fetch_yahoo_bars, fetch_premarket_price
from lib.pa_strategy import analyze_pa
from lib.positions import fetch_positions
from lib.position_decision import build_position_decision
from lib.output import build_console_table, build_telegram_message, send_telegram
from lib.watchlist import WatchlistManager


logging.info("=" * 60)
logging.info("脚本启动 (Yahoo Finance, modular)")


def _adjust_with_premarket(pa_result: dict, premarket: dict, currency_sign: str) -> None:
    """用盘前价格修正限价单 / 追踪止损，原地修改 pa_result。"""
    if not premarket:
        return

    live_price = (
        premarket.get("pre_market_price")
        or premarket.get("post_market_price")
        or premarket.get("regular_market_price")
    )
    prev_close = premarket.get("previous_close")

    if live_price and live_price != pa_result.get("last_close"):
        pa_result["live_price"] = round(live_price, 2)
        if prev_close and prev_close > 0:
            pa_result["live_price_change_pct"] = round(
                (live_price - prev_close) / prev_close * 100, 2
            )

        # 修正限价单：取支撑位与盘前价中较低者，再加 0.5% 偏移
        support = pa_result.get("support")
        if support and pa_result.get("signal") in ("加仓", "观望"):
            base = min(support, live_price)
            pa_result["limit_entry"] = round(base * (1 + LIMIT_ENTRY_PCT), 2)
            pa_result["limit_entry_note"] = (
                f"基于盘前价 {fmt_price(live_price, currency_sign)} 修正"
            )

        # 修正追踪止损百分比
        trailing_stop = pa_result.get("trailing_stop")
        if trailing_stop and live_price > 0:
            pa_result["trailing_stop_pct"] = round(
                (trailing_stop - live_price) / live_price * 100, 2
            )

    # 记录盘前涨跌幅
    pm_change = premarket.get("pre_market_change_pct")
    if pm_change is not None:
        pa_result["pre_market_change_pct"] = (
            round(pm_change * 100, 2) if abs(pm_change) < 1 else round(pm_change, 2)
        )


def _format_display_fields(pa_result: dict, currency_sign: str) -> None:
    """补充控制台/Telegram 用到的格式化字段。"""
    for key in ("last_close", "resistance", "support", "ma_short", "ma_long"):
        val = pa_result.get(key)
        pa_result[f"{key}_fmt"] = fmt_price(val, currency_sign) if val is not None else "N/A"


def _build_symbols(image_path: str = None) -> dict:
    """确定分析标的列表。返回 {symbol: cfg} 字典。"""
    wm = WatchlistManager()
    if image_path:
        positions = fetch_positions(image_path)
        if not positions:
            print("⚠️  未从截图识别到持仓，使用监控列表")
            return {sym: dict(cfg) for sym, cfg in wm.get_all().items()}
        symbols = {}
        for sym, pos in positions.items():
            info = wm.get(sym)
            if info:
                cfg = dict(info)
            else:
                if sym.endswith(".T"):
                    currency_sign = "¥"
                elif sym.endswith(".SW"):
                    currency_sign = "€"
                elif sym.endswith(".SS") or sym.endswith(".SZ"):
                    currency_sign = "CN¥"
                else:
                    currency_sign = "$"
                cfg = {"currency_sign": currency_sign, "display_name": sym}
            cfg["shares"] = pos.get("shares")
            cfg["avg_cost"] = pos.get("avg_cost")
            symbols[sym] = cfg
        print(f"\n📊 将分析 {len(symbols)} 个持仓标的：{', '.join(symbols.keys())}")
        return symbols
    return {sym: dict(cfg) for sym, cfg in wm.get_all().items()}


def _analyze_symbols(symbols: dict) -> list:
    """核心分析循环：拉 K 线 → PA 分析 → 返回 results 列表。"""
    results = []

    for idx, (symbol, cfg) in enumerate(symbols.items()):
        display_name = cfg.get("display_name", symbol)
        currency_sign = cfg.get("currency_sign", "$")

        if idx > 0 and YF_REQUEST_DELAY > 0:
            print(f"  ⏳ 等待 {YF_REQUEST_DELAY}s 避免限流...")
            time.sleep(YF_REQUEST_DELAY)

        print(f"\n🔍 处理标的：{display_name} ({symbol})")

        row = {
            "symbol":        symbol,
            "display_name":  display_name,
            "currency_sign": currency_sign,
            "pa":            {},
            "error":         None,
            "shares":        cfg.get("shares"),
            "avg_cost":      cfg.get("avg_cost"),
        }

        bars = fetch_yahoo_bars(symbol)
        if not bars:
            row["error"] = "K线数据获取失败"
            results.append(row)
            logging.warning(f"{symbol}：K线数据获取失败")
            continue

        premarket = fetch_premarket_price(symbol)
        row["premarket"] = premarket

        pa_result = analyze_pa(bars, currency_sign)
        row["pa"] = pa_result

        _adjust_with_premarket(pa_result, premarket, currency_sign)
        _format_display_fields(pa_result, currency_sign)
        row["decision"] = build_position_decision(row)

        results.append(row)
        print(f"  {pa_result.get('diagnosis', '')}\n")

        if pa_result.get("pa_error"):
            logging.warning(f"{symbol}：{pa_result['pa_error']}")
        else:
            logging.info(
                f"{symbol} 收盘={pa_result.get('last_close_fmt','')} "
                f"趋势={pa_result.get('trend','')} "
                f"信号={pa_result.get('signal','')}({pa_result.get('signal_strength',0)}) "
                f"阻力={pa_result.get('resistance_fmt','')} "
                f"支撑={pa_result.get('support_fmt','')}"
            )

    return results


def run_calculator(image_path: str = None, symbols: dict = None) -> tuple:
    """主流程：确定标的 → 分析 → 输出 → 返回 (results, report)。

    参数：
      image_path: 持仓截图路径（与 symbols 二选一）
      symbols:    预构建的 {symbol: cfg} 字典（优先级高于 image_path）
    返回：
      (results, report_text)
    """
    try:
        import yfinance  # noqa: F401
    except ImportError:
        print("❌ 缺少依赖库 yfinance，请执行：pip install yfinance")
        sys.exit(1)

    if symbols is None:
        symbols = _build_symbols(image_path)

    results = _analyze_symbols(symbols)

    if not results:
        print("⚠️  没有获取到任何标的数据。")
        sys.exit(1)

    # 控制台输出
    print()
    print(build_console_table(results))
    print()

    # 构建报告
    report = build_telegram_message(results)

    logging.info("脚本正常结束")
    print("✅ 完成。")

    return results, report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="盘前 Price Action 策略计算器")
    parser.add_argument("--image", type=str, help="持仓截图路径（Gemini Vision 识别）")
    args = parser.parse_args()
    results, report = run_calculator(image_path=args.image)
    send_telegram(report)
