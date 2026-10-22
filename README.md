# StockMan 个人持仓助手

用 Python 和 LangChain 实现的自用多 Agent 项目。一个主控按请求委派持仓、研究、监控三个 Agent；复用原有 PA 与仓位计算。支持 **IBKR 只读持仓同步和持仓截图**，以 Telegram 交互，在本地 SQLite 保存执行记录。首版支持美股与美股 ETF。

当前离线闭环已实现（60 个测试通过）；真实 IBKR 尚待新用户名获批与 Gateway 登录。Gemini 搜索真实探测超过 75 秒总时限，未验证成功；Telegram 真实会话也尚待验收。不要把合成演示数据当成实际持仓或真实研究。

物理项目目录：`/Users/yao/personal/stock/`。完整需求见 [Product.md](Product.md)，实施与逐项验收见 [Plan.md](Plan.md)，学习示例见 [Learning.md](Learning.md)。

## 安装和离线体验

已在 Python 3.10.14 验证；使用项目虚拟环境。

```sh
cd /Users/yao/personal/stock
# 已有 venv 时沿用；首次安装才执行下一行
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
venv/bin/python scripts/validate.py
venv/bin/python scripts/demo.py --offline --scenario end-to-end
venv/bin/python scripts/chat.py --offline "研究 QQQ"
venv/bin/python scripts/tasks.py --data-dir runtime/offline
```

`demo.py` 使用临时目录，退出后清理；`chat.py --offline` 使用 `runtime/offline`，适合查看检查点和跨次对话。离线模式替换模型、行情、券商、SEC、新闻与识图，输出明确标为合成数据；使用真实 LangChain 工具调用循环和 SQLite，并非真实模型效果验收。统一检查禁止 socket 连接，清空凭据、不读取 `.env`。

## 配置真实服务

首次可复制 `env.template` 为 `.env`；已有 `.env` 请只补所需字段，不覆盖现有值。运行依赖网络和相应服务权限。

| 配置 | 作用 |
| --- | --- |
| `GEMINI_API_KEY` | 主控、业务 Agent、截图识别、Google 搜索接地 |
| `GEMINI_MODEL` | 默认 `gemini-3.5-flash-lite`，按已选方案保持一致 |
| `IBKR_ACCOUNT` | 填持仓所在的联名账户 ID，不能用空个人账户代替 |
| `IBKR_HOST` / `IBKR_PORT` | 本机 Gateway 地址及实际 API 端口；模板为 `127.0.0.1:4001`，需核对自己的 Gateway 设置 |
| `IBKR_CLIENT_ID` | 默认 17，避免与其他 API 客户端冲突 |
| `SEC_USER_AGENT` | SEC 要求的应用名与可联系邮箱，例如 `StockMan your-email@example.com` |
| `TG_BOT_TOKEN` / `TG_CHAT_ID` | 本人 Bot 与目标会话 |
| `TG_ALLOWED_USERS` | 允许的数字用户 ID，逗号分隔；本人私聊可默认使用正数 `TG_CHAT_ID` |
| `YF_PROXY` | Yahoo／Telegram 使用的代理；Gemini SDK 使用标准网络代理环境变量 |
| `STOCKMAN_DATA_DIR` | 默认 `runtime`；数据库、检查点、截图仅保存在本机并被 Git 忽略 |

四个 Agent 可分别用 `GEMINI_SUPERVISOR_MODEL`、`GEMINI_PORTFOLIO_MODEL`、`GEMINI_RESEARCH_MODEL`、`GEMINI_MONITOR_MODEL` 覆盖默认值；当前均沿用同一个已选型号。不要在排障时自动更换供应商或开通付费。

模型会收到完成任务所需的持仓与公开资料；截图会发送给 Gemini 识别。IBKR 密码只在用户自己的 Gateway 登录流程输入，不填入项目。运行时禁用 LangSmith 云端追踪，执行记录留在本地。

### IBKR 准备及核对

1. 等程序专用用户名获批，确认它能访问联名账户；核对实际读取权限，`SecondHolder` 这样的角色名本身不代表只读。
2. 用该用户名在本机 IB Gateway 登录并完成认证。启用 Socket API、勾选 **Read-Only API**，核对端口和受信任的本地地址。
3. 在 `.env` 填联名账户的 `IBKR_ACCOUNT`，运行只读探测。

```sh
venv/bin/python scripts/smoke.py --service ibkr --read-only
```

程序先完成连接握手并核对可访问账户，再用 `reqPositionsMulti` 查询指定账户；没有注册下单、撤单或资金工具。成功后仍需人工对比联名持仓、分数股及成本，并确认手机主用户名与 Gateway 专用用户名能同时使用。API 无法代替你核验界面上的只读开关。

未登录、账户错误、超时或快照未完整返回时，不保存伪完整结果；可用同一账户的旧快照时明确标为缓存，超过 24 小时标为过期。没有缓存时可以继续使用截图入口。当前固定 SDK 是 PyPI `ibapi`，与实际 Gateway 版本的兼容性仍需真实联调。

## 运行入口

本地单次任务不发送 Telegram 消息：

```sh
venv/bin/python scripts/chat.py "检查持仓"
venv/bin/python scripts/chat.py "研究 AAPL"
venv/bin/python scripts/chat.py "检查截图持仓" --image /absolute/path/positions.png
venv/bin/python scripts/tasks.py
venv/bin/python scripts/tasks.py --task <任务编号>
```

真实研究结果中的 `news` 是原文、来源和关联搜索建议的临时展示包；本地 CLI 输出其原始结构，Telegram 则发送 HTML 附件。新闻不进入持久化对话或新闻缓存库；来源中的发布日期不可取得时保持未知。请在 Telegram 客户端下载／打开附件确认内容和建议显示正常。

启动 Telegram Bot（会连接外部服务；请自己执行）：

```sh
venv/bin/python bot.py
```

在本人会话发送以下自然语言，或直接发送持仓截图：

```text
检查持仓
研究 AAPL
研究 QQQ
开始监控 AAPL
监控状态
停止监控 AAPL
停止监控
```

代码对监控变更有额外授权检查：本次用户原文明确要求开始／停止才写规则，工具结果中的指令不能替代授权。一次只研究一个明确股票代码。截图只识别一次并作为本次任务的独立来源，不与 IBKR 数量相加；下次普通复盘默认仍使用 IBKR。

同一数据目录只运行一个应用进程。先停止 Bot，再用本地 CLI 操作其数据目录，避免多个进程同时轮询或恢复任务。`Ctrl+C` 停止 Bot。不会自动删除已有 webhook；若 Bot 的 getUpdates 与 webhook 冲突，先检查自己的 Telegram 配置。

原本的单次计算与图片入口继续保留：

```sh
venv/bin/python analyze_image.py /absolute/path/positions.png
venv/bin/python premarket_grid_calculator.py --image /absolute/path/positions.png
venv/bin/python premarket_grid_calculator.py
```

这些是旧版直接计算入口，不经过新主控；部分旧入口按既有配置可能推送 Telegram，不能用作离线测试。旧 `/test`、`/analyze`、`watchlist.json` 已不作为新 Agent 入口的指令协议或监控配置来源。新规则在 SQLite 中。

## 监控、成本及恢复

普通 Python 每 5 分钟检查一次，仅在美股常规交易时段接受有时间戳、未过期的常规报价。按 XNYS 交易日历处理节假日、夏令时和提早收盘；价格超出支撑／阻力 2% 缓冲才产生事件。每标的纽约自然日最多 5 条提醒，状态与事件 ID 持久去重。参数见模板。

关键位取自创建规则时的 Yahoo 日 K 线，保存 `created_at` 和 `bars_asof`，持续沿用直至再次发送“开始监控 AAPL”重算；本版没有自动每日刷新规则。PA 使用调整后日线收盘价，报告注明 K 线日期，与报价事件时间分开。Yahoo 可能延迟、限流或缺数据；默认超过 15 分钟不提醒，休眠后不补发伪实时事件。

正常轮询不用模型；有效事件才进入主控分析。每任务默认最多 8 次逻辑模型调用、12 次 LangChain 工具调用；Google 搜索另计入模型次数，SDK 内部重试不当作新的逻辑调用。暂时性失败最多重试 2 次，发送超时／部分发送标记 `unknown`，不盲目重发。这些预算不构成供应商费用硬上限。

任务状态是 `running`、`completed`、`partial`、`failed`、`interrupted`；任务执行和消息投递分开记录。`completed` 不代表外部数据均已取得，须阅读报告缺失项。进程重启会标出中断任务；仅当确有待执行检查点且没有后续会话任务时可手动恢复：

```sh
venv/bin/python scripts/chat.py --resume-task <任务编号>
```

没有可恢复检查点时明确失败，不能保证任意网络中断下的绝对一次投递；未知结果先查看 Telegram 与本地记录，再决定是否重新发起请求。原始新闻不保存，因此恢复后的输出不保证包含中断前的新闻附件。

## 验收及尚待完成

```sh
venv/bin/python -m pip check
venv/bin/python -m unittest discover -s tests -v
venv/bin/python scripts/validate.py
venv/bin/python scripts/demo.py --offline --scenario routing
venv/bin/python scripts/demo.py --offline --scenario persistence
venv/bin/python scripts/demo.py --offline --scenario portfolio
venv/bin/python scripts/demo.py --offline --scenario research
venv/bin/python scripts/demo.py --offline --scenario monitor
venv/bin/python scripts/demo.py --offline --scenario end-to-end
venv/bin/python scripts/smoke.py --service telegram --dry-run
```

真实服务显式探测：

```sh
venv/bin/python scripts/smoke.py --service gemini --symbol AAPL
venv/bin/python scripts/smoke.py --service ibkr --read-only
```

Gemini 探测可能消耗额度，总时限 75 秒；未通过返回非零。当前真实 IBKR 取决于用户登录；SEC 尚缺 `SEC_USER_AGENT`；真实 Telegram、图片识别及完整研究仍待验收。真实 Gemini 搜索已出现超时，Google 根地址可达，Yahoo 根地址返回 429，这只能帮助排障，不能证明具体数据请求成功。

私人项目；只提供规则分析与研究参考，没有自动交易功能。
