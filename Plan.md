# StockMan 实施计划

状态：原 M1—M6 的离线实现与验收完成，原真实服务验收仍待办。用户已明确每日 Stock 简报范围及预算可配置要求；本文已整理新增实施里程碑，用户已明确确认，按 N1—N5 连续执行；真实日报尚未部署。

物理目录 `/Users/yao/personal/stock/`，现有项目扩展，未搬移。需求依据 [Product.md](Product.md)，工作规则见 [AGENTS.md](AGENTS.md)，当前状态见 [Session_Handoff.md](Session_Handoff.md)。

## 里程碑与验收

| 阶段 | 依赖 | 已实现的可观察行为与测试 | 状态／提交 |
| --- | --- | --- | --- |
| M0 | 需求讨论 | 四份具体文档、选型、验收和默认值已获确认 | `a8902af` |
| M1 | M0 | 无凭据导入无网络／线程／日志副作用；修复原有失败；锁定依赖；统一语法、文档与测试入口 | `4ab3060`、`3de6de0` |
| M2 | M1 | 实际 LangChain 主控＋三子 Agent 工具循环；调用记录、预算、错误和 SQLite 重启；会话隔离 | `ecfd5d1` |
| M3 | M2 | IBKR 指定账户、完整快照、分数股、超时／断线；图片只读一次且来源隔离；未知／空头／不适用资产 | `4a8f57b`，真实 L1 待办 |
| M4 | M2；组合依赖 M3 | 报价按事件时间选择；SEC 报告期、单位与修订；ETF 适用性；引用校验、新闻展示与缓存隔离 | `068081d`，真实研究待办 |
| M5 | M3、M4 | 本人 Telegram 文字／图片分派；价格事件、交易时段、乱序／过期过滤、重启去重、每日限额与投递状态 | `85e9140`，真实 Telegram 待办 |
| M6 | M1—M5 | 完整双来源到研究及提醒闭环；本地 CLI、学习与部署说明；补齐部分失败、图片恢复、有限重试和真实验收记录 | `542b370`、`5942907`；离线通过 |

每个独立行为改动配测试并验证后提交；Stop-and-Fix：失败先修复重跑，不削弱断言、不推进依赖失败的工作。旧规划基线为 `84009ce`，当时 5 个仓位测试通过、Telegram 导入失败；已在 M1 修复，不再是当前问题。

## 准确验证命令

以下从项目根目录执行，均已创建。统一入口 C3 在临时目录禁用 dotenv、清空凭据和禁止 socket 连接。

| 编号 | 命令 | 预期结果及实际状态 |
| --- | --- | --- |
| C1 | `venv/bin/python -m pip check` | 无依赖冲突；已通过 |
| C2 | `venv/bin/python -m unittest discover -s tests -v` | 全部离线测试通过；最终结果见交接 |
| C3 | `venv/bin/python scripts/validate.py` | 全量离线测试、Python AST、文档链接／代码围栏、模型配置及依赖检查通过；已通过 |
| D1 | `venv/bin/python scripts/demo.py --offline --scenario routing` | 持仓／研究／监控按需委派，无关请求不调用业务工具；通过 |
| D2 | `venv/bin/python scripts/demo.py --offline --scenario persistence` | 业务任务和 LangChain 会话在重启后保留；通过 |
| D3 | `venv/bin/python scripts/demo.py --offline --scenario portfolio` | 截图持仓独立分析，双来源和失败边界另由测试覆盖；通过 |
| D4 | `venv/bin/python scripts/demo.py --offline --scenario research` | ETF 不套公司财务，缺少新闻明确说明；股票、引用和口径由测试覆盖；通过 |
| D5 | `venv/bin/python scripts/demo.py --offline --scenario monitor` | 有效事件、过期报价与持久化去重；通过 |
| D6 | `venv/bin/python scripts/demo.py --offline --scenario end-to-end` | 模拟 IBKR／截图→股票／ETF 研究→监控→一次假投递→重启去重→停止；通过 |
| L1 | `venv/bin/python scripts/smoke.py --service ibkr --read-only` | 目标联名账户真实读取后人工核对；未运行，用户仍等待或未登录 Gateway |
| L2 | `venv/bin/python scripts/smoke.py --service gemini --symbol AAPL` | 实际模型、接地来源和用量；已运行，75 秒总超时，退出 2，未验证成功 |
| L3 | `venv/bin/python scripts/smoke.py --service telegram --dry-run` | HTML 转义和分段检查，不发送消息；通过 |

另外实测 `scripts/chat.py --offline` 和 `scripts/tasks.py` 的写入、读取闭环。真实 Bot 未自动启动，未修改用户 `.env`，未发真实 Telegram 消息。

## 仍需执行的真实验收

1. **IBKR**：新用户名获批、登录 Gateway、启用只读 Socket API、配置联名账户 ID；L1 成功后人工对比持仓、权限和手机并发。模拟器不证明服务兼容，特别是当前固定的 PyPI `ibapi` SDK。
2. **Gemini／研究**：检查已选模型权限、网络代理及搜索额度，重跑 L2；再从本地 CLI 检查实际 Agent 委派、新闻引用和报告。基础 HTTP 可达不代表生成或搜索成功，不自动换供应商或型号。
3. **SEC／Yahoo**：用户填写真实可联系的 `SEC_USER_AGENT`；公开 AAPL 与 QQQ 分别确认公司财务／基金资料、报告期、价格事件时间和缺失项。基础 Yahoo 地址出现 429，实际数据尚待验收。
4. **Telegram**：用户启动本人 Bot，依次验证文字复盘、单股／ETF 研究、图片、状态、开始及停止；打开新闻 HTML 附件核对原文、来源、关联搜索建议。检查非授权用户被拒绝、长消息可读。发生未知投递先人工核实，避免重复发送。

这些是外部条件和用户端验收待办，不再添加文档批准关卡。全部 AC1—AC8 满足后才能标为产品整体完成；当前交付为离线已验证的实现及明确的恢复步骤。

## 新增 Stock 日报：已确认实施计划

已选：同仓库共用模块 B；首版仅 Stock；官方公告／SEC／可用 RSS 的 A 路线；直接关联 A；每天北京时间 08:00；恢复后补发当天一份 A；预算由配置控制，不锁定金额。Python、LangChain、Gemini、SQLite、Mac 和 Telegram 沿用。

Product.md 的常规默认与样例一并评审。数据源处理能力逐源记录，配置里任何金额可调整。没有新增待选的主技术栈、云服务、新闻付费订阅或 AI News 实现；没有自动创建计划任务。

### N0：本次产品／计划评审

- 交付物：整合的 Product.md、Plan.md、AGENTS.md、Session_Handoff.md；明确配置默认、素材保存边界、日报结构、失败和验收标准。
- 状态：用户已回复“是的”，N0 已确认，执行 N1—N5；原 M1—M6 不再重复请求批准。
- 校验：文档结构、交叉链接、决定与状态一致性、Git 差异；本次文档校验通过不构成新增功能验收。

### N1：可复用条目、业务状态与离线最小闭环（已完成）

前置：N0 确认。可观察结果：同一条合成新闻从来源、去重、报告到假发送，状态可查；重启仍保留。

- 创建 `lib/briefing/` 的条目／证据／来源处理能力／投递契约，复用现有 SQLite 并增加必要表；不得导入股票策略或 IBKR。
- 创建统一入口 `scripts/briefing.py`，默认预览，离线模式不读凭据或连接网络。第一阶段创建下列演示接口，不提前声称已存在。
- 覆盖正常条目、非法时间／链接、来源能力未知、任务失败、SQL 并发及重启；原入口和回归保持。
- 验收命令：C1—C3；`venv/bin/python scripts/briefing.py --offline --scenario pipeline`，应显示合成标记、来源、任务和一次假投递，无真实请求。

### N2：Stock 素材和直接关系证据（已完成）

前置：N1。可观察结果：按持仓关注范围取得条目与直接关联；每条有来源和时间，失败、未知及旧关系可见。

- 创建 `lib/stock_briefing/`，复用 IBKR／图片和缓存的作用域，不合并来源，不需要券商密码或真实账户就能离线验收。
- 实现预配置官方公告／SEC／RSS 适配，记录健康状态、访问及处理限制。具体来源在接入时查官方资料，不经 Google 接地收集爬取链接；不绕过付费墙。
- 关系从实际材料提取、核验和保存，不用模型记忆冒充来源，不做多层递归。无法确认的关系／ETF 资料说明缺失。
- 覆盖限流、超时、错误 XML／JSON、无时间、同名实体、无关系证据、资产不适用、来源不允许处理、部分来源故障。
- 验收命令：C1—C3；`venv/bin/python scripts/briefing.py --offline --scenario sources`，应同时展示成功来源与失败源、直接关系及拒绝无证据关系。

N2 实际验证：75 项离线测试、AST、文档与依赖检查通过；sources 演示含一个正常来源、一个超时来源、一跳关系及无证据拒绝。默认仅 SEC 元数据和美联储货币政策 RSS；未知标的／ETF、未支持语法的关系均显式缺失，不冒充完整行业覆盖。

### N3：新进展识别、Agent 解释和预算降级（已完成）

前置：N2。可观察结果：同事件不重复堆砌，新进展保留；研究 Agent 产生能核查的简报，预算不足仍返回明确的部分结果。

- 实现 URL／内容／事件标识和跨日报历史对照；区分重复转载与实质更新，保留事件和发布时间，不仅凭标题相似度删除。
- 将日报研究接入现有 LangChain 研究角色，校验事实与引用 ID、关系时点、输出数值和缺失标记。模型不能改变来源权限、预算或发送授权。
- 实现 `BRIEFING_DAILY_BUDGET_USD` 等配置和账本；输入／输出及可重试调用计入预留；SDK 能力不足时采取可见的保守估算，不假称账单硬封顶。
- 配置模板提供建议默认，用户 `.env` 不改；零预算不调用日报模型，无效金额在联网前失败；达到预算／调用限制停止追加调查。
- 覆盖相同转载、同标题新进展、恶意外部文本、未知引用、错误数值、空结果与来源失败区别、零预算、跨日及并发预留、超时用量未知。
- 验收命令：C1—C3；`venv/bin/python scripts/briefing.py --offline --scenario research`；`venv/bin/python scripts/briefing.py --offline --scenario budget`，应分别验证证据与推测、去重及配置降级，无真实模型费用。

N3 实际验收：全量 84 项离线测试；research／budget 演示通过。现有 LangChain 研究角色共享工厂，日报只暴露证据、无写入或发送工具。原文摘录及引用 ID 校验，错误数值拒绝后降级；SQLite 按日原子预留，每次重试独立计数，未知用量保留预留。已通过本地 SDK 请求契约确认 attempts=1，输出包含思考的用量由已装 SDK 统一 output_tokens 返回。费用仍是估算。

### N4：本人日报、08:00 与当天补发（已完成）

前置：N3。可观察结果：假时钟到时产生一份日报，恢复补发仅当天，重启不重复确认成功的发送，未知投递状态可查。

- 在现有本机应用整合日报调度执行器，时区与时间可配，默认自然日均检查；价格监控仍用原交易日历。未启用自动日报时不发消息。
- 以会话／日报日期唯一标识持久化生成与投递状态，保留计划、实际、资料截止和持仓时点；跨多天恢复不补一串历史日报。
- 复用本人授权、转义、分段和发送层；同一文章多种展示规则按来源执行。临时 Google 新闻包仍走原按需展示路径，不进入新历史。
- 覆盖临界时间、时区配置、日界切换、休眠、无网络、来源部分失败、部分发送、unknown、重启和未经授权的会话。只运行一个应用实例。
- 验收命令：C1—C3；`venv/bin/python scripts/briefing.py --offline --scenario schedule`；`venv/bin/python scripts/smoke.py --service telegram --dry-run`，应无真实消息、无 webhook 修改。

N4 实际验收：93 项全量测试通过；schedule 与 Telegram dry-run 通过。默认关闭；启用后在现有 Bot 中运行，日／本人唯一认领，今日补发、周末、时区、并发、重启及 unknown 覆盖。生成前失败每 15 分钟最多尝试三次；可能已发送的报告不自动重试。新增实际 runtime 进程锁；截图来源限本人会话。

### N5：完整验收、配置和学习交付（离线完成，真实验收部分待办）

前置：N4。可观察结果：完整 Stock 离线闭环、预算可调、故障可解释，已有功能保留，用户可以据说明启用真实日报。

- 完成 `venv/bin/python scripts/briefing.py --offline --scenario end-to-end`，覆盖合成持仓、直接关联、新事件及旧进展、预算、计划、补发、重启与假投递。
- 新增 CLI 的 `--dry-run` 应能生成真实服务预览但不发送；真实 `--send` 为用户显式执行入口，缺本人配置时失败；不得为演示自动运行真实发送。
- 更新 README、Learning.md、env.template 与四份项目文档，记录默认、可调参数、数据去向、费用估算／限额、运行与恢复、来源处理能力及限制。
- 最终离线验收：C1—C3、D1—D6、N1—N4 演示、完整新闭环与 L3，全应通过。没有配置 lint／类型／构建时不编造检查结果。
- 真实公开来源和 Gemini 预览可在不含账户敏感信息的条件下显式验证；IBKR 等用户登录；真实 Telegram 与自动日报由用户在本人会话验收。无法运行的检查保留未验证和恢复步骤。
- 无真实测试不能把 B1—B8 全部勾选完成。预算真实成本以用量记录核对，不能用通用假模型测试代替研究质量评估。

## 最终命令状态与验收证据

- 统一检查 `venv/bin/python scripts/validate.py`：104 项离线测试、AST、文档结构／链接及 pip check 通过。原 62 项回归保留；未配置 lint／类型／build，不编造结果。
- 原 D1—D6 六场景、新 `scripts/briefing.py --offline --scenario pipeline/sources/research/budget/schedule/end-to-end` 六场景、L3 Telegram dry-run 全通过（每个场景分别执行，非将斜线字符串传给 CLI）。
- 完整合成验收实际经过 PortfolioService 图片快照、SEC/RSS 适配、关系核对后同次补抓直接发行人、研究角色、预算、报告／历史、假时钟、两次假投递、重启及 unknown 不重发。合成快照时间由固定时钟决定，不依赖真实时间。
- 2026-10-22 真实公开预览：`BRIEFING_LOOKBACK_HOURS=168 BRIEFING_MAX_MODEL_CALLS=1 venv/bin/python scripts/briefing.py --dry-run --public-symbols AAPL QQQ --data-dir runtime/public-check`。美联储 RSS 连通、格式有效，窗口内 0 条；SEC 因缺有效 SEC_USER_AGENT 报 ValueError；QQQ 基金材料未配置，输出 partial。没有调用模型（费用账本 0）、没有读取券商或发送 Telegram。该结果只证明实际 RSS 和降级流程，不证明真实 Gemini 研究质量。
- 新 CLI 默认真实预览；`--send` 只允许用户配置的本人私聊且当天一次，未实际执行；`--status` 只读本地状态。启用说明见 README.md，阶段学习见 Learning.md。
- N1 `585012c`、N2 `7a78d7c`、N3 `2bb2ef9`、N4 `9dfcea5`；N5 为包含本记录的提交。

用户已确认新增 Stock 日报文档，N1—N5 按授权实施，不重复请求批准。下一步需要用户的外部条件：设置真实 SEC 联系信息、IBKR 用户名获批并登录 Gateway／核对联名账户，以及本人 Telegram 和 Gemini 的实际质量／费用验收。不要启动 Bot 或发送真实测试来冒充这些步骤完成。

## 2026-10-26：交互设计调整（讨论中，未开始实现）

- 触发：用户要求重新梳理为后台服务，用户截图／提问不再作为产品主入口；截图原为 IBKR 未就绪时的临时方式。
- 影响：产品说明和后续主流程围绕配置、后台任务、结果查看、推送与异常处理组织；既有 Bot 对话不再定义服务的运行生命周期，需在方案确定后评估相应代码调整。
- 当前下一步：先确定管理交互采用控制台还是配置文件／命令行；再逐项明确初始化、日常查看和异常处理。界面、部署和新增框架未批准，不据此安装依赖或改应用代码。
- 既有 N1—N5 验收不重做、不冒充新交互已实现；新方案需求和关键技术明确后，更新可验收里程碑并交付文档确认。
- 本次仅记录用户已明确的方向及待选项；执行现有统一检查验证文档与回归，结果见 Session_Handoff.md。
