# StockMan 个人持仓助手

配置驱动、可自行运行的持仓信息后台服务。配置券商账户、模型和 IM 后，后台自动同步持仓、生成简报并推送；保留主动文字提问。使用 Python、LangChain 和本机 SQLite，首版单实例单账户，支持美股／美股 ETF。当前提供 IBKR 与 Telegram 适配器，其他平台需要对应适配实现。

每日持仓信息简报和原多 Agent 离线闭环已实现（最终检查记录见 Plan.md）；真实 IBKR 尚待新用户名获批与 Gateway 登录。Gemini 搜索真实探测超过 75 秒总时限，未验证成功；Telegram 真实会话也尚待验收。不要把合成演示数据当成实际持仓或真实研究。

物理项目目录：`/Users/yao/personal/stock/`。完整需求见 [Product.md](Product.md)，实施与逐项验收见 [Plan.md](Plan.md)，学习示例见 [Learning.md](Learning.md)。

## 后台服务：推荐的使用入口

先运行无凭据、无网络、无真实消息的完整演示：

```sh
venv/bin/python service.py --offline --scenario end-to-end
```

实际使用时，先在项目根目录准备配置，再检查，最后由自己启动常驻进程：

```sh
cp stock.conf.example stock.conf
# 编辑 stock.conf；在进程环境中提供其引用的认证值
venv/bin/python service.py --config stock.conf --check
venv/bin/python service.py --config stock.conf
```

配置样例见 [stock.conf.example](stock.conf.example)。`.conf` 采用 INI 语法；只读取显式指定文件，路径相对配置文件所在目录解析，修改后重启生效。`--check` 检查配置／环境引用和来源 JSON，不联网、不调用模型、不发送、不创建业务库。实际 stock.conf 被 Git 忽略。

默认引用 `IBKR_ACCOUNT`、`GEMINI_API_KEY`、`TG_BOT_TOKEN`、`TG_CHAT_ID`，可在 .conf 中改为其他环境变量名；`TG_ALLOWED_USERS` 可选，私聊默认允许目标本人，`SEC_USER_AGENT` 缺少时 SEC 来源会报告失败。新服务不自动读取 .env；需要通过启动进程的环境提供这些值，不能只在旧 .env 中填写后假定会生效。不要把实际值贴入问题或提交 Git。

IBKR 用户名与二次认证仍在 Gateway 完成，.conf 只描述连接和指定只读账户。模板 `[briefing] enabled=true` 表示用户启动该服务后按计划自动推送；安装、检查和离线演示均不启动常驻服务。`bot.py` 已于 2026-10-28 废弃为转发别名，不再有独立的 .env 启动路线；运行 `bot.py` 等同于运行 `service.py`。

服务启动尝试同步指定账户，随后每 900 秒同步（可配）；到点自动研究和推送不依赖 IM 来消息。报价轮询、日报和同步独立运行；IM 辅助接收文字，可配置 interactive=false 只推送。图片消息被忽略，不下载、保存或调用识图模型。同账户请求共享同步结果，失败只使用该账户缓存并显示时间／过期，不能把未同步当成空持仓。

日常只读查看状态，可在服务运行时执行：

```sh
venv/bin/python service.py --config stock.conf --status
```

输出最近记录的同步、任务与投递状态，须核对记录时间，不能据旧记录判断进程仍存活。`unknown` 需到 Telegram 人工核实，不自动重发。Ctrl+C 停止服务；关闭时等待有界退出，不在工作线程仍使用数据库时关闭连接。服务与其他写入 CLI 不能同时占用相同 runtime；只读状态可并行。

新目录 `lib/brokers/` 为只读券商及统一快照契约，`lib/channels/` 为报告发送与入站事件契约，`lib/service.py` 为后台组装与生命周期。依赖、模型、价格规则和日报来源沿用既有方案；配置驱动不意味着所有券商和 IM 已接入，也不意味着已有完整行业新闻覆盖。

## 每天上班前需要知道什么

围绕持仓筛选官方素材，按“发生了什么、为何相关、可能影响、未知、后续观察、来源”生成中文日报。默认北京时间 08:00，自然日含周末；服务恢复后只补当天一份。自动发送只由新服务的 .conf 显式开关控制；下文 .env 的 `BRIEFING_ENABLED` 只影响 `scripts/briefing.py` 的预览／手动发送流程。安装、检查或预览不会自动启动。

先看完整合成演示，无凭据、不联网、不发送：

```sh
venv/bin/python scripts/briefing.py --offline --scenario end-to-end
venv/bin/python scripts/briefing.py --offline --scenario budget
```

真实预览默认不发送；会访问公开来源，存在新材料且预算允许时调用 Gemini。公开标的预览不读取券商：

```sh
venv/bin/python scripts/briefing.py --dry-run --public-symbols AAPL QQQ
venv/bin/python scripts/briefing.py --dry-run
venv/bin/python scripts/briefing.py --status --data-dir runtime
```

第二条读取配置券商账户的持仓或同账户缓存。券商尚未就绪时，可先使用明确标为非实际持仓的 `--public-symbols` 预览或离线演示。

2026-10-28 起移除持仓截图功能；原 `--image`／`--snapshot` 参数会被拒绝，旧 `.env` 中的 `BRIEFING_PORTFOLIO_SOURCE=image` 会明确报错，请移除此旧配置后再运行。已有数据库和本地截图文件不清理；旧中断图片任务不再恢复，也不转换成券商任务。

| 可配置项 | 默认及说明 |
| --- | --- |
| `BRIEFING_ENABLED` | `false`；仅影响 `scripts/briefing.py` 流程；自动日报请用新服务 .conf 的 `briefing.enabled` 并启动 `service.py` |
| `BRIEFING_TIME` / `BRIEFING_TIMEZONE` | `08:00` / `Asia/Shanghai`；更改后按所配本地自然日计日报和预算 |
| `BRIEFING_DAILY_BUDGET_USD` | `0.20`；可任意调小调大，`0` 关闭日报模型并保留来源简报；非有限／负值拒绝 |
| `BRIEFING_MAX_MODEL_CALLS` | `8`；每天跨所有预览及发送共同累计，显式重试也占一次 |
| `BRIEFING_MAX_OUTPUT_TOKENS` | `2048`；单次输出上限 |
| `BRIEFING_MAX_EVENTS` | `5`；未展开的新增数量会注明 |
| `BRIEFING_LOOKBACK_HOURS` / `BRIEFING_RETENTION_DAYS` | `24` / `30`；跨天休眠不补齐全部历史，报告注明缺口 |
| `BRIEFING_INPUT_USD_PER_MILLION` / `BRIEFING_OUTPUT_USD_PER_MILLION` | `0.30` / `2.50`，按已核对的标准文本费率估算；换型号／费率需更新 |
| `BRIEFING_SOURCES_FILE` | 空值使用 [briefing.sources.json](briefing.sources.json)；可改成本机已审核的来源配置 |

日报费用按 SQLite 原子预留，SDK 每次仅一次网络尝试，最多一次应用重试且重新预留；超时保留未知用量估算，不按零元处理。输入按字节加开销保守估算，输出含思考按 SDK usage 结算；这不是供应商账单硬封顶。预览也消耗日报预算，按需聊天／Google 搜索另计。

事实只展示可匹配的原文摘录；数字限在原文，解释分为推测、未知和观察点。引用 ID、原文匹配只能检查来源一致性，不能保证模型推断正确。失效模型／预算不足时回到原文与链接，来源失败不会当作无新闻。

### 来源覆盖的实际范围

默认配置 9 个常见美股发行人的 SEC 申报元数据和美联储货币政策 RSS。SEC 条目只确认申报类型／时间，**没有读取财报或公告正文**，无法仅凭这些判断业绩好坏。QQQ 等 ETF 不套公司财务，未配置基金材料会提示缺失；当前还没有完整的公司新闻、行业、ETF 成分或供应链覆盖。

自定义官方 RSS 需逐源审核 `review_url` 及 `reviewed/analyze/store_metadata/store_body/store_analysis`。未审核拒绝采集；默认不存正文；不允许保存分析的来源仅临时显示，报告库保存限制标记。美联储保留出处；来源政策依据见 Product.md。Google 接地仍仅用于原按需展示，不进入日报库，也不用来发现爬取目标。

直接关系从可信配置的完整公司名称和材料中的明确供应／购买／竞争句式抽取，保留原句、来源与核对时间，只跟一层；不把公司共现当关系。当前严格英语句式识别可能漏报复杂表述，默认没有凭空填入的关系库，180 天旧证据停止扩展。新确认的直接发行人本次即可补抓，仍受每次最多 20 来源限制。

### 启用与恢复

填好已有 `.env` 所需字段及 `SEC_USER_AGENT`，先预览核对来源、持仓时点和成本；自动日报在 `stock.conf` 里设置 `briefing.enabled=true` 并启动 `venv/bin/python service.py --config stock.conf`。本轮没有修改用户 `.env`、启动服务或真实发送。

用户确需手动发送今天一份时，先停止同目录运行中的服务，再显式运行：

```sh
venv/bin/python scripts/briefing.py --send
```

只允许 `TG_CHAT_ID` 对应的正数本人私聊和授权用户，不向群发日报；同一天已确认发送则返回现有状态。手动发送不需要打开自动调度；公开标的预览不能直接转为发送。没有删除 webhook 或修改轮询 offset 的行为。

`--status` 只读显示 `brief_daily/brief_reports`；`unknown` 先在 Telegram 人工核实，程序不自动重发。生成前失败可在当天每 15 分钟重试、最多三次；发送失败或可能部分发送后停止自动重试。保留错误类型，不记录带密钥的原始异常。运行目录有进程锁；预览／发送要先停止同目录运行中的服务。

业务表：`brief_evidence` 保存许可内元数据与指纹，`brief_reports/brief_report_items` 保存报告／引用，`brief_relations` 保存已核对原句，`brief_budget` 保存估算及未知用量，`brief_daily` 保存计划和投递状态。它们在已有本机 SQLite 中，Git 忽略；不是公开数据或云端服务。报告历史默认清理三十天，财务账本和日投递索引保留用于审计。

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

`demo.py` 使用临时目录，退出后清理；`chat.py --offline` 使用 `runtime/offline`，适合查看检查点和跨次对话。离线模式替换模型、行情、券商、SEC 和新闻，输出明确标为合成数据；使用真实 LangChain 工具调用循环和 SQLite，并非真实模型效果验收。统一检查禁止 socket 连接，清空凭据、不读取 `.env`。

## 旧入口的 .env 配置（兼容）

首次可复制 `env.template` 为 `.env`；已有 `.env` 请只补所需字段，不覆盖现有值。运行依赖网络和相应服务权限。

| 配置 | 作用 |
| --- | --- |
| `GEMINI_API_KEY` | 主控、业务 Agent、Google 搜索接地 |
| `GEMINI_MODEL` | 默认 `gemini-3.5-flash-lite`，按已选方案保持一致 |
| `IBKR_ACCOUNT` | 填持仓所在的联名账户 ID，不能用空个人账户代替 |
| `IBKR_HOST` / `IBKR_PORT` | 本机 Gateway 地址及实际 API 端口；模板为 `127.0.0.1:4001`，需核对自己的 Gateway 设置 |
| `IBKR_CLIENT_ID` | 默认 17，避免与其他 API 客户端冲突 |
| `SEC_USER_AGENT` | SEC 要求的应用名与可联系邮箱，例如 `StockMan your-email@example.com` |
| `TG_BOT_TOKEN` / `TG_CHAT_ID` | 本人 Bot 与目标会话 |
| `TG_ALLOWED_USERS` | 允许的数字用户 ID，逗号分隔；本人私聊可默认使用正数 `TG_CHAT_ID` |
| `YF_PROXY` | Yahoo／Telegram 使用的代理；Gemini SDK 使用标准网络代理环境变量 |
| `STOCKMAN_DATA_DIR` | 默认 `runtime`；数据库、检查点仅保存在本机并被 Git 忽略 |

四个 Agent 可分别用 `GEMINI_SUPERVISOR_MODEL`、`GEMINI_PORTFOLIO_MODEL`、`GEMINI_RESEARCH_MODEL`、`GEMINI_MONITOR_MODEL` 覆盖默认值；当前均沿用同一个已选型号。不要在排障时自动更换供应商或开通付费。

模型会收到完成任务所需的持仓与公开资料。已移除截图接收和 Gemini 图片识别。IBKR 密码只在用户自己的 Gateway 登录流程输入，不填入项目。运行时禁用 LangSmith 云端追踪，执行记录留在本地。

### IBKR 准备及核对

1. 等程序专用用户名获批，确认它能访问联名账户；核对实际读取权限，`SecondHolder` 这样的角色名本身不代表只读。
2. 用该用户名在本机 IB Gateway 登录并完成认证。启用 Socket API、勾选 **Read-Only API**，核对端口和受信任的本地地址。
3. 在 `.env` 填联名账户的 `IBKR_ACCOUNT`，运行只读探测。

```sh
venv/bin/python scripts/smoke.py --service ibkr --read-only
```

程序先完成连接握手并核对可访问账户，再用 `reqPositionsMulti` 查询指定账户；没有注册下单、撤单或资金工具。成功后仍需人工对比联名持仓、分数股及成本，并确认手机主用户名与 Gateway 专用用户名能同时使用。API 无法代替你核验界面上的只读开关。

未登录、账户错误、超时或快照未完整返回时，不保存伪完整结果；可用同一账户的旧快照时明确标为缓存，超过 24 小时标为过期。没有同账户缓存时明确失败，请恢复 Gateway 连接后重试。当前固定 SDK 是 PyPI `ibapi`，与实际 Gateway 版本的兼容性仍需真实联调。

## 旧对话和单次入口（兼容）

本地单次任务不发送 Telegram 消息：

```sh
venv/bin/python scripts/chat.py "检查持仓"
venv/bin/python scripts/chat.py "研究 AAPL"
venv/bin/python scripts/tasks.py
venv/bin/python scripts/tasks.py --task <任务编号>
```

真实研究结果中的 `news` 是原文、来源和关联搜索建议的临时展示包；本地 CLI 输出其原始结构，Telegram 则发送 HTML 附件。新闻不进入持久化对话或新闻缓存库；来源中的发布日期不可取得时保持未知。请在 Telegram 客户端下载／打开附件确认内容和建议显示正常。

启动后台服务（唯一启动入口，会连接外部服务；请自己执行）：

```sh
venv/bin/python service.py --config stock.conf
```

`venv/bin/python bot.py` 只是转发到上面这条命令的废弃别名，不要再用它区分两套行为。

在本人会话发送以下自然语言：

```text
检查持仓
研究 AAPL
研究 QQQ
开始监控 AAPL
监控状态
停止监控 AAPL
停止监控
```

代码对监控变更有额外授权检查：本次用户原文明确要求开始／停止才写规则，工具结果中的指令不能替代授权。一次只研究一个明确股票代码。持仓复盘始终使用已配置的券商账户及其同账户缓存。

同一数据目录只运行一个应用进程。先停止服务，再用本地 CLI 操作其数据目录，避免多个进程同时轮询或恢复任务。`Ctrl+C` 停止服务。不会自动删除已有 webhook；若服务的 getUpdates 与 webhook 冲突，先检查自己的 Telegram 配置。

旧版按观察列表进行的单次计算入口继续保留：

```sh
venv/bin/python premarket_grid_calculator.py
```

这些是旧版直接计算入口，不经过新主控；部分旧入口按既有配置可能推送 Telegram，不能用作离线测试。旧 `/test`、`/analyze`、`watchlist.json` 已不作为新 Agent 入口的指令协议或监控配置来源。新规则在 SQLite 中。

## 监控、成本及恢复

普通 Python 每 5 分钟检查一次，仅在美股常规交易时段接受有时间戳、未过期的常规报价。按 XNYS 交易日历处理节假日、夏令时和提早收盘；价格超出支撑／阻力 2% 缓冲才产生事件。每标的纽约自然日最多 5 条提醒，状态与事件 ID 持久去重。参数见模板。

关键位取自创建规则时的 Yahoo 日 K 线，保存 `created_at` 和 `bars_asof`，持续沿用直至再次发送“开始监控 AAPL”重算；本版没有自动每日刷新规则。PA 使用调整后日线收盘价，报告注明 K 线日期，与报价事件时间分开。Yahoo 可能延迟、限流或缺数据；默认超过 15 分钟不提醒，休眠后不补发伪实时事件。

正常轮询不用模型；有效事件才进入主控分析。每任务默认最多 8 次逻辑模型调用、12 次 LangChain 工具调用；Google 搜索计入模型次数，SDK 内部重试不当作新的逻辑调用。暂时性失败最多重试 2 次，发送超时／部分发送标记 `unknown`，不盲目重发。这些预算不构成供应商费用硬上限。

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

Gemini 探测可能消耗额度，总时限 75 秒；未通过返回非零。当前真实 IBKR 取决于用户登录；SEC 尚缺 `SEC_USER_AGENT`；真实 Telegram 及完整研究仍待验收。真实 Gemini 搜索已出现超时，Google 根地址可达，Yahoo 根地址返回 429，这只能帮助排障，不能证明具体数据请求成功。

项目计划开源，正式许可证及发布范围尚待选择；只提供规则分析与研究参考，没有自动交易功能。
