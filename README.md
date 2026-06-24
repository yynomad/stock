# 盘前 Price Action 策略计算器

每日美股开盘前一次性运行的 PA 策略分析工具。基于 Yahoo Finance 日K线数据，输出加仓 / 减仓 / 观望信号、关键支撑阻力位、ATR 追踪止损与限价单建议，可选推送至 Telegram。

> **风控声明：** 本程序仅提供静态分析与建议，不包含任何下单指令。

## 核心功能

- 📈 **趋势判断** — 短/长期均线 + 高低点结构
- 🎯 **关键价位** — 近 30 日 swing high/low 修正后的支撑 / 阻力
- 🕯 **K线形态** — Pin Bar、Engulfing（吞没）、连续同向K线检测
- 🟢 **综合信号** — 趋势 / 位置 / 形态评分 → 加仓 / 减仓 / 观望（含强度）
- 🧭 **仓位决策** — 结合持仓 / 成本，把 PA 信号转成试探加仓、分批止盈、风控减仓等明确动作
- 🛑 **ATR 追踪止损** — 20 日最高价 − N×ATR
- 💸 **限价单建议** — 支撑位 +0.5% 偏移；盘前价偏低时自动修正
- 📤 **双通道输出** — 控制台美化表格 + Telegram HTML 富文本推送

## 项目结构

```
stock/
├── premarket_grid_calculator.py    # 主入口（串流程，~160 行）
├── lib/
│   ├── config.py                   # 环境变量 + PA 参数 + WATCHLIST
│   ├── format_utils.py             # 价格 / 百分比格式化
│   ├── yahoo_data.py               # Yahoo Finance K线 + 盘前价
│   ├── pa_strategy.py              # PA 算法核心（趋势 / 形态 / ATR）
│   ├── position_decision.py        # 仓位动作决策（加仓 / 止盈 / 风控）
│   └── output.py                   # 控制台表格 + Telegram 消息构建 + 推送
├── env.template                    # 环境变量模板，复制为 .env 填值
└── premarket_calculator.log        # 运行日志（gitignored）
```

## 快速开始

### 1. 安装依赖

```bash
python3 -m venv venv
source venv/bin/activate
pip install yfinance python-dotenv requests google-genai
```

### 2. 配置（可选）

复制模板：

```bash
cp env.template .env
```

按需填写 `.env`：

| 变量 | 说明 | 必需？ |
|---|---|:---:|
| `TG_BOT_TOKEN` | Telegram Bot Token（@BotFather 获取） | 否 |
| `TG_CHAT_ID` | 接收消息的 Chat ID | 否 |
| `YF_PROXY` | HTTP 代理（如 `http://127.0.0.1:7890`） | 否 |
| `YF_REQUEST_DELAY` | 标的间请求间隔，秒。默认 5 | 否 |
| `GEMINI_API_KEY` | Gemini Vision 持仓截图识别 | 否 |
| `POSITION_*_PCT` | 仓位动作比例，如试探加仓、强止盈等 | 否 |

> 全部留空也能跑 — 只输出到控制台、直连 Yahoo。

### 3. 编辑监控标的

打开 `lib/config.py`，修改 `WATCHLIST`：

```python
WATCHLIST = {
    "QQQ": {"currency_sign": "$", "display_name": "QQQ  纳指100 ETF"},
    "NVDA": {"currency_sign": "$", "display_name": "NVDA 英伟达"},
    "1321.T": {"currency_sign": "¥", "display_name": "1321 野村日经225"},
    # ...
}
```

> Yahoo Finance 的代码：东京证交所标的加 `.T`、瑞士加 `.SW`、港股加 `.HK` 等。

### 4. 运行

```bash
python premarket_grid_calculator.py
```

Telegram Bot 支持单标的测试：

```text
/test AAPL
/test AAPL 10 150
```

第二种格式表示：测试 AAPL，当前持仓 10 股，成本 150。Bot 会返回简洁结论、PA 强度、关键价位和仓位动作。

## Telegram 消息规格

### 单标的测试命令

用于先拿一只股票验证策略输出，推荐从这个命令开始：

```text
/test <股票代码> [持仓股数] [平均成本]
```

示例：

```text
/test NVDA 10 120
```

Bot 返回格式示例：

```text
📌 单标的测试  2026-06-24 09:00:00

NVDA
结论：止盈 20.0% 当前仓位
PA：减仓 强度2｜趋势 up
价格：$145.20
浮盈亏：+21.0%
计划：压力/目标 $148.00；风控线 $132.50
原因：浮盈亏 +21.0%、接近阻力、趋势向上
```

### 默认 WATCHLIST 分析命令

```text
/analyze
```

Bot 会返回两段内容：

1. 简表：标的、收盘价、趋势、PA 信号、阻力、支撑、持仓数量。
2. 逐标的诊断：PA 原因、仓位结论、限价买入 / 目标价 / 风控线。

简表规格示例：

```text
📊 盘前 PA 策略报告
🕐 2026-06-24 09:00:00 | 数据源：Yahoo Finance

标的          收盘   趋势     信号       阻力       支撑
────────────────────────────────────────────────────
NVDA      $145.20    ↑   🔴减仓    $148.00    $132.50  ×10
AAPL      $210.10    →   🟡观望    $215.00    $201.00
────────────────────────────────────────────────────
🟢加仓 🟡观望 🔴减仓
```

### 图片持仓识别

直接向 Bot 发送持仓截图时，流程是：

```text
图片 → Gemini 识别股票/股数/成本 → Yahoo Finance 拉K线 → PA分析 → 仓位结论
```

如果识别失败，Bot 会提示重新发送截图；如果识别成功，会按识别出的持仓生成分析报告。

每日美股开盘前手动跑一次，或用 cron 定时（例如盘前 1 小时）：

```bash
# 21:00 美东盘前 (Asia/Shanghai 09:00 美东 21:30 → 此时段已停 PRE，按需调整)
0 21 * * 1-5 cd /Users/yao/stock && /usr/bin/env python premarket_grid_calculator.py
```

## 调参

`lib/config.py` 顶部集中维护：

| 参数 | 默认 | 说明 |
|---|:---:|---|
| `PA_TREND_MA_SHORT` | 10 | 短期均线周期 |
| `PA_TREND_MA_LONG` | 30 | 长期均线周期 |
| `PA_SWING_LOOKBACK` | 10 | swing 高低点回望窗口 |
| `PA_NEAR_LEVEL_PCT` | 0.02 | 距关键位 ≤ 2% 视为"接近" |
| `PA_PIN_UPPER_RATIO` | 2.0 | 看跌 Pin Bar：上影线/实体倍数阈值 |
| `PA_PIN_LOWER_RATIO` | 2.0 | 看涨 Pin Bar：下影线/实体倍数阈值 |
| `PA_ENGULFING_MIN_BODY` | 0.3 | 吞没形态实体占振幅最小比例 |
| `PA_CONSECUTIVE_DAYS` | 3 | 连续同向K线触发阈值 |
| `ATR_PERIOD` | 14 | ATR 计算周期 |
| `ATR_STOP_MULTIPLIER` | 2.0 | 追踪止损 = 最高价 − N×ATR |
| `LIMIT_ENTRY_PCT` | 0.005 | 限价距支撑的偏移（0.5%） |

## 信号评分逻辑

正分倾向加仓，负分倾向减仓：

| 因素 | 加分 | 减分 |
|---|:---:|:---:|
| 趋势 | 上升 +2 | 下降 −2 |
| 位置 | 接近支撑 +2 | 接近阻力 −2 |
| Pin Bar | 看涨 +2 | 看跌 −2 |
| Engulfing | 看涨 +2 | 看跌 −2 |
| 连续K线 ≥3 | 连跌 +1（超卖） | 连涨 −1（过热） |

最终分级：
- `score ≥ 3` → 加仓（强度 3）
- `score ≥ 1` → 加仓（强度 1~2）
- `score ≤ −3` → 减仓（强度 3）
- `score ≤ −1` → 减仓（强度 1~2）
- 其他 → 观望

## 历史版本

- **当前版** — 改用 Yahoo Finance + 纯 K线 PA 策略；模块化拆分；移除持仓追踪
- **v2.0** — IBKR API + 持仓 / 均价分析（见 git 历史）

## License

私人项目。
