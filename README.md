# StockMan

配置驱动的美股／ETF 持仓信息助手。通过 IBKR 只读同步持仓，结合公开资料生成中文日报、回答研究问题，并通过 Telegram 推送价格提醒。

后台任务独立运行，不依赖用户发消息。项目使用 Python、LangChain、Gemini 和 SQLite，面向单机、单用户、单券商账户；没有网页界面或自动交易功能。

## 功能

- **持仓复盘**：读取指定账户的完整快照，校验股数、成本和时间，使用确定性代码计算 PA 指标与仓位规则。
- **每日简报**：采集配置的 SEC／RSS 来源，去重并解释重要新增，默认每天北京时间 08:00 生成；恢复后只补当天一份。
- **按需研究**：支持单只股票或 ETF，区分已取得证据、潜在影响与缺失资料，保留原始引用。
- **价格监控**：按交易日历检查有效报价，达到规则条件时才调用模型并推送；规则与提醒状态持久保存。
- **本地可观察性**：SQLite 保存任务、调用记录、会话检查点、预算估算和投递状态；提供无凭据的离线演示。

项目处于开发阶段：离线流程已有自动化验收，真实 IBKR、模型、数据源及 Telegram 连接和报告质量仍需在自己的环境中核对。离线演示使用合成数据，不能证明真实持仓或研究效果。

## 目录

- [快速开始](#快速开始)
- [配置和启动](#配置和启动)
- [日常使用](#日常使用)
- [数据来源与费用](#数据来源与费用)
- [运行状态与恢复](#运行状态与恢复)
- [架构与代码导航](#架构与代码导航)
- [开发与验证](#开发与验证)
- [限制与许可证](#限制与许可证)

## 快速开始

需要 Python 3.10+；目前安装与离线验收使用 macOS、Python 3.10.14。

```sh
git clone https://github.com/yynomad/stock.git
cd stock
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
venv/bin/python -m pip install -e .
venv/bin/stockman --offline --scenario end-to-end
```

演示无需密钥，不访问真实券商、行情或模型，也不发送消息。`synthetic: true` 表示外部依赖已替换为合成实现。

`requirements.txt` 是完整的精确依赖锁定；[pyproject.toml](pyproject.toml) 声明包信息和直接依赖下限。先安装锁定依赖，再安装当前包。安装后 `stockman` 命令与根 `service.py` 共用同一个 CLI，参数一致；跨目录运行时使用虚拟环境中命令的完整路径和配置文件路径。

## 配置和启动

### 1. 准备配置与连接

```sh
cp stock.conf.example stock.conf
```

编辑 [stock.conf.example](stock.conf.example) 的本地副本。配置采用 INI，文件路径相对配置文件所在目录解析，修改后重启生效。

| 配置组 | 主要内容与示例默认值 |
| --- | --- |
| `[service]` | 数据目录 `runtime`、时区 `Asia/Shanghai`、日志级别 |
| `[broker]` | `provider=ibkr`，Gateway 地址、端口、client ID、账户环境变量名；每 900 秒同步 |
| `[model]` | Gemini 模型名、API key 环境变量名；每任务最多 8 次逻辑模型／12 次工具调用 |
| `[notification]` | `provider=telegram`，token、chat ID、授权用户的环境变量名；`interactive=true` 接收文字提问 |
| `[briefing]` | 日报开关、08:00、预算估算目标、调用／输出限制、资料窗口与历史保留 |
| `[monitor]` | 300 秒检查间隔、2% 突破缓冲、每标的每天最多 5 条、报价最长 900 秒 |
| `[sources]` | 来源配置文件及 SEC 联系身份的环境变量名 |

默认模型为 `gemini-3.5-flash-lite`，使用前需确认自己的 API 账户支持该型号。

服务从**启动进程的环境**读取配置引用的值，**不自动加载 `.env`**。示例配置引用：

| 环境变量 | 用途 |
| --- | --- |
| `IBKR_ACCOUNT` | 实际持仓账户 ID，须能通过已登录 Gateway 访问 |
| `GEMINI_API_KEY` | 模型及按需 Google 搜索接地 |
| `TG_BOT_TOKEN` | Telegram Bot token |
| `TG_CHAT_ID` | 本人目标私聊的正数数字 ID |
| `TG_ALLOWED_USERS` | 可选，逗号分隔的授权用户 ID；本人私聊可默认使用目标 ID |
| `SEC_USER_AGENT` | 应用名与可联系邮箱，例如 `StockMan your-email@example.com` |

这些环境变量名可在 `.conf` 中调整。密钥、真实 `stock.conf`、数据库和日志不要提交 Git 或粘贴到 Issue。

IBKR 认证在自己的 IB Gateway 中完成。启用 Socket API，勾选 **Read-Only API**，核对端口、可信地址及所选账户权限。程序不接收券商密码，也没有下单、撤单或资金操作工具。指定账户不可用时，只允许读取该账户的缓存；没有缓存会明确失败。

### 2. 检查配置

```sh
venv/bin/stockman --config stock.conf --check
```

此命令检查配置、环境引用和来源 JSON，不联网、不调用模型、不发送消息、不创建业务数据库。配置通过不代表外部连接已验证。

### 3. 启动与停止

```sh
venv/bin/stockman --config stock.conf
```

启动时尝试同步持仓，随后独立运行同步、日报、价格监控和可选文字接收。示例配置的 `briefing.enabled=true` 会在服务启动后按计划生成并推送；需要关闭自动日报时，将其设为 `false`。安装、检查与离线演示不会启动真实服务。

用 `Ctrl+C` 停止。一个数据目录只允许一个应用写入进程；运行本地问答或日报预览前，先停止使用同一目录的服务。只读状态查询可以并行：

```sh
venv/bin/stockman --config stock.conf --status
```

状态显示已记录的同步、任务与投递结果，需核对记录时间；它不证明进程仍存活。

## 日常使用

### Telegram 文字问答

在授权的本人会话中输入：

```text
检查持仓
研究 AAPL
研究 QQQ
开始监控 AAPL
监控状态
停止监控 AAPL
停止监控
```

一次研究一个明确股票代码。监控规则只有在本次用户文字明确要求开始或停止时才变更，工具结果和新闻中的指令不构成授权。配置 `interactive=false` 可只推送、不接收提问。图片消息被忽略，不下载、保存或调用识图模型。

### 本地问答与任务记录

```sh
venv/bin/python scripts/chat.py --offline "研究 QQQ"
venv/bin/python scripts/tasks.py --data-dir runtime/offline
```

`chat.py --offline` 使用合成依赖及 `runtime/offline`，输出 `task_id`、模型／工具调用次数和任务状态。用 `scripts/tasks.py --data-dir runtime/offline --task <任务编号>` 查看对应的 `traces`；它记录角色、工具、耗时和错误，不包含模型隐藏推理。

去掉 `--offline` 可进行真实本地问答，结果打印到终端，不发送 Telegram。单次脚本沿用环境配置：可将 [env.template](env.template) 复制为 `.env`，已有文件只补必要字段。它与后台 `.conf` 的加载方式不同；代理、模型角色覆盖和日报参数以模板为准。

### 日报预览与手动发送

```sh
# 仅使用公开标的，不读取实际持仓；真实来源和模型可能产生费用
venv/bin/python scripts/briefing.py --dry-run --public-symbols AAPL QQQ
# 读取指定账户持仓或同账户缓存；不发送消息
venv/bin/python scripts/briefing.py --dry-run
# 只读日报状态
venv/bin/python scripts/briefing.py --status --data-dir runtime
```

日报说明持仓来源／时点、资料截止、事实、与持仓的关系、可能影响、未知、观察点及来源。没有重要新增时简短说明；来源失败或覆盖不足单独列出，不当作“没有新闻”。

需要手动发送当天一份时，显式执行 `venv/bin/python scripts/briefing.py --send`。它只向已授权的本人正数私聊 ID 投递，不向群发日报；已确认发送的当天报告不再重发，公开标的预览不能直接转为发送。自动日报由后台 `.conf` 控制，脚本的 `.env` 参数不覆盖后台配置。

旧版 `premarket_grid_calculator.py` 仍可作单次观察列表计算，按配置可能发送消息，不能作为离线验收入口。`bot.py`、截图识别和旧图片 CLI 参数已移除，历史图片任务不能恢复为券商任务；已有本地数据不会自动清理。

## 数据来源与费用

默认来源见 [briefing.sources.json](briefing.sources.json)：9 个美股发行人的 SEC 申报元数据及美联储货币政策 RSS。SEC 元数据只能确认申报类型和时间，**不包含财报／公告正文**。ETF 不套用公司财务口径，未配置的基金资料会显示缺失。

自定义官方 RSS 需逐源审核 `review_url` 和 `reviewed/analyze/store_metadata/store_body/store_analysis` 权限。未审核的来源不采集；正文默认不持久保存；不允许保存分析的来源只作临时展示。可参考 [SEC 开发资料](https://www.sec.gov/about/developer-resources)、[美联储 RSS](https://www.federalreserve.gov/feeds/feeds.htm) 和[版权说明](https://www.federalreserve.gov/disclaimer.htm)。

直接供应／购买／竞争关系必须有材料原句、来源及核对时间，只扩展一层；公司共现不等于存在关系。当前规则可能漏掉复杂表述，180 天旧证据停止扩展；尚无完整公司新闻、行业、ETF 成分或供应链覆盖。

Yahoo 提供行情与日线；SEC 提供适用的公司财务披露。按需 Google 搜索接地作为原文、来源和关联搜索建议的临时包展示，Telegram 使用 HTML 附件，不进入日报库或持久化对话，也不用于发现爬取目标。

日报的可调默认值：

| `.conf` 字段（`[briefing]`） | 单次脚本环境变量 | 默认 |
| --- | --- | --- |
| `time`；时区见 `[service] timezone` | `BRIEFING_TIME` / `BRIEFING_TIMEZONE` | `08:00` / `Asia/Shanghai` |
| `daily_budget_usd` | `BRIEFING_DAILY_BUDGET_USD` | `0.20`，估算目标；`0` 禁用日报模型 |
| `max_calls` | `BRIEFING_MAX_MODEL_CALLS` | `8`，按日累计，预览与重试也计数 |
| `max_output_tokens` | `BRIEFING_MAX_OUTPUT_TOKENS` | `2048` |
| `max_events` | `BRIEFING_MAX_EVENTS` | `5` |
| `lookback_hours` | `BRIEFING_LOOKBACK_HOURS` | `24` |
| `retention_days` | `BRIEFING_RETENTION_DAYS` | `30` |
| `input_rate` / `output_rate` | `BRIEFING_INPUT_USD_PER_MILLION` / `BRIEFING_OUTPUT_USD_PER_MILLION` | 模板中可调的每百万 token 估算费率 |

预算通过 SQLite 原子预留，超时保留未知用量估算。日报模型每次 SDK 只尝试一次，最多一次应用重试且重新预留；实际用量可得时结算。费用为估算，不能硬性封顶整个供应商账户账单；按需聊天／搜索另计。模型、费率和费用以自己的供应商配置与账单核对。

持仓、必要股数／成本及公开资料可能进入按需模型请求；日报模型只接收公开材料、股票标识和关系证据。账户编号和券商密码不进入模型，LangSmith 云端追踪被禁用，执行记录保存在本机。

## 运行状态与恢复

- **持仓故障**：不保存不完整、错账户、未来或乱序快照。只使用同账户缓存，超过 24 小时标旧；无缓存不能冒充空组合。
- **价格监控**：依 XNYS 日历处理交易日、夏令时和提早收盘，仅接受有时间戳的有效常规时段报价；默认超过 15 分钟不提醒。每标的纽约自然日最多 5 条提醒，休眠后不补发伪实时事件。
- **关键位**：从创建规则时的 Yahoo 调整后日线计算，记录 K 线日期；不会自动每日刷新，再次“开始监控 AAPL”可重算。普通轮询不调用模型。
- **延迟日报**：按配置的本地自然日运行，含周末；恢复后只补当天一份。生成前失败每 15 分钟最多尝试三次，不排队补多天历史。
- **未知投递**：超时、部分发送或重启可能留下 `unknown`，先核对 Telegram 和本地状态，程序不盲目重发。网络故障下不能保证绝对一次送达。
- **任务恢复**：任务有 `running/completed/partial/failed/interrupted` 状态。只有确有待执行检查点、且同会话没有后续任务的 `interrupted` 任务可用 `scripts/chat.py --resume-task <任务编号>` 恢复；`completed` 仍需阅读缺失项。

`stockman.sqlite` 保存任务、快照、规则、来源、日报、预算和投递状态，`checkpoints.sqlite` 保存主控会话执行状态。报告历史默认保留 30 天，预算账本与日投递索引保留审计。原始新闻不持久保存，恢复后的输出不保证带有中断前的新闻附件。

## 架构与代码导航

```text
stockman / service.py → 配置校验 → BackendService
                                  ├ 券商同步 → 持仓快照 → 业务计算
                                  ├ 日报调度 → SEC/RSS → 去重/预算 → 研究 → 推送
                                  ├ 价格监控 → 有效事件 → Agent 分析 → 推送
                                  └ Telegram 文字 → 主控 → 持仓/研究/监控子 Agent
```

| 路径 | 职责 |
| --- | --- |
| [src/stockman/cli.py](src/stockman/cli.py) | CLI 参数及执行入口 |
| [src/stockman/service_config.py](src/stockman/service_config.py)、[service.py](src/stockman/service.py) | INI 校验、依赖组装、后台生命周期 |
| `src/stockman/brokers/`、`channels/` | 券商快照和消息通道契约，以及 IBKR／Telegram 适配器 |
| [src/stockman/agents.py](src/stockman/agents.py) | LangChain 主控与三个子 Agent、调用限制、日志和会话检查点 |
| `src/stockman/portfolio.py`、`research.py`、`monitoring.py` | 持仓、研究和监控业务 |
| `src/stockman/briefing/` | 条目、权限、去重、预算、历史与调度 |
| `src/stockman/stock_briefing/` | 股票来源、直接关系、证据解释与报告 |
| `scripts/`、`tests/` | 单次操作、离线演示和自动化回归 |

模型只负责按需决策和证据解释；指标、时间、权限、预算、去重及投递状态由普通代码校验。主控按需选择分支，不要求每次调用全部子 Agent；日报直接复用研究角色，没有手写 LangGraph 编排图。

离线模型通过真实 LangChain 工具协议运行，可用 `scripts/demo.py --offline --scenario routing` 观察委派，用 `scripts/tasks.py` 查看记录。模型调用数限制可通过 `STOCKMAN_MAX_MODEL_CALLS=1 venv/bin/python scripts/chat.py --offline "研究 AAPL"` 练习：预期因 `CallLimit` 失败并退出 2；移除临时限制后重新发起任务。

## 开发与验证

从项目根目录执行：

```sh
venv/bin/python scripts/validate.py
```

统一检查解析 Python AST、检查 README 结构及本地链接、在临时目录禁用网络并运行全部单元测试，最后执行 `pip check`。新拉取的仓库不需要任何本地开发计划或交接文件即可验证。

可按功能运行离线场景，每个场景名分别传入 `--scenario`：

| 命令 | 场景 |
| --- | --- |
| `venv/bin/python scripts/demo.py --offline --scenario <场景>` | `routing`、`persistence`、`portfolio`、`research`、`monitor`、`end-to-end` |
| `venv/bin/python scripts/briefing.py --offline --scenario <场景>` | `pipeline`、`sources`、`research`、`budget`、`schedule`、`end-to-end` |
| `venv/bin/stockman --offline --scenario <场景>` | `config`、`broker`、`channel`、`service`、`end-to-end` |

Telegram 格式检查使用 `venv/bin/python scripts/smoke.py --service telegram --dry-run`，不发送消息。完整场景使用假模型、假时钟和假发送器，验证权限、来源隔离、预算、重启去重及未知投递。

真实只读连接检查须在准备好自己的环境后显式执行；Gemini 探测可能产生费用，IBKR 结果仍需人工核对账户、股数与只读设置：

```sh
venv/bin/python scripts/smoke.py --service ibkr --read-only
venv/bin/python scripts/smoke.py --service gemini --symbol AAPL
```

提交问题时请附脱敏的复现步骤、Python／依赖版本和错误类型。修改业务前补充正常、边界及失败路径回归，再运行统一检查；不要提交真实配置、运行数据或本地开发笔记。

## 限制与许可证

当前仅实现 IBKR、Telegram 和单账户部署，没有多账户聚合、其他通道、Web 管理台或自动交易。Yahoo 可能延迟或限流；引用匹配只验证来源一致性，不保证模型解释正确。本文中的功能描述不等于真实外部服务验收通过。

项目计划开源，许可证尚未确定，当前没有声明开源许可证。只提供规则分析与研究参考，不承诺投资结论、收益或费用硬上限。
