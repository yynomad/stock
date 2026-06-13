"""
Yahoo Finance 数据获取模块
─────────────────────────────────────────────────────────────
  · fetch_yahoo_bars      日K线（带重试 + 代理 + 限流退避）
  · fetch_premarket_price 盘前/盘后实时价格（轻量调用）
"""

import os
import logging
import time

from .config import YF_PROXY, YF_REQUEST_DELAY


def fetch_yahoo_bars(symbol: str, period: str = None, retries: int = 3) -> list[dict]:
    """通过 yfinance 获取日K线数据，带重试机制。"""
    try:
        import yfinance as yf
    except ImportError:
        print("❌ 缺少依赖库 yfinance，请执行：pip install yfinance")
        return []

    if period is None:
        period = "5mo"

    for attempt in range(1, retries + 1):
        try:
            print(f"  📥 [{symbol}] 正在拉取 Yahoo Finance 日K线 (period={period})..."
                  f"{'  [重试 ' + str(attempt) + '/' + str(retries) + ']' if attempt > 1 else ''}")

            # 如果配置了代理，设置环境变量
            if YF_PROXY:
                os.environ["HTTP_PROXY"] = YF_PROXY
                os.environ["HTTPS_PROXY"] = YF_PROXY

            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, auto_adjust=True)

            if df.empty:
                print(f"  ⚠️  [{symbol}] 未获取到数据。")
                if attempt < retries:
                    print(f"  ⏳ 等待 {YF_REQUEST_DELAY}s 后重试...")
                    time.sleep(YF_REQUEST_DELAY)
                    continue
                return []

            bars = []
            for idx, row in df.iterrows():
                bars.append({
                    "date":   idx.strftime("%Y-%m-%d"),
                    "open":   float(row["Open"]),
                    "high":   float(row["High"]),
                    "low":    float(row["Low"]),
                    "close":  float(row["Close"]),
                    "volume": int(row["Volume"]),
                })

            print(f"  ✅ [{symbol}] 获取到 {len(bars)} 根K线，"
                  f"最新日期：{bars[-1]['date']}，收盘：{bars[-1]['close']:.2f}")
            return bars

        except Exception as e:
            print(f"  ❌ [{symbol}] 第{attempt}次获取失败：{e}")
            logging.error(f"[{symbol}] 第{attempt}次获取失败：{e}")
            if attempt < retries:
                wait = YF_REQUEST_DELAY * attempt  # 递增等待
                print(f"  ⏳ 等待 {wait}s 后重试...")
                time.sleep(wait)

    return []


def fetch_premarket_price(symbol: str, retries: int = 2) -> dict:
    """获取盘前/盘后最新价格（轻量方案）。"""
    try:
        import yfinance as yf
    except ImportError:
        return None

    for attempt in range(1, retries + 1):
        try:
            if YF_PROXY:
                os.environ["HTTP_PROXY"] = YF_PROXY
                os.environ["HTTPS_PROXY"] = YF_PROXY

            ticker = yf.Ticker(symbol)
            info = ticker.info

            result = {
                "regular_market_price": info.get("regularMarketPrice"),
                "pre_market_price": info.get("preMarketPrice"),
                "pre_market_change_pct": info.get("preMarketChangePercent"),
                "post_market_price": info.get("postMarketPrice"),
                "post_market_change_pct": info.get("postMarketChangePercent"),
                "previous_close": info.get("previousClose"),
            }

            # 只在有盘前/盘后数据时返回
            if result["pre_market_price"] or result["post_market_price"]:
                return result

            # 退回到 regularMarketPrice
            if result["regular_market_price"]:
                return result

            if attempt < retries:
                time.sleep(2)

        except Exception as e:
            logging.warning(f"[{symbol}] 盘前价格获取失败（第{attempt}次）：{e}")
            if attempt < retries:
                time.sleep(2)

    return None
