# StockMan

配置驱动的美股／ETF 持仓信息助手。通过 SnapTrade 只读同步 IBKR 持仓，结合公开资料生成中文日报、回答研究问题，并通过 Telegram 推送价格提醒。

后台任务独立运行，不依赖用户发消息。项目使用 Python、LangChain、Gemini 和 SQLite，面向单机、单用户、单券商账户；没有网页界面或自动交易功能。

## 功能

- **持仓复盘**：分析指定账户的美元美股／ETF／ADR 子集，保留非美元排除清单和覆盖范围；校验股数、成本和时间，使用确定性代码计算 PA 指标与仓位规则。
- **每日简报**：采集配置的 SEC／RSS 来源，去重并解释重要新增，默认每天北京时间 08:00 生成；恢复后只补当天一份。
- **按需研究**：围绕单只美股或 ETF 选择报价、日线、SEC 财务、申报正文和新闻工具，保留来源、时间、已有证据和缺失资料。
- **价格监控**：按交易日历检查有效报价，达到规则条件时才调用模型并推送；规则与提醒状态持久保存。
- **本地可观察性**：SQLite 保存任务、调用记录、会话检查点、预算估算和投递状态；提供无凭据的离线演示。

项目处于开发阶段：离线流程已有自动化验收，真实 SnapTrade/IBKR、模型、数据源及 Telegram 连接和报告质量仍需在自己的环境中核对。离线演示使用合成数据，不能证明真实持仓或研究效果。

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
| `[broker]` | `provider=snaptrade`，Personal API 凭据与目标账户环境变量名；每 900 秒尝试同步 |
| `[model]` | Gemini 模型名、API key 环境变量名；每任务最多 8 次逻辑模型／12 次工具调用 |
| `[notification]` | `provider=telegram`，token、chat ID、授权用户的环境变量名；`interactive=true` 接收文字提问 |
| `[briefing]` | 日报开关、08:00、预算估算目标、调用／输出限制、资料窗口与历史保留 |
| `[monitor]` | 300 秒检查间隔、2% 突破缓冲、每标的每天最多 5 条、报价最长 900 秒 |
| `[sources]` | 来源配置文件及 SEC 联系身份的环境变量名 |

默认模型为 `gemini-3.5-flash-lite`，使用前需确认自己的 API 账户支持该型号。

服务从**启动进程的环境**读取配置引用的值，**不自动加载 `.env`**。示例配置引用：

| 环境变量 | 用途 |
| --- | --- |
| `SNAPTRADE_CLIENT_ID` | SnapTrade Personal API 的 Client ID |
| `SNAPTRADE_CONSUMER_KEY` | SnapTrade Personal API 的 Consumer Key |
| `SNAPTRADE_ACCOUNT_ID` | SnapTrade 中目标账户 UUID；须核对联名账户 |
| `GEMINI_API_KEY` | 模型及按需 Google 搜索接地 |
| `TG_BOT_TOKEN` | Telegram Bot token |
| `TG_CHAT_ID` | 本人目标私聊的正数数字 ID |
| `TG_ALLOWED_USERS` | 可选，逗号分隔的授权用户 ID；本人私聊可默认使用目标 ID |
| `SEC_USER_AGENT` | 应用名与可联系邮箱，例如 `StockMan your-email@example.com` |

这些环境变量名可在 `.conf` 中调整。密钥、真实 `stock.conf`、数据库和日志不要提交 Git 或粘贴到 Issue。

在 SnapTrade Personal 中先完成 IBKR 授权，再启用 SnapTrade 双重认证并创建 Personal API Key，将 Client ID 与 Consumer Key 放入自己的环境变量；不要提供券商登录密码给本程序。本项目只读取账号和持仓，拒绝失效或非只读的连接，不调用下单、撤单或资金操作接口。账户数据经 SnapTrade 处理，须核对其授权与隐私条款。

目标账户使用 SnapTrade 内部 UUID，而不是 IBKR 的 U 开头账户号。可在自己的本机终端，通过已配置 Client ID、Consumer Key 的环境（或本地 .env）运行以下只读命令。它仅显示账户 UUID、类别和账号尾号；请依据尾号选出联名账户，并把对应 UUID 写入本地 SNAPTRADE_ACCOUNT_ID。不要把命令输出、UUID 或密钥贴到公开位置：

```sh
venv/bin/python scripts/smoke.py --service snaptrade --read-only --list-accounts
```

后台服务从进程环境而非 .env 自动读取这些变量。数据可能延迟：显示 SnapTrade 提供的持仓数据时间，而非本次请求时间；未同步完成、连接失效或时间不可确认时明确报错。空列表如不能证明初始同步完成会视为未知而非空仓。同步不可用时仅使用同一 SnapTrade 账户的缓存；旧 Gateway 缓存不会自动迁移或复用。

首版分析范围为美元美股／ETF／ADR。币种明确为非美元的持仓会保留在排除清单中，不进入美元分析或自动持仓监控。`coverage` 记录纳入／总持仓条目数、排除项和原因；这是按 API 记录计数，不是市值覆盖率，不计算跨币种总额。全是非美元持仓时说明“范围内无持仓”，不认定账户空仓。

只读探测成功返回 `read_verified` 只证明所声明范围的读取/校验通过，需同时查看 `coverage.partial`、纳入数与排除数。探测不打印排除标的明细；持仓分析、日报、监控规则和本地状态会保留相应范围说明及排除清单，部分覆盖的分析标为 `partial`。覆盖信息与持仓原子保存在版本化快照中，重启或故障回退不会丢失；旧缓存未记录覆盖时显示未知。旧版程序不能直接解释新版范围快照，回退运行前须保留数据库备份，勿将子集当完整组合。

探测失败时查看 `failure_code`：`request_failed` 表示请求/SDK 异常；`account_unavailable` 表示目标账户不可用；`holdings_unavailable` 表示持仓不可用或初始同步未完成；`currency_unsupported` 表示币种缺失或格式不明确；`currency_conflict` 表示持仓与标的币种相互矛盾；`asset_unsupported` 表示美元范围里存在未支持资产类型。币种未知、美元条目损坏、重复标的、错误时间或错误账户仍会拒绝，不用排除记录掩盖数据错误。诊断只输出固定错误码，不暴露密钥或账户。

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
研究 AAPL 10-Q 2024-12-28，管理层如何解释营收变化？
研究 QQQ
AAPL 最新价格
AAPL 最近一季营收
开始监控 AAPL
监控状态
停止监控 AAPL
停止监控
```

一次研究一个明确股票代码。监控规则只有在本次用户文字明确要求开始或停止时才变更，工具结果和新闻中的指令不构成授权。配置 `interactive=false` 可只推送、不接收提问。图片消息被忽略，不下载、保存或调用识图模型。

研究 Agent 按问题选择只读工具。同一会话的明确追问可沿用上一只标的。问最新价格通常只需 Yahoo 报价，问最近一季营收可直接取 SEC 季度事实，新闻搜索保留原文及关联搜索建议附件。默认每项任务最多三次研究查询，其中 Gemini 接地搜索最多一次并计入模型调用额度。相同查询复用本次结果，失败查询计入额度。来源超时、缺失、范围拒绝或预算耗尽时，返回缺口和已取得的可核对证据。

### 本地问答与任务记录

```sh
venv/bin/python scripts/chat.py --offline "研究 QQQ"
venv/bin/python scripts/tasks.py --data-dir runtime/offline
```

`chat.py --offline` 使用合成依赖及 `runtime/offline`，输出 `task_id`、模型／工具调用次数、任务状态和本次研究结果。用 `scripts/tasks.py --data-dir runtime/offline --task <任务编号>` 查看角色、工具、耗时和错误。任务详情还会列出本地模型和搜索逻辑调用的用量、耗时及可计算的估算费用，未知值为 null。日报调用使用其配置费率。问答和搜索可按指定模型费率估算，搜索附加费用仍标为未知。

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

日报说明持仓来源／时点、资料截止、事实、与持仓的关系、可能影响、未知、观察点及来源。有可分析材料时，研究角色可按标的补查本次已审核的 SEC／RSS 证据，或为范围内已映射的发行人补查 SEC Company Facts。补充财务附报告期、单位、披露时间和来源，单列为背景。缺少来源或可核验的新增材料时会明确说明。

需要手动发送当天一份时，显式执行 `venv/bin/python scripts/briefing.py --send`。它只向已授权的本人正数私聊 ID 投递，不向群发日报；已确认发送的当天报告不再重发，公开标的预览不能直接转为发送。自动日报由后台 `.conf` 控制，脚本的 `.env` 参数不覆盖后台配置。

旧版 `premarket_grid_calculator.py` 仍可作单次观察列表计算，按配置可能发送消息，不能作为离线验收入口。`bot.py`、截图识别和旧图片 CLI 参数已移除，历史图片任务不能恢复为券商任务；已有本地数据不会自动清理。

## 数据来源与费用

默认来源见 [briefing.sources.json](briefing.sources.json)。包括 9 个发行人的 SEC 申报元数据及美联储货币政策 RSS。元数据确认申报类型和时间；Company Facts 提供标准化财务指标；正文工具读取公司在申报中对业务、经营表现和风险的原文。ETF 不套用公司财务口径，未配置的基金资料会显示缺失。

问答和日报共用 SEC 正文工具。仅对设置 `filing_body_reviewed: true` 且配置了 CIK 的发行人开放，需要配置 `SEC_USER_AGENT`。每次任务最多补查一份 10-K 或 10-Q 的 HTML 主文档，支持管理层讨论、风险因素以及 10-K 的业务章节。给出 YYYY-MM-DD 格式的期末可查找指定报告期，缺省时取 SEC 近期索引内、截至任务时点已披露的最新同类型申报。修订件、附件、8-K、历史归档索引和表格数值解析超出当前范围。

可核验引文附 SEC 接收时间、报告期、申报编号、章节和原文链接。有唯一 HTML 锚点时链接直接定位，否则可在主文档内搜索短引文。正文和候选段落只在当次任务中使用，历史记录可保留经逐字校验的短引文和摘要。请求失败或找不到对应章节时，会显示缺口及已取得的申报信息。

自定义官方 RSS 需逐源审核 `review_url` 和 `reviewed/analyze/store_metadata/store_body/store_analysis` 权限。未审核的来源不采集；正文默认不持久保存；不允许保存分析的来源只作临时展示。可参考 [SEC 开发资料](https://www.sec.gov/about/developer-resources)、[美联储 RSS](https://www.federalreserve.gov/feeds/feeds.htm) 和[版权说明](https://www.federalreserve.gov/disclaimer.htm)。

直接供应／购买／竞争关系必须有材料原句、来源及核对时间，只扩展一层；公司共现不等于存在关系。当前规则可能漏掉复杂表述，180 天旧证据停止扩展；尚无完整公司新闻、行业、ETF 成分或供应链覆盖。

Yahoo 提供行情与日线；SEC 提供适用的公司财务披露。按需 Google 搜索接地作为原文、来源和关联搜索建议的临时包展示，Telegram 使用 HTML 附件，不进入日报库或持久化对话，也不用于发现爬取目标。

日报的可调默认值：

| `.conf` 字段（`[briefing]`） | 单次脚本环境变量 | 默认 |
| --- | --- | --- |
| `time`；时区见 `[service] timezone` | `BRIEFING_TIME` / `BRIEFING_TIMEZONE` | `08:00` / `Asia/Shanghai` |
| `daily_budget_usd` | `BRIEFING_DAILY_BUDGET_USD` | `0.20`，估算目标；`0` 禁用日报模型 |
| `max_calls` | `BRIEFING_MAX_MODEL_CALLS` | `8`，按日累计，预览与重试也计数 |
| `max_report_model_calls` | `BRIEFING_MAX_REPORT_MODEL_CALLS` | `4`，单份日报的模型次数，含重试 |
| `max_research_queries` | `BRIEFING_MAX_RESEARCH_QUERIES` | `3`，单份日报的补充查询次数 |
| `research_timeout_seconds` | `BRIEFING_RESEARCH_TIMEOUT_SECONDS` | `90`，补充调查的步骤截止时间 |
| `max_output_tokens` | `BRIEFING_MAX_OUTPUT_TOKENS` | `2048` |
| `max_events` | `BRIEFING_MAX_EVENTS` | `5` |
| `lookback_hours` | `BRIEFING_LOOKBACK_HOURS` | `24` |
| `retention_days` | `BRIEFING_RETENTION_DAYS` | `30` |
| `input_rate` / `output_rate` | `BRIEFING_INPUT_USD_PER_MILLION` / `BRIEFING_OUTPUT_USD_PER_MILLION` | 模板中可调的每百万 token 估算费率 |

预算通过 SQLite 原子预留，超时保留未知用量估算。日报模型每次 SDK 只尝试一次，最多一次应用重试且重新预留；实际用量可得时结算。

**主动问答任务费用阈值（可选）**：在 stock.conf 的 [model] 中设置 task_budget_usd，并按需一起设置 input_rate 和 output_rate（单位：每百万 Token 美元）。price_model 指定费率适用的精确模型名，省略时使用 [model] 的 name；其他模型和未配置费率的调用显示未知。首次调用结束后按返回用量估算，达到阈值后阻止后续可估价模型调用并提示未完成。无法估算的调用及搜索附加费用仍可继续并标记未知；单次调用可能超过阈值，内部重试可能不可见，不能保证账单封顶。任务内可估价模型调用串行检查；搜索附加费不计入已知金额。默认不启用该阈值。

问答的 `[model] max_research_queries`、`research_timeout_seconds` 默认为 `3`、`90`，本地 CLI 可用 `STOCKMAN_MAX_RESEARCH_QUERIES`、`STOCKMAN_RESEARCH_TIMEOUT_SECONDS` 配置。研究截止时间在开始下一步骤前检查，已发出的请求仍由来源自身的超时机制控制。日报每个模型步骤先从每日账本预留费用。

本地 scripts/chat.py 使用环境变量 STOCKMAN_TASK_BUDGET_USD、STOCKMAN_TASK_PRICE_MODEL、STOCKMAN_TASK_INPUT_RATE 和 STOCKMAN_TASK_OUTPUT_RATE；输入与输出费率须同时设置。任务详情中的 usage_summary 汇总已知费用和费用未知调用；旧任务没有记录时不代表免费。日报继续使用独立的 [briefing] 配置。费用为估算，不能硬性封顶整个供应商账户账单；按需聊天／搜索另计。模型、费率和费用以自己的供应商配置与账单核对。

持仓、必要股数／成本及公开资料可能进入按需模型请求；日报模型只接收公开材料、股票标识和关系证据。账户编号和券商密码不进入模型，LangSmith 云端追踪被禁用，执行记录保存在本机。

## 运行状态与恢复

- **持仓故障**：不保存声明范围内损坏、错账户、未来或乱序快照。非美元资产作为有记录的范围排除，不声称完整账户覆盖；同时间戳却持仓或排除信息变化时拒绝覆盖缓存。只使用同账户缓存，超过 24 小时标旧；无缓存不能冒充空组合。
- **价格监控**：依 XNYS 日历处理交易日、夏令时和提早收盘，仅接受有时间戳的有效常规时段报价；默认超过 15 分钟不提醒。每标的纽约自然日最多 5 条提醒，休眠后不补发伪实时事件。
- **关键位**：从创建规则时的 Yahoo 调整后日线计算，记录 K 线日期；不会自动每日刷新，再次“开始监控 AAPL”可重算。普通轮询不调用模型。
- **延迟日报**：按配置的本地自然日运行，含周末；恢复后只补当天一份。生成前失败每 15 分钟最多尝试三次，不排队补多天历史。
- **未知投递**：超时、部分发送或重启可能留下 `unknown`，先核对 Telegram 和本地状态，程序不盲目重发。网络故障下不能保证绝对一次送达。
- **任务恢复**：任务有 `running/completed/partial/failed/interrupted` 状态。只有确有待执行检查点、且同会话没有后续任务的 `interrupted` 任务可用 `scripts/chat.py --resume-task <任务编号>` 恢复；`completed` 仍需阅读缺失项。

`stockman.sqlite` 保存任务、快照、规则、来源、日报、预算和投递状态，`checkpoints.sqlite` 保存主控会话执行状态。报告历史默认保留 30 天，预算账本与日投递索引保留审计。原始新闻不持久保存，恢复后的输出不保证带有中断前的新闻附件。

### Agent Harness

问答主控和子 Agent 在模型调用前为上下文设置输入预算。后台通过 `[model] context_bytes` 配置，本地问答通过 `STOCKMAN_CONTEXT_BYTES` 配置，默认 49,152 字节。预算估算包含系统提示、工具 schema、消息、工具调用参数与结果。它使用 UTF-8 JSON 大小，供应商 token 用量仍取自独立的用量记录。

当前问题及其工具调用链整体保留。剩余预算按最近顺序容纳已完成的历史轮次，超限或未完成的历史轮次可从本轮输入省略，原检查点保持完整。当前问题和证据自身超限时，在调用供应商前停止。`scripts/tasks.py --task <任务编号>` 的 `context` 轨迹记录前后字节数、上限与省略消息数。恢复和新任务共用会话锁，有后续任务时拒绝恢复旧任务。日报保留独立的证据和预算处理。

会话级排版偏好保存在 SQLite，重启后仍可读取。由本机操作员设置，只有该会话的主控读取，模型没有写入工具。离线操作示例：

```sh
venv/bin/python scripts/chat.py --offline --session demo --response-style detailed
venv/bin/python scripts/chat.py --offline --session demo --response-style show
venv/bin/python scripts/chat.py --offline --session demo --response-style clear
```

可选值为 `concise`、`standard`、`detailed`。管理已有运行目录时显式指定 `--data-dir` 和相同的 `--session`，先停止占用该目录的进程。偏好只用于答复排版，证据和授权仍由现有规则检查。

主控按目标选择角色，在多目标请求中委派子任务并汇总证据。`decision` 轨迹记录模型实际提出的工具步骤或直接作答，工具轨迹用 `span` 和 `parent_tool` 关联委派与子角色执行。步骤表示模型在当前轮选择的动作，执行结果与失败另有记录。

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
| [src/stockman/harness.py](src/stockman/harness.py) | 模型输入预算和按完整轮次筛选历史 |
| `src/stockman/portfolio.py`、`research.py`、`monitoring.py` | 持仓、研究和监控业务 |
| `src/stockman/briefing/` | 条目、权限、去重、预算、历史与调度 |
| `src/stockman/stock_briefing/` | 股票来源、直接关系、证据解释与报告 |
| `scripts/`、`tests/` | 单次操作、离线演示和自动化回归 |

模型只负责按需决策和证据解释；指标、时间、权限、预算、去重及投递状态由普通代码校验。主控按需选择分支，不要求每次调用全部子 Agent；日报直接复用研究角色，没有手写 LangGraph 编排图。

离线模型通过 LangChain 工具协议运行，可用 `scripts/demo.py --offline --scenario routing` 观察委派，用 `scripts/tasks.py` 查看记录。运行 `STOCKMAN_MAX_MODEL_CALLS=1 venv/bin/python scripts/chat.py --offline "研究 AAPL"` 可看到模型调用额度用尽时的 `partial` 结果。

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

真实只读连接检查须在准备好自己的环境后显式执行；Gemini 探测可能产生费用，SnapTrade 结果仍需人工核对账户、股数和数据时效：

```sh
venv/bin/python scripts/smoke.py --service snaptrade --read-only
venv/bin/python scripts/smoke.py --service gemini --symbol AAPL
```

提交问题时请附脱敏的复现步骤、Python／依赖版本和错误类型。修改业务前补充正常、边界及失败路径回归，再运行统一检查；不要提交真实配置、运行数据或本地开发笔记。

## 分析质量评测

[固定案例](evals/cases-v1.json) 包含 24 个自编合成场景，覆盖证据不足、错误引用、数字与时间口径、工具参数、多轮会话、提示注入、权限、失败及预算边界。评测复用现有 Agent 工具循环、日报引用校验和鉴权入口；券商、行情、搜索与消息通道均使用合成资料。评测不启动后台服务，不读取 `.env` 或 `stock.conf`。

默认执行离线协议回放，禁止网络，每次在临时目录运行；结果保存在被忽略的 `runtime/evals/<本次运行>/`：

```sh
venv/bin/python scripts/evaluate.py run --offline --output runtime/evals
# 只检查指定案例；重复的每次尝试都保留，不择优挑选
venv/bin/python scripts/evaluate.py run --offline --case C01 --case F03 --repeat 2
```

每次生成 `report.json`、可直接打开的 `report.html` 和 `review-template.json`。报告包含数据集版本/hash、Git 与代码/提示词标识、固定资料时钟、模型、全部调用/错误/降级、tokens、费用未知和耗时分布。离线响应和 tokens 是预设测试数据；`offline_replay_pass` 只证明流程与检测器通过，不能证明真实模型分析质量。超时或无 usage 不视为免费，SDK 重试关闭，日报仍最多进行一次有记录的应用重试。

`tool_contract` 按问答案例尝试计算，检查预期工具的使用或省略、角色作用域、适用的标的参数，以及是否尝试未经授权的变更。报告给出通过数和分母，单次调用的语义正确性由案例证据和人工评审补充。

真实模型评测必须显式指定 `--live` 和 Gemini 型号，并在当前进程环境设置自己的 `GEMINI_API_KEY`。以下命令会产生供应商费用，业务工具仍是同一组合成资料，不是实盘联调：

```sh
venv/bin/python scripts/evaluate.py run --live --model gemini-3.5-flash-lite --max-cases 4 --repeat 1 --max-calls 12 --call-timeout 30 --timeout 120
```

`--max-calls` 限制每个案例尝试的总模型调用；`--call-timeout` 为单次供应商超时，`--timeout` 为整次隔离执行超时。可同时提供 `--input-rate` 与 `--output-rate`，单位为指定型号每百万 token 的美元估算费率；未提供时费用明确标记未知。估算、调用数和超时不是供应商账单硬上限。鉴权及阈值 guard 始终使用假模型，真实报告将其模拟用量与供应商用量分开。

人工复核时复制 `review-template.json` 为自己的评审文件，填写 `reviewer`、带时区的 ISO `reviewed_at`，并逐项核对任务、原始资料、所有模型响应、工具请求及最终回答。`supported` 检查事实/引用是否支持结论，`uncertainty_clear` 检查缺失/冲突/时效是否说清，`task_completed` 检查是否完成任务；填写 `pass` 或 `fail` 和具体 `reason`，未评保持 `pending`。适用性由数据集预先固定，不能事后用 `not_applicable` 跳过失败项。

```sh
venv/bin/python scripts/evaluate.py review --report runtime/evals/<本次运行>/report.json --input runtime/evals/<本次运行>/my-review.json
venv/bin/python scripts/evaluate.py compare --baseline runtime/evals/<基线>/report.json --candidate runtime/evals/<候选>/report.json
```

导入会更新 JSON/HTML 并保留评审历史；评审与运行记录绑定，修改执行内容后不能套用旧评审。对比要求数据集、模式、阈值、运行配置一致且覆盖完整；模型或提示词变化会列出，不混排离线和真实成绩。

通过标准：离线检查及错误回答控制样本全部通过；越权、泄密、已检出的事实/引用错误等关键失败为零容忍；真实模式普通任务完成率至少 95%，事实支持与不确定性人工评审不得失败。关键词/原文匹配只能发现部分错误，语义支持必须人工复核。少跑案例、运行错误或人工待评都不能宣称完整真实基线通过；真实待评退出 3，执行/判定失败退出 2。首版成本和延迟只做观测，不设置未经测量的费用或 P95 保证。小型合成案例集的结果不代表生产准确率，DeepSeek 等未接入模型不在当前支持范围。

## 限制与许可证

当前仅实现通过 SnapTrade 读取单账户、Telegram 和单账户部署，没有多账户聚合、其他通道、Web 管理台或自动交易。Yahoo 可能延迟或限流；引用匹配只验证来源一致性，不保证模型解释正确。本文中的功能描述不等于真实外部服务验收通过。

项目计划开源，许可证尚未确定，当前没有声明开源许可证。只提供规则分析与研究参考，不承诺投资结论、收益或费用硬上限。
