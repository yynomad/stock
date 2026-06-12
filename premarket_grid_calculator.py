"""
╔══════════════════════════════════════════════════════════════════════╗
║       盘前 Price Action 策略计算器 v3.0                              ║
║       Pure Price Action · Yahoo Finance Data                        ║
║                                                                      ║
║  核心变更（vs v2.0）：                                                ║
║    · 数据源：IBKR API → Yahoo Finance (yfinance)                    ║
║    · 判断依据：持仓+均价 → 纯K线 Price Action                       ║
║    · 输出：加仓/减仓信号 + 关键价位                                   ║
║                                                                      ║
║  PA 策略逻辑：                                                        ║
║    1. 趋势判断：均线 + 高低点结构                                     ║
║    2. 支撑/阻力：近期 swing high/low                                  ║
║    3. K线形态：Pin Bar / Engulfing / 连续方向K线                      ║
║    4. 综合信号：趋势+位置+形态 → 加仓/减仓/观望                       ║
║                                                                      ║
║  风控声明：本程序仅提供静态分析与建议，不包含任何下单指令。            ║
║  运行方式：每日美股开盘前单次执行。                                    ║
╚══════════════════════════════════════════════════════════════════════╝

依赖安装：
    pip install yfinance python-dotenv requests

使用方式：
    python premarket_grid_calculator.py
"""

import sys
import os
import logging
from datetime import datetime
from dotenv import load_dotenv

# ── 加载 .env 配置文件 ─────────────────────────────────────────────────
load_dotenv()

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID   = os.getenv("TG_CHAT_ID", "")

# ── Yahoo Finance 代理配置（国内网络可能需要）───────────────────────────
YF_PROXY = os.getenv("YF_PROXY", "")  # 如 http://127.0.0.1:7890

# ── IBKR Flex Query 持仓获取配置（主方案）────────────────────────────
IBKR_FLEX_TOKEN = os.getenv("IBKR_FLEX_TOKEN", "")   # Flex Web Service Token
IBKR_FLEX_QUERY_ID = os.getenv("IBKR_FLEX_QUERY_ID", "")  # Position Query ID

# ── 大模型读图配置（降级方案）───────────────────────────────────────
VISION_API_URL = os.getenv("VISION_API_URL", "https://ark.cn-beijing.volces.com/api/v3/chat/completions")
VISION_API_KEY = os.getenv("VISION_API_KEY", "")
VISION_MODEL = os.getenv("VISION_MODEL", "doubao-1-5-vision-pro-32k")
POSITION_IMAGE_PATH = os.getenv("POSITION_IMAGE_PATH", "positions.jpg")  # 持仓截图路径

# ── 请求间隔（秒），避免触发 Yahoo 限流 ──────────────────────────────
YF_REQUEST_DELAY = float(os.getenv("YF_REQUEST_DELAY", "5"))

# ── 日志初始化 ────────────────────────────────────────────────────────
LOG_FILE = "premarket_calculator.log"
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.info("=" * 60)
logging.info("脚本启动 (v3.0 Yahoo Finance)")

# ══════════════════════════════════════════════════════════════════════
#  PA 策略参数配置
# ══════════════════════════════════════════════════════════════════════
PA_KLINE_LOOKBACK      = 60    # 获取多少根日K线
PA_TREND_MA_SHORT      = 10    # 短期均线周期
PA_TREND_MA_LONG       = 30    # 长期均线周期
PA_SWING_LOOKBACK      = 10    # swing high/low 回望窗口
PA_NEAR_LEVEL_PCT      = 0.02  # 距关键位 ≤ 2% 视为"接近"
PA_PIN_UPPER_RATIO     = 2.0   # 上影线/实体倍数阈值（看跌Pin Bar）
PA_PIN_LOWER_RATIO     = 2.0   # 下影线/实体倍数阈值（看涨Pin Bar）
PA_ENGULFING_MIN_BODY  = 0.3   # Engulfing 实体占振幅最小比例
PA_CONSECUTIVE_DAYS    = 3     # 连续同向K线天数阈值

# ── ATR 追踪止损参数 ────────────────────────────────────────────────
ATR_PERIOD             = 14    # ATR 计算周期
ATR_STOP_MULTIPLIER    = 2.0   # 追踪止损 = 最高价 - N倍ATR
LIMIT_ENTRY_PCT        = 0.005 # 限价单距支撑的偏移（0.5%）

# ══════════════════════════════════════════════════════════════════════
#  监控标的配置表
#  不再依赖持仓，WATCHLIST 即为分析范围
#  1321.T 是 Yahoo Finance 上东京证券交易所的日经225 ETF 代码
# ══════════════════════════════════════════════════════════════════════
WATCHLIST = {
    "QQQ": {
        "currency_sign": "$",
        "display_name":  "QQQ  纳指100 ETF",
        "shares": None,
        "avg_cost": None,
    },
    "1321.T": {
        "currency_sign": "¥",
        "display_name":  "1321 野村日经225",
        "shares": None,
        "avg_cost": None,
    },
    "EWY": {
        "currency_sign": "$",
        "display_name":  "EWY  韩国指数 ETF",
        "shares": None,
        "avg_cost": None,
    },
    "GOOG": {
        "currency_sign": "$",
        "display_name":  "GOOG 谷歌",
        "shares": None,
        "avg_cost": None,
    },
    "NVDA": {
        "currency_sign": "$",
        "display_name":  "NVDA 英伟达",
        "shares": None,
        "avg_cost": None,
    },
    "AAPL": {
        "currency_sign": "$",
        "display_name":  "AAPL 苹果",
        "shares": None,
        "avg_cost": None,
    },
    "RDDT": {
        "currency_sign": "$",
        "display_name":  "RDDT Reddit",
        "shares": None,
        "avg_cost": None,
    },
    "MRVL": {
        "currency_sign": "$",
        "display_name":  "MRVL Marvell",
        "shares": None,
        "avg_cost": None,
    },
    "DELL": {
        "currency_sign": "$",
        "display_name":  "DELL 戴尔",
        "shares": None,
        "avg_cost": None,
    },
    "NOK": {
        "currency_sign": "$",
        "display_name":  "NOK 诺基亚",
        "shares": None,
        "avg_cost": None,
    },
    "IBKR": {
        "currency_sign": "$",
        "display_name":  "IBKR 盈透",
        "shares": None,
        "avg_cost": None,
    },
    "XFAB.SW": {
        "currency_sign": "€",
        "display_name":  "XFAB",
        "shares": None,
        "avg_cost": None,
    },
}


# ══════════════════════════════════════════════════════════════════════
#  模块一：工具函数
# ══════════════════════════════════════════════════════════════════════

def fmt_price(price: float, sign: str, decimals: int = 2) -> str:
    if sign == "¥":
        return f"¥{price:,.0f}"
    return f"{sign}{price:,.{decimals}f}"


def pct_str(val: float) -> str:
    sign = "+" if val >= 0 else ""
    return f"{sign}{val*100:.2f}%"


# ══════════════════════════════════════════════════════════════════════
#  模块二：Yahoo Finance K线获取
# ══════════════════════════════════════════════════════════════════════

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
                    import time
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
                import time
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
                import time
                time.sleep(2)

        except Exception as e:
            logging.warning(f"[{symbol}] 盘前价格获取失败（第{attempt}次）：{e}")
            if attempt < retries:
                import time
                time.sleep(2)

    return None


# ══════════════════════════════════════════════════════════════════════
#  模块三：持仓数据获取（Flex Query API + 大模型读图）
# ══════════════════════════════════════════════════════════════════════

def fetch_positions_from_ibkr() -> dict:
    """
    主方案：通过 IBKR Flex Query HTTP API 获取持仓。
    
    Flex Query 不占用 TWS/Gateway API 会话，不影响手机登录。
    配置方法：Account Management → Flex Queries → 创建 Position 查询
    
    返回：{symbol: {"shares": float, "avg_cost": float}} 或 None
    """
    if not IBKR_FLEX_TOKEN or not IBKR_FLEX_QUERY_ID:
        print("  ℹ️  IBKR Flex Query 未配置，跳过 API 获取持仓")
        return None
    
    try:
        import requests
        proxies = {"http": YF_PROXY, "https": YF_PROXY} if YF_PROXY else None
        
        url = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.SendRequest"
        params = {
            "t": IBKR_FLEX_TOKEN,
            "q": IBKR_FLEX_QUERY_ID,
            "v": "3",
        }
        resp = requests.get(url, params=params, timeout=30, proxies=proxies)
        if resp.status_code != 200:
            print(f"  ⚠️  IBKR Flex Query 请求失败: HTTP {resp.status_code}")
            return None
        
        # 解析响应获取 query_id
        import xml.etree.ElementTree as ET
        root = ET.fromstring(resp.text)
        status = root.find("Status")
        if status is not None and status.text == "Warn":
            error_code = root.find("ErrorCode")
            error_msg = root.find("ErrorMessage")
            print(f"  ⚠️  IBKR Flex Query 错误: {error_code.text if error_code is not None else ''} {error_msg.text if error_msg is not None else ''}")
            return None
        
        query_id_el = root.find("QueryId")
        if query_id_el is None:
            print("  ⚠️  IBKR Flex Query 未返回 QueryId")
            return None
        
        query_id = query_id_el.text
        print(f"  📡 IBKR Flex Query 已提交，QueryId: {query_id}")
        
        # 等待后拉取结果
        import time
        time.sleep(3)
        
        result_url = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement"
        result_params = {"q": query_id, "t": IBKR_FLEX_TOKEN, "v": "3"}
        result_resp = requests.get(result_url, params=result_params, timeout=30, proxies=proxies)
        if result_resp.status_code != 200:
            print(f"  ⚠️  IBKR Flex Query 结果获取失败: HTTP {result_resp.status_code}")
            return None
        
        # 解析 XML 持仓数据
        result_root = ET.fromstring(result_resp.text)
        positions = {}
        for pos in result_root.iter("OpenPosition"):
            symbol = pos.find("symbol")
            shares = pos.find("position")
            avg_price = pos.find("avgPrice")
            currency = pos.find("currency")
            
            if symbol is not None and shares is not None:
                sym = symbol.text
                # 跳过现金和非股票持仓
                asset_category = pos.find("assetCategory")
                if asset_category is not None and asset_category.text not in ("STK", "ETF"):
                    continue
                
                positions[sym] = {
                    "shares": float(shares.text) if shares.text else 0,
                    "avg_cost": float(avg_price.text) if avg_price is not None else None,
                    "currency": currency.text if currency is not None else "USD",
                }
        
        if positions:
            print(f"  ✅ IBKR Flex Query 获取到 {len(positions)} 个持仓")
            logging.info(f"IBKR Flex Query 获取到 {len(positions)} 个持仓")
            return positions
        else:
            print("  ⚠️  IBKR Flex Query 未获取到持仓数据")
            return None
            
    except Exception as e:
        print(f"  ❌ IBKR Flex Query 异常: {e}")
        logging.error(f"IBKR Flex Query 异常: {e}")
        return None


def fetch_positions_from_image(image_path: str) -> dict:
    """
    降级方案：通过大模型 Vision API 读取持仓截图。
    
    将图片 base64 编码后发送给支持视觉的 LLM，提取持仓信息。
    返回：{symbol: {"shares": float, "avg_cost": float}} 或 None
    """
    if not VISION_API_KEY:
        print("  ℹ️  Vision API Key 未配置，跳过大模型读图")
        return None
    
    if not os.path.exists(image_path):
        print(f"  ℹ️  持仓截图不存在: {image_path}")
        return None
    
    try:
        import requests
        import base64
        
        # 读取图片并 base64 编码
        with open(image_path, "rb") as f:
            img_data = f.read()
        img_b64 = base64.b64encode(img_data).decode("utf-8")
        
        # 判断图片类型
        ext = os.path.splitext(image_path)[1].lower()
        mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}
        mime_type = mime_map.get(ext, "image/jpeg")
        
        print(f"  🖼️  正在通过 Vision API 读取持仓截图...")
        
        proxies = {"http": YF_PROXY, "https": YF_PROXY} if YF_PROXY else None
        
        payload = {
            "model": VISION_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "请读取这张投资组合截图中的持仓信息，输出严格的 JSON 格式。\n"
                                "格式如下，不要输出其他内容：\n"
                                '{"positions": [{"symbol": "AAPL", "shares": None, "avg_cost": None}]}' + "\n"
                                "注意：\n"
                                "1. symbol 必须是股票代码（如 AAPL, NVDA, QQQ）\n"
                                "2. shares 是持仓数量（小数也可以，如 0.5）\n"
                                "3. avg_cost 是平均买入价格\n"
                                "4. 只输出 JSON，不要任何解释文字\n"
                                "5. ETF 也包含在内（如 QQQ, EWY, 1321）\n"
                                "6. 如果是日元标的，价格保持原数字\n"
                            )
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{img_b64}"
                            }
                        }
                    ]
                }
            ],
            "max_tokens": 2048,
            "temperature": 0.1,
        }
        
        headers = {
            "Authorization": f"Bearer {VISION_API_KEY}",
            "Content-Type": "application/json",
        }
        
        resp = requests.post(
            VISION_API_URL,
            json=payload,
            headers=headers,
            timeout=60,
            proxies=proxies,
        )
        
        if resp.status_code != 200:
            print(f"  ❌ Vision API 调用失败: HTTP {resp.status_code} - {resp.text[:200]}")
            logging.error(f"Vision API 调用失败: HTTP {resp.status_code}")
            return None
        
        # 解析响应
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        
        # 提取 JSON（可能被 markdown 代码块包裹）
        import json
        import re
        json_match = re.search(r'\{[\s\S]*\}', content)
        if not json_match:
            print(f"  ⚠️  Vision API 返回内容无法解析为 JSON: {content[:200]}")
            return None
        
        parsed = json.loads(json_match.group())
        positions = {}
        for item in parsed.get("positions", []):
            sym = item.get("symbol", "")
            if sym:
                positions[sym] = {
                    "shares": item.get("shares", 0),
                    "avg_cost": item.get("avg_cost"),
                }
        
        if positions:
            print(f"  ✅ Vision API 识别到 {len(positions)} 个持仓")
            logging.info(f"Vision API 识别到 {len(positions)} 个持仓: {list(positions.keys())}")
            return positions
        else:
            print("  ⚠️  Vision API 未识别到任何持仓")
            return None
            
    except Exception as e:
        print(f"  ❌ Vision API 读图异常: {e}")
        logging.error(f"Vision API 读图异常: {e}")
        return None


def fetch_positions() -> dict:
    """
    获取持仓数据，支持降级：
      1. 优先 IBKR Flex Query API
      2. 降级到大模型读图
      3. 都失败则使用 WATCHLIST 中的静态配置
    
    返回：{symbol: {"shares": float, "avg_cost": float}}
    """
    print("📋 获取持仓数据...")
    
    # 方案1: IBKR Flex Query
    positions = fetch_positions_from_ibkr()
    if positions:
        return positions
    
    # 方案2: 大模型读图
    positions = fetch_positions_from_image(POSITION_IMAGE_PATH)
    if positions:
        return positions
    
    # 方案3: 使用 WATCHLIST 中的静态配置
    print("  ℹ️  使用 WATCHLIST 静态持仓配置")
    static = {}
    for sym, cfg in WATCHLIST.items():
        if cfg.get("avg_cost"):
            static[sym] = {
                "shares": cfg.get("shares", 0),
                "avg_cost": cfg["avg_cost"],
            }
    return static if static else {}


# ══════════════════════════════════════════════════════════════════════
#  模块四：Price Action 分析引擎（v3.0 纯K线版）
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


def compute_trailing_stop(bars: list[dict], currency_sign: str) -> dict:
    """
    计算 ATR 追踪止损和限价单价格。
    
    追踪止损逻辑：
      - 取近N日最高价作为追踪基准
      - 止损 = 最高价 - ATR_MULTIPLIER × ATR
      - 价格涨，止损跟着上移；价格跌，止损不动
    
    限价单逻辑：
      - 加仓信号：限价 = 支撑位 × (1 + LIMIT_ENTRY_PCT)，略高于支撑避免滑点
      - 观望信号：限价 = 支撑位（保守等待）
      - 减仓信号：不设限价买入
    """
    result = {
        "atr": None,
        "recent_high": None,
        "trailing_stop": None,
        "limit_entry": None,
        "target_price": None,
        "trailing_stop_pct": None,
    }
    
    if not bars or len(bars) < ATR_PERIOD + 1:
        return result
    
    atr = compute_atr(bars, ATR_PERIOD)
    if atr is None:
        return result
    
    result["atr"] = round(atr, 2)
    
    # 近20日最高价作为追踪基准
    lookback = min(20, len(bars))
    recent_high = max(b["high"] for b in bars[-lookback:])
    result["recent_high"] = recent_high
    
    # 追踪止损 = 最高价 - N倍ATR
    trailing_stop = recent_high - ATR_STOP_MULTIPLIER * atr
    result["trailing_stop"] = round(trailing_stop, 2)
    
    last_close = bars[-1]["close"]
    if last_close > 0:
        result["trailing_stop_pct"] = round((trailing_stop - last_close) / last_close * 100, 2)
    
    return result


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
    ts = compute_trailing_stop(bars, currency_sign)
    result["atr"] = ts["atr"]
    result["trailing_stop"] = ts["trailing_stop"]
    result["trailing_stop_pct"] = ts["trailing_stop_pct"]
    result["recent_high"] = ts["recent_high"]
    
    # 限价买入（只在加仓/观望时设置）
    if signal in ("加仓", "观望"):
        limit_entry = round(support * (1 + LIMIT_ENTRY_PCT), 2)
        result["limit_entry"] = limit_entry
    
    # 目标价 = 阻力位
    result["target_price"] = resistance

    return result


# ══════════════════════════════════════════════════════════════════════
#  模块四：控制台表格输出
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
#  模块五：Telegram 消息构建
# ══════════════════════════════════════════════════════════════════════

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
        display = r["display_name"]
        pa = r.get("pa", {})
        signal = pa.get("signal", "")

        display = display.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        if signal == "减仓":
            has_reduce = True
            diag_lines.append(f"🔴 <b>{display}</b>")
        elif signal == "加仓":
            diag_lines.append(f"🟢 <b>{display}</b>")
        else:
            diag_lines.append(f"🟡 <b>{display}</b>")

        # 诊断文字
        diag = pa.get("diagnosis", "数据不足")
        diag = diag.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        diag_lines.append(f"   {diag}")

        # 关键价位
        if pa.get("key_levels"):
            kl = pa["key_levels"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            diag_lines.append(f"   {kl}")

        # 建议原因逐条列出
        reasons = pa.get("reasons", [])
        if reasons:
            diag_lines.append("   📎 原因：")
            for reason in reasons:
                reason_esc = reason.replace("&", "&amp;").replace("<", "&lt;").replace("&gt;", ">")
                diag_lines.append(f"     · {reason_esc}")

        # 持仓盈亏
        avg_cost = pa.get("avg_cost")
        shares = pa.get("shares")
        unrealized_pnl = pa.get("unrealized_pnl_pct")
        blended_avg = pa.get("blended_avg")
        if avg_cost is not None:
            cs = r.get('currency_sign', '$')
            shares_str = f"{shares}股" if shares else "—"
            pnl_emoji = "📉" if unrealized_pnl and unrealized_pnl < 0 else "📈"
            diag_lines.append(f"   💼 持仓 {shares_str} | 均价 {fmt_price(avg_cost, cs)}")
            if unrealized_pnl is not None:
                diag_lines.append(f"     {pnl_emoji} 浮动 {unrealized_pnl:+.2f}%")
            if blended_avg is not None:
                diag_lines.append(f"     🔄 加1股后均价 {fmt_price(blended_avg, cs)}")

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
            diag_lines.append("   📊 交易计划：")
            # 盘前价格
            if live_price is not None:
                change_str = f" ({live_price_change:+.2f}%)" if live_price_change is not None else ""
                diag_lines.append(f"     🔴 盘前价 {fmt_price(live_price, r.get('currency_sign', '$'))}{change_str}")
            if pre_market_change is not None:
                diag_lines.append(f"     盘前涨跌 {pre_market_change:+.2f}%")
            if limit_entry is not None:
                note = f" ({limit_entry_note})" if limit_entry_note else ""
                diag_lines.append(f"     限价买入 {fmt_price(limit_entry, r.get('currency_sign', '$'))}{note}")
            if target_price is not None:
                diag_lines.append(f"     目标价 {fmt_price(target_price, r.get('currency_sign', '$'))}")
            if trailing_stop is not None:
                pct_str_ts = f"({trailing_stop_pct:+.2f}%)" if trailing_stop_pct is not None else ""
                diag_lines.append(f"     追踪止损 {fmt_price(trailing_stop, r.get('currency_sign', '$'))} {pct_str_ts}")
            if atr_val is not None:
                diag_lines.append(f"     ATR(14) {fmt_price(atr_val, r.get('currency_sign', '$'))}")
            if recent_high is not None:
                diag_lines.append(f"     20日最高 {fmt_price(recent_high, r.get('currency_sign', '$'))}")

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
#  模块六：Telegram 发送
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


# ══════════════════════════════════════════════════════════════════════
#  主执行函数
# ══════════════════════════════════════════════════════════════════════

def run_calculator():
    """
    单次执行主流程：
    1. 遍历 WATCHLIST，通过 Yahoo Finance 拉取K线
    2. 对每个标的执行 PA 分析
    3. 打印控制台表格
    4. 推送 Telegram
    """

    # ── Step 0: 延迟导入检查 ──────────────────────────────────────
    try:
        import yfinance  # noqa: F401
    except ImportError:
        print("❌ 缺少依赖库 yfinance，请执行：pip install yfinance")
        sys.exit(1)

    results = []

    # ── Step 0.5: 获取持仓数据（Flex Query → Vision 读图 → 静态配置）──
    live_positions = fetch_positions()
    if live_positions:
        print(f"  📋 已获取 {len(live_positions)} 个持仓数据\n")

    # ── Step 1~2: 逐标的处理 ────────────────────────────────────────
    import time  # noqa: E402

    for idx, (symbol, cfg) in enumerate(WATCHLIST.items()):
        display_name = cfg["display_name"]
        currency_sign = cfg["currency_sign"]

        # 请求间延迟，避免限流（第一个不延迟）
        if idx > 0 and YF_REQUEST_DELAY > 0:
            print(f"  ⏳ 等待 {YF_REQUEST_DELAY}s 避免限流...")
            time.sleep(YF_REQUEST_DELAY)

        print(f"\n🔍 处理标的：{display_name} ({symbol})")

        row = {
            "symbol":       symbol,
            "display_name": display_name,
            "currency_sign": currency_sign,
            "pa":           {},
            "error":        None,
        }

        # 拉取K线
        bars = fetch_yahoo_bars(symbol)

        if not bars:
            row["error"] = "K线数据获取失败"
            results.append(row)
            logging.warning(f"{symbol}：K线数据获取失败")
            continue

        # 拉取盘前/实时价格
        premarket = fetch_premarket_price(symbol)
        row["premarket"] = premarket

        # PA 策略分析
        pa_result = analyze_pa(bars, currency_sign)
        row["pa"] = pa_result

        # ── 持仓盈亏分析 ────────────────────────────────────────────
        # 优先用动态获取的持仓，降级到 WATCHLIST 静态配置
        pos = live_positions.get(symbol, {})
        avg_cost = pos.get("avg_cost") or cfg.get("avg_cost")
        shares = pos.get("shares") or cfg.get("shares")
        if avg_cost and pa_result.get("last_close"):
            last_close = pa_result["last_close"]
            pnl_pct = round((last_close - avg_cost) / avg_cost * 100, 2)
            pa_result["avg_cost"] = avg_cost
            pa_result["shares"] = shares
            pa_result["unrealized_pnl_pct"] = pnl_pct

            # 如果是加仓信号，计算摊薄均价
            if pa_result.get("signal") == "加仓" and pa_result.get("limit_entry"):
                if shares and shares > 0:
                    new_shares = shares + 1
                    blended = round((avg_cost * shares + pa_result["limit_entry"] * 1) / new_shares, 2)
                    pa_result["blended_avg"] = blended
                else:
                    pa_result["blended_avg"] = pa_result["limit_entry"]

        # ── 用盘前价格修正限价单和追踪止损 ──────────────────────────
        if premarket:
            # 优先用盘前价，其次盘后价，最后常规价
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

                # 修正限价单：用 live_price 替代 last_close 重新计算
                support = pa_result.get("support")
                if support and pa_result.get("signal") in ("加仓", "观望"):
                    # 限价取支撑位和实时价偏低的那个，再加0.5%偏移
                    base = min(support, live_price)
                    pa_result["limit_entry"] = round(base * (1 + LIMIT_ENTRY_PCT), 2)
                    pa_result["limit_entry_note"] = (
                        f"基于盘前价 {fmt_price(live_price, currency_sign)} 修正"
                    )

                # 修正追踪止损：用 live_price 重算止损百分比
                trailing_stop = pa_result.get("trailing_stop")
                if trailing_stop and live_price > 0:
                    pa_result["trailing_stop_pct"] = round(
                        (trailing_stop - live_price) / live_price * 100, 2
                    )

            # 记录盘前涨跌幅
            pm_change = premarket.get("pre_market_change_pct")
            if pm_change is not None:
                pa_result["pre_market_change_pct"] = round(pm_change * 100, 2) if abs(pm_change) < 1 else round(pm_change, 2)

        # 格式化输出字段
        if pa_result.get("last_close") is not None:
            pa_result["last_close_fmt"] = fmt_price(pa_result["last_close"], currency_sign)
        else:
            pa_result["last_close_fmt"] = "N/A"

        if pa_result.get("resistance") is not None:
            pa_result["resistance_fmt"] = fmt_price(pa_result["resistance"], currency_sign)
        else:
            pa_result["resistance_fmt"] = "N/A"

        if pa_result.get("support") is not None:
            pa_result["support_fmt"] = fmt_price(pa_result["support"], currency_sign)
        else:
            pa_result["support_fmt"] = "N/A"

        if pa_result.get("ma_short") is not None:
            pa_result["ma_short_fmt"] = fmt_price(pa_result["ma_short"], currency_sign)
        else:
            pa_result["ma_short_fmt"] = "N/A"

        if pa_result.get("ma_long") is not None:
            pa_result["ma_long_fmt"] = fmt_price(pa_result["ma_long"], currency_sign)
        else:
            pa_result["ma_long_fmt"] = "N/A"

        results.append(row)
        print(f"  {pa_result.get('diagnosis', '')}\n")

        # 日志
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

    if not results:
        print("⚠️  没有获取到任何标的数据。")
        sys.exit(1)

    # ── Step 3: 打印控制台表格 ───────────────────────────────────────
    print()
    print(build_console_table(results))
    print()

    # ── Step 4: 推送 Telegram ────────────────────────────────────────
    tg_msg = build_telegram_message(results)
    send_telegram(tg_msg)

    logging.info("脚本正常结束")
    print("✅ 完成。")


# ══════════════════════════════════════════════════════════════════════
#  程序入口
# ══════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    run_calculator()