"""
配置模块
─────────────────────────────────────────────────────────────
集中管理：
  · .env 环境变量加载
  · 代理 / 推送配置
  · PA 策略参数
  · WATCHLIST 监控标的表
  · 日志初始化
"""

import os
import logging
from dotenv import load_dotenv

# ── 加载 .env 配置文件 ─────────────────────────────────────────────────
load_dotenv()

# ── Telegram 推送配置 ─────────────────────────────────────────────────
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID   = os.getenv("TG_CHAT_ID", "")

# ── Yahoo Finance 代理配置（国内网络可能需要）───────────────────────────
YF_PROXY = os.getenv("YF_PROXY", "")  # 如 http://127.0.0.1:7890

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
#  1321.T 是 Yahoo Finance 上东京证券交易所的日经225 ETF 代码
# ══════════════════════════════════════════════════════════════════════
WATCHLIST = {
    "QQQ": {
        "currency_sign": "$",
        "display_name":  "QQQ  纳指100 ETF",
    },
    "1321.T": {
        "currency_sign": "¥",
        "display_name":  "1321 野村日经225",
    },
    "EWY": {
        "currency_sign": "$",
        "display_name":  "EWY  韩国指数 ETF",
    },
    "GOOG": {
        "currency_sign": "$",
        "display_name":  "GOOG 谷歌",
    },
    "NVDA": {
        "currency_sign": "$",
        "display_name":  "NVDA 英伟达",
    },
    "AAPL": {
        "currency_sign": "$",
        "display_name":  "AAPL 苹果",
    },
    "RDDT": {
        "currency_sign": "$",
        "display_name":  "RDDT Reddit",
    },
    "MRVL": {
        "currency_sign": "$",
        "display_name":  "MRVL Marvell",
    },
    "DELL": {
        "currency_sign": "$",
        "display_name":  "DELL 戴尔",
    },
    "NOK": {
        "currency_sign": "$",
        "display_name":  "NOK 诺基亚",
    },
    "IBKR": {
        "currency_sign": "$",
        "display_name":  "IBKR 盈透",
    },
    "XFAB.SW": {
        "currency_sign": "€",
        "display_name":  "XFAB",
    },
}


