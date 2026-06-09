"""
╔══════════════════════════════════════════════════════════════════════╗
║       盘前静态补仓位置计算器 v2.0                                     ║
║       Pre-Market Static Grid + Price Action Strategy                 ║
║                                                                      ║
║  新增模块：价格行为法（Price Action）止盈策略                          ║
║    · 结构性强阻力位（30日最高收盘价）                                  ║
║    · 动能衰竭 Pin Bar 识别（长上影线形态）                             ║
║    · 多头衰竭 / 触及阻力 双重预警                                      ║
║                                                                      ║
║  风控声明：本程序严禁包含任何 placeOrder-SELL 卖出指令。               ║
║           所有输出均为静态计算与诊断提示，最终操作由用户手动执行。      ║
║  运行方式：每日美股开盘前单次执行，无后台驻留。                        ║
╚══════════════════════════════════════════════════════════════════════╝

依赖安装：
    pip install ib_insync python-dotenv requests

使用方式：
    python premarket_grid_calculator.py
"""

import sys
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

# ── 加载 .env 配置文件 ─────────────────────────────────────────────────
load_dotenv()

IB_HOST      = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT      = int(os.getenv("IB_PORT", "7497"))   # TWS纸盘=7497 | 实盘=7496 | GW纸盘=4002 | 实盘=4001
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "10"))
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID   = os.getenv("TG_CHAT_ID", "")

# ══════════════════════════════════════════════════════════════════════
#  PA 策略参数配置（可按需调整）
# ══════════════════════════════════════════════════════════════════════
PA_KLINE_LOOKBACK     = 30    # 获取多少根日K线用于分析
PA_RESISTANCE_WINDOW  = 30    # 阻力位回望窗口（根K线数）
PA_NEAR_RESIST_PCT    = 0.02  # 距阻力位 ≤ 2% 触发预警
PA_PIN_UPPER_RATIO    = 2.0   # 上影线 / 实体 倍数阈值，超过即为衰竭Pin Bar
PA_PIN_BODY_TOP_PCT   = 0.55  # 收盘价在K线范围的百分位阈值（收在中下部），< 此值才判定

# ══════════════════════════════════════════════════════════════════════
#  监控标的配置表
#  1321 是东京证券交易所上市的日经225 ETF，需精确路由 TSE + JPY
# ══════════════════════════════════════════════════════════════════════
WATCHLIST = {
    "QQQ": {
        "exchange":      "SMART",
        "currency":      "USD",
        "secType":       "STK",
        "grid_pct":      0.03,        # 网格 3%
        "currency_sign": "$",
        "display_name":  "QQQ  纳指100 ETF",
    },
    "1321": {
        "exchange":      "TSE",       # 东京证券交易所
        "currency":      "JPY",
        "secType":       "STK",
        "grid_pct":      0.05,        # 网格 5%
        "currency_sign": "¥",
        "display_name":  "1321 野村日经225",
    },
    "EWY": {
        "exchange":      "SMART",
        "currency":      "USD",
        "secType":       "STK",
        "grid_pct":      0.06,        # 网格 6%
        "currency_sign": "$",
        "display_name":  "EWY  韩国指数 ETF",
    },
    "GOOG": {
        "exchange":      "SMART",
        "currency":      "USD",
        "secType":       "STK",
        "grid_pct":      0.08,        # 网格 8%
        "currency_sign": "$",
        "display_name":  "GOOG 谷歌",
    },
    "NVDA": {
        "exchange":      "SMART",
        "currency":      "USD",
        "secType":       "STK",
        "grid_pct":      0.10,        # 网格 10%
        "currency_sign": "$",
        "display_name":  "NVDA 英伟达",
    },
}


# ══════════════════════════════════════════════════════════════════════
#  模块一：工具函数
# ══════════════════════════════════════════════════════════════════════

def fmt_price(price: float, sign: str, decimals: int = 2) -> str:
    """格式化价格字符串，自动加货币符号。日元不需要小数位。"""
    if sign == "¥":
        return f"¥{price:,.0f}"
    return f"{sign}{price:,.{decimals}f}"


def pct_str(val: float) -> str:
    """将小数转为百分比字符串，如 0.034 -> '+3.40%'"""
    sign = "+" if val >= 0 else ""
    return f"{sign}{val*100:.2f}%"


# ══════════════════════════════════════════════════════════════════════
#  模块二：价格行为法（PA）分析引擎
#  ⚠️ 纯静态计算，严禁包含任何下单/卖出逻辑
# ══════════════════════════════════════════════════════════════════════

def analyze_pa(bars: list, currency_sign: str) -> dict:
    """
    对一组日K线（BarData列表）执行 PA 止盈策略分析。

    参数：
        bars          - ib_insync 返回的 BarDataList，按时间升序排列
        currency_sign - 货币符号，用于格式化输出

    返回字典包含：
        resistance        : float  - 30日结构性强阻力位（最高收盘价）
        resistance_fmt    : str    - 格式化阻力位字符串
        last_close        : float  - 最后一根已收盘K线的收盘价
        dist_to_resist    : float  - 最后收盘价距阻力位的距离百分比（正=低于阻力）
        is_near_resistance: bool   - 是否触及近阻力区（距离 ≤ PA_NEAR_RESIST_PCT）
        is_pin_bar        : bool   - 昨日是否出现动能衰竭 Pin Bar
        pin_bar_detail    : str    - Pin Bar 详细描述（调试用）
        tp_price          : float  - PA建议止盈价（阻力位，不含空仓情况）
        tp_price_fmt      : str    - 格式化止盈价
        alert_level       : int    - 预警级别 0=正常 1=注意 2=强烈预警
        diagnosis         : str    - 今日行情诊断文字
        pa_error          : str|None - 若分析失败的原因
    """

    result = {
        "resistance":         None,
        "resistance_fmt":     "N/A",
        "last_close":         None,
        "dist_to_resist":     None,
        "is_near_resistance": False,
        "is_pin_bar":         False,
        "pin_bar_detail":     "",
        "tp_price":           None,
        "tp_price_fmt":       "N/A",
        "alert_level":        0,
        "diagnosis":          "数据不足，无法分析",
        "pa_error":           None,
    }

    # ── 数据有效性检查 ────────────────────────────────────────────────
    # 需要至少 2 根K线：1根用于计算阻力（lookback），1根用于形态分析（昨日）
    if not bars or len(bars) < 2:
        result["pa_error"] = f"历史K线数量不足（获取到 {len(bars) if bars else 0} 根）"
        result["diagnosis"] = "⚪ 历史数据不足，跳过PA分析"
        return result

    # ib_insync 的 BarData 对象带有 open/high/low/close 属性
    # 取 min(实际根数, 回望窗口) 根用于阻力位计算
    window_bars = bars[-PA_RESISTANCE_WINDOW:] if len(bars) >= PA_RESISTANCE_WINDOW else bars

    # ── 计算结构性强阻力位：窗口内最高收盘价 ────────────────────────
    resistance = max(b.close for b in window_bars)

    # ── 最后一根已收盘K线（昨日K线）= bars 列表倒数第一根 ────────────
    # IB reqHistoricalData 在开盘前运行时，最后一根即为前一交易日收盘K线
    yest = bars[-1]   # 昨日K线
    last_close = yest.close

    # ── 计算最后收盘价距阻力位的距离 ────────────────────────────────
    # dist > 0 表示收盘价低于阻力位（正常），dist ≤ 0 表示已触及或突破阻力
    dist_to_resist = (resistance - last_close) / resistance if resistance > 0 else 1.0

    is_near_resistance = (dist_to_resist <= PA_NEAR_RESIST_PCT)

    # ── Pin Bar 识别（动能衰竭形态）────────────────────────────────
    #
    #  Pin Bar 判定条件（必须同时满足）：
    #    1. 实体大小 = |close - open|，确保实体 > 0（避免十字星除零）
    #    2. 上影线 = high - max(open, close)
    #    3. 上影线长度 ≥ 实体 × PA_PIN_UPPER_RATIO（上影线是实体的2倍以上）
    #    4. 收盘价在整根K线范围的下半区（收在中部偏下）
    #       计算：(close - low) / (high - low) < PA_PIN_BODY_TOP_PCT
    #
    is_pin_bar   = False
    pin_detail   = ""

    bar_range = yest.high - yest.low       # 整根K线总振幅

    if bar_range > 1e-9:                   # 防止除零（价格极小的情况）
        body_size   = abs(yest.close - yest.open)
        upper_wick  = yest.high - max(yest.open, yest.close)
        # 收盘价在K线总范围内的相对位置（0=最低，1=最高）
        close_pos   = (yest.close - yest.low) / bar_range

        # 实体过小（十字星）时，上影线比较无意义，至少保留 0.1% 振幅的实体才判定
        min_body = bar_range * 0.03        # 实体至少占振幅的 3%

        if (
            body_size >= min_body                                    # 条件1：有效实体
            and upper_wick >= body_size * PA_PIN_UPPER_RATIO        # 条件2：上影线足够长
            and close_pos < PA_PIN_BODY_TOP_PCT                     # 条件3：收在中下部
        ):
            is_pin_bar = True
            wick_ratio = upper_wick / body_size if body_size > 1e-9 else 0
            pin_detail = (
                f"上影/实体={wick_ratio:.1f}x, "
                f"收盘位置={close_pos*100:.0f}%处"
            )

    # ── 止盈价 = 阻力位（PA建议止盈目标）────────────────────────────
    # 阻力位即为最近30日最高收盘价，是 PA 止盈的自然参考目标
    tp_price = resistance

    # ── 综合预警逻辑 ─────────────────────────────────────────────────
    alert_level = 0
    diagnosis_parts = []

    if is_pin_bar:
        alert_level = max(alert_level, 2)
        diagnosis_parts.append("昨日出现【动能衰竭 Pin Bar】")

    if is_near_resistance:
        alert_level = max(alert_level, 2)
        diagnosis_parts.append(f"价格已触及阻力区（距阻力 {dist_to_resist*100:.2f}%）")
    elif dist_to_resist < 0.05:   # 距阻力 < 5% 给出轻度注意
        alert_level = max(alert_level, 1)
        diagnosis_parts.append(f"价格接近阻力区（距阻力 {dist_to_resist*100:.2f}%）")

    if alert_level == 2:
        diagnosis = "🔴 " + "，".join(diagnosis_parts) + "，今日不宜补仓，建议分批止盈"
    elif alert_level == 1:
        diagnosis = "🟡 " + "，".join(diagnosis_parts) + "，建议观望，谨慎补仓"
    else:
        diagnosis = f"🟢 结构健康，距阻力 {dist_to_resist*100:.2f}%，可按网格计划补仓"

    # ── 格式化 ───────────────────────────────────────────────────────
    result.update({
        "resistance":         resistance,
        "resistance_fmt":     fmt_price(resistance, currency_sign),
        "last_close":         last_close,
        "dist_to_resist":     dist_to_resist,
        "is_near_resistance": is_near_resistance,
        "is_pin_bar":         is_pin_bar,
        "pin_bar_detail":     pin_detail,
        "tp_price":           tp_price,
        "tp_price_fmt":       fmt_price(tp_price, currency_sign),
        "alert_level":        alert_level,
        "diagnosis":          diagnosis,
        "pa_error":           None,
    })
    return result


# ══════════════════════════════════════════════════════════════════════
#  模块三：控制台表格输出（升级版，含 PA 列）
# ══════════════════════════════════════════════════════════════════════

def build_console_table(results: list[dict]) -> str:
    """
    构建精美的控制台中文表格（v2.0，含PA止盈与行情诊断栏）。
    """
    W = 100  # 总宽度
    now_str = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")

    def hline(l, m, r, c="═"):
        return f"{l}{c * (W - 2)}{r}"

    lines = []
    lines.append(hline("╔", "═", "╗"))
    lines.append(f"║{'📊  盘前补仓网格 + PA止盈策略计算报告':^{W-2}}║")
    lines.append(f"║{'运行时间：' + now_str:^{W-5}}║")
    lines.append(hline("╠", "═", "╣"))

    # ── 列标题 ──
    lines.append(
        f"║  {'标的':<20} {'持仓':>7} {'均价':>10} {'网格':>4} "
        f"{'补仓挂单价':>12} {'PA止盈参考价':>12} {'30日阻力位':>11} {'行情诊断':<20}  ║"
    )
    lines.append(hline("╠", "═", "╣", "─"))

    for i, r in enumerate(results):
        sep = hline("║", "─", "║", "─") if i < len(results) - 1 else ""

        if r.get("error"):
            # 持仓异常行
            lines.append(
                f"║  {r['display_name']:<20}  "
                f"{'⚠️  ' + r['error']:<{W-26}}  ║"
            )
        else:
            # 正常数据行
            pa = r.get("pa", {})
            # 预警标记
            alert = r.get("alert_level", 0)
            alert_tag = " 🔴" if alert == 2 else (" 🟡" if alert == 1 else " 🟢")

            lines.append(
                f"║  {r['display_name']:<20} "
                f"{r['position']:>7,.0f} "
                f"{r['avg_cost_fmt']:>10} "
                f"{r['grid_str']:>4} "
                f"{r['limit_price_fmt']:>12} "
                f"{pa.get('tp_price_fmt','N/A'):>12} "
                f"{pa.get('resistance_fmt','N/A'):>11} "
                f"{alert_tag + ' ' + r.get('diag_short',''):< 20}  ║"
            )

            # Pin Bar 附加说明行
            if pa.get("is_pin_bar"):
                lines.append(
                    f"║  {'':4}⚡ Pin Bar 细节：{pa.get('pin_bar_detail',''):<{W-26}}║"
                )

        if sep:
            lines.append(sep)

    lines.append(hline("╠", "═", "╣"))

    # 图例说明
    lines.append(f"║  {'🟢 可按计划补仓   🟡 谨慎观望   🔴 检测到衰竭/触及阻力 → 建议止盈，今日停止补仓':^{W-4}}  ║")
    lines.append(f"║  {'补仓挂单价 = 均价×(1-网格%)   PA止盈价 = 30日最高收盘价（结构性阻力）':^{W-4}}  ║")
    lines.append(hline("╚", "═", "╝"))

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
#  模块四：Telegram 消息构建（升级版，含 PA 预警高亮）
# ══════════════════════════════════════════════════════════════════════

def build_telegram_message(results: list[dict]) -> str:
    """
    构建分两段的 Telegram 消息：
    - 第一段：等宽代码块表格（核心数据）
    - 第二段：各标的 PA 行情诊断（含高亮预警）
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── 段一：数据表格（等宽代码块）────────────────────────────────
    table_lines = [
        "📊 *盘前网格 \\+ PA止盈策略报告* v2\\.0",
        f"🕐 {now_str}",
        "",
        "```",
        f"{'标的':<7} {'持仓':>6} {'均价':>10} {'补仓挂单':>10} {'PA止盈参考':>11}",
        "─" * 50,
    ]

    for r in results:
        if r.get("error"):
            table_lines.append(f"{r['symbol']:<7}  ⚠️ {r['error']}")
        else:
            pa = r.get("pa", {})
            table_lines.append(
                f"{r['symbol']:<7}"
                f"{r['position']:>6,.0f} "
                f"{r['avg_cost_fmt']:>10} "
                f"{r['limit_price_fmt']:>10} "
                f"{pa.get('tp_price_fmt','N/A'):>11}"
            )

    table_lines.append("─" * 50)
    table_lines.append("补仓价=均价×(1-网格%)")
    table_lines.append("PA止盈=30日最高收盘价")
    table_lines.append("```")

    # ── 段二：PA 行情诊断（逐标的，带预警高亮）──────────────────────
    diag_lines = ["", "📋 *今日行情诊断*", ""]

    has_critical_alert = False

    for r in results:
        sym          = r["symbol"]
        display      = r["display_name"]
        alert_level  = r.get("alert_level", 0)
        pa           = r.get("pa", {})
        full_diag    = r.get("full_diagnosis", "数据不足")

        if alert_level == 2:
            has_critical_alert = True
            diag_lines.append(f"🔴 *{display}*")
        elif alert_level == 1:
            diag_lines.append(f"🟡 *{display}*")
        else:
            diag_lines.append(f"🟢 *{display}*")

        diag_lines.append(f"   {full_diag}")

        # 阻力位与止盈价补充信息
        if pa.get("resistance_fmt") and pa.get("resistance_fmt") != "N/A":
            dist = pa.get("dist_to_resist", 1.0)
            diag_lines.append(
                f"   阻力位 {pa['resistance_fmt']}  |  "
                f"距当前收盘价 {dist*100:.2f}%"
            )

        # Pin Bar 细节提示
        if pa.get("is_pin_bar"):
            diag_lines.append(f"   ⚡ 昨日 Pin Bar：{pa.get('pin_bar_detail','')}")

        diag_lines.append("")

    # ── 全局预警横幅 ─────────────────────────────────────────────────
    if has_critical_alert:
        diag_lines.insert(
            2,  # 插入到标题下方
            "⚠️⚠️ *高优先级提醒：检测到多头衰竭或触及重大阻力！*\n"
            "今日不宜继续补仓，请在手机端考虑分批卖出止盈。⚠️⚠️\n"
        )

    return "\n".join(table_lines + diag_lines)


# ══════════════════════════════════════════════════════════════════════
#  模块五：Telegram 发送
# ══════════════════════════════════════════════════════════════════════

def send_telegram(message: str) -> bool:
    """使用 requests 同步发送 Telegram MarkdownV2 消息。"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️  [Telegram] 未配置 TG_BOT_TOKEN 或 TG_CHAT_ID，跳过推送。")
        return False
    try:
        import requests  # noqa: PLC0415
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id":    TG_CHAT_ID,
            "text":       message,
            "parse_mode": "MarkdownV2",
        }
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            print("✅ [Telegram] 消息推送成功。")
            return True
        else:
            print(f"❌ [Telegram] 推送失败，HTTP {resp.status_code}：{resp.text[:300]}")
            return False
    except Exception as e:
        print(f"❌ [Telegram] 推送异常：{e}")
        return False


# ══════════════════════════════════════════════════════════════════════
#  模块六：IB 历史K线获取
# ══════════════════════════════════════════════════════════════════════

def fetch_historical_bars(ib, contract, symbol: str) -> list:
    """
    通过 ib.reqHistoricalData 获取指定合约的近 N 根日K线。

    参数：
        ib       - IB 连接实例
        contract - ib_insync Contract 对象
        symbol   - 标的代码（仅用于日志）

    返回：
        BarDataList（升序排列）；失败则返回空列表。

    ⚠️ 注意：
        · whatToShow="TRADES" 适合股票；ETF在某些交易所可能需要 "MIDPOINT"
        · 日本TSE品种 1321 使用 "TRADES" 通常可正常取到日K
        · useRTH=True 仅使用常规交易时段（排除盘前盘后），确保K线的一致性
    """
    from ib_insync import util  # noqa: PLC0415

    try:
        print(f"  📥 [{symbol}] 正在拉取 {PA_KLINE_LOOKBACK} 根日K线...")
        bars = ib.reqHistoricalData(
            contract,
            endDateTime="",           # 空字符串 = 拉取到最新可用数据
            durationStr=f"{PA_KLINE_LOOKBACK + 5} D",  # 多取几天以防节假日缺口
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=True,              # 只用正常交易时段
            formatDate=1,             # 日期格式：YYYYMMDD HH:MM:SS
            keepUpToDate=False,       # 静态历史数据，不订阅实时更新
        )
        if bars:
            print(f"  ✅ [{symbol}] 获取到 {len(bars)} 根K线，"
                  f"最新日期：{bars[-1].date}")
        else:
            print(f"  ⚠️  [{symbol}] 未获取到历史K线数据。")
        return bars if bars else []
    except Exception as e:
        print(f"  ❌ [{symbol}] 历史K线获取失败：{e}")
        return []


# ══════════════════════════════════════════════════════════════════════
#  主执行函数
# ══════════════════════════════════════════════════════════════════════

def run_calculator():
    """
    单次执行主流程：
    1. 连接 IB Gateway（只读模式）
    2. 拉取账户持仓
    3. 拉取每个标的的历史K线（30根日K）
    4. 计算网格补仓挂单价
    5. 执行 PA 止盈策略分析
    6. 打印升级版控制台表格
    7. 推送 Telegram
    8. 【风控】显式断开，sys.exit 退出，严禁驻留后台
    """

    # ── Step 0: 延迟导入，给出友好安装提示 ──────────────────────────
    try:
        from ib_insync import IB, Stock, Contract  # noqa: PLC0415
    except ImportError:
        print("❌ 缺少依赖库 ib_insync，请执行：pip install ib_insync")
        sys.exit(1)

    ib = IB()

    # ── Step 1: 连接 IB Gateway ──────────────────────────────────────
    print(f"🔌 正在连接 IB Gateway：{IB_HOST}:{IB_PORT}  clientId={IB_CLIENT_ID} ...")
    try:
        ib.connect(
            host=IB_HOST,
            port=IB_PORT,
            clientId=IB_CLIENT_ID,
            timeout=20,
            readonly=True,    # ⚠️ 只读模式，物理层禁止任何下单操作
        )
    except Exception as e:
        print(f"❌ 连接 IB Gateway 失败：{e}")
        print("   请确认 IB Gateway / TWS 已启动，且已启用 Socket 客户端连接。")
        sys.exit(1)

    print(f"✅ 已连接 IB Gateway。账户：{ib.managedAccounts()}")
    print()

    # ── Step 2: 拉取账户持仓 ─────────────────────────────────────────
    try:
        portfolio_items = ib.portfolio()
    except Exception as e:
        print(f"❌ 读取持仓失败：{e}")
        ib.disconnect()
        sys.exit(1)

    holding_map: dict[str, object] = {}
    for item in portfolio_items:
        sym = item.contract.symbol
        if sym in WATCHLIST:
            holding_map[sym] = item

    # ── Step 3~5: 逐标的处理 ─────────────────────────────────────────
    results = []

    for symbol, cfg in WATCHLIST.items():
        sign  = cfg["currency_sign"]
        grid  = cfg["grid_pct"]
        print(f"🔍 处理标的：{cfg['display_name']}")

        row = {
            "symbol":            symbol,
            "display_name":      cfg["display_name"],
            "grid_str":          f"{grid*100:.0f}%",
            "position":          0,
            "avg_cost":          0.0,
            "avg_cost_fmt":      "N/A",
            "limit_price":       0.0,
            "limit_price_fmt":   "N/A",
            "pa":                {},
            "alert_level":       0,
            "diag_short":        "无数据",
            "full_diagnosis":    "无数据",
            "error":             None,
        }

        # ── 3a. 持仓数据 ────────────────────────────────────────────
        if symbol not in holding_map:
            row["error"] = "无持仓记录"
            results.append(row)
            print(f"  ⚪ 未找到持仓，跳过。\n")
            continue

        item     = holding_map[symbol]
        position = item.position
        avg_cost = item.averageCost

        if position <= 0:
            row["error"] = "持仓为 0，已清仓"
            results.append(row)
            print(f"  ⚪ 持仓为0，跳过。\n")
            continue

        if avg_cost <= 0:
            row["error"] = "均价异常（≤0），请核查"
            results.append(row)
            continue

        # ── 3b. 计算网格挂单价 ──────────────────────────────────────
        limit_price = avg_cost * (1 - grid)
        if sign == "¥":
            limit_price = round(limit_price)
            avg_cost_r  = round(avg_cost)
        else:
            limit_price = round(limit_price, 2)
            avg_cost_r  = round(avg_cost, 2)

        row.update({
            "position":          position,
            "avg_cost":          avg_cost_r,
            "avg_cost_fmt":      fmt_price(avg_cost_r, sign),
            "limit_price":       limit_price,
            "limit_price_fmt":   fmt_price(limit_price, sign),
        })

        # ── 4. 构建合约对象，拉取历史K线 ────────────────────────────
        contract = Contract(
            symbol   = symbol,
            secType  = cfg["secType"],
            exchange = cfg["exchange"],
            currency = cfg["currency"],
        )
        # IB 要求对合约进行限定（qualify）以补全 conId 等必填字段
        try:
            qualified = ib.qualifyContracts(contract)
            if not qualified:
                raise ValueError("无法限定合约（qualifyContracts 返回空）")
            contract = qualified[0]
        except Exception as e:
            print(f"  ⚠️  [{symbol}] 合约限定失败：{e}，将尝试直接请求K线...")

        bars = fetch_historical_bars(ib, contract, symbol)

        # ── 5. PA 策略分析 ───────────────────────────────────────────
        pa_result = analyze_pa(bars, sign)
        row["pa"] = pa_result

        alert_level = pa_result.get("alert_level", 0)
        row["alert_level"]    = alert_level
        row["full_diagnosis"] = pa_result.get("diagnosis", "")

        # 控制台表格用简短诊断（截断以适应列宽）
        diag_map = {0: "可按计划补仓", 1: "谨慎观望", 2: "衰竭预警"}
        row["diag_short"] = diag_map.get(alert_level, "")

        results.append(row)
        print(f"  {pa_result.get('diagnosis','')}\n")

    # ── Step 6: 打印控制台表格 ───────────────────────────────────────
    print()
    print(build_console_table(results))
    print()

    # ── Step 7: 推送 Telegram ────────────────────────────────────────
    tg_msg = build_telegram_message(results)
    send_telegram(tg_msg)

    # ── Step 8: 【关键风控】显式断开，禁止驻留后台 ──────────────────
    print()
    print("🔒 正在断开 IB Gateway 连接...")
    try:
        ib.disconnect()
        print("✅ 连接已断开。程序正常退出。")
    except Exception as e:
        print(f"⚠️  断开时发生异常（可忽略）：{e}")

    sys.exit(0)


# ══════════════════════════════════════════════════════════════════════
#  程序入口
# ══════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    run_calculator()
