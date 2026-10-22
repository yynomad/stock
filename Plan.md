# StockMan 实施计划

状态：原 M1—M6 的离线实现与验收完成，原真实服务验收仍待办。用户已明确每日 Stock 简报范围及预算可配置要求；本文已整理新增实施里程碑，待本轮文档评审后执行。日报未实现或部署。

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

## 新增 Stock 日报：待评审实施计划

已选：同仓库共用模块 B；首版仅 Stock；官方公告／SEC／可用 RSS 的 A 路线；直接关联 A；每天北京时间 08:00；恢复后补发当天一份 A；预算由配置控制，不锁定金额。Python、LangChain、Gemini、SQLite、Mac 和 Telegram 沿用。

Product.md 的常规默认与样例一并评审。数据源处理能力逐源记录，配置里任何金额可调整。没有新增待选的主技术栈、云服务、新闻付费订阅或 AI News 实现；没有自动创建计划任务。

### N0：本次产品／计划评审

- 交付物：整合的 Product.md、Plan.md、AGENTS.md、Session_Handoff.md；明确配置默认、素材保存边界、日报结构、失败和验收标准。
- 状态：文档已整理，待用户确认新增日报实现；原 M1—M6 不再重复请求批准。
- 校验：文档结构、交叉链接、决定与状态一致性、Git 差异；本次文档校验通过不构成新增功能验收。

### N1：可复用条目、业务状态与离线最小闭环

前置：N0 确认。可观察结果：同一条合成新闻从来源、去重、报告到假发送，状态可查；重启仍保留。

- 创建 `lib/briefing/` 的条目／证据／来源处理能力／投递契约，复用现有 SQLite 并增加必要表；不得导入股票策略或 IBKR。
- 创建统一入口 `scripts/briefing.py`，默认预览，离线模式不读凭据或连接网络。第一阶段创建下列演示接口，不提前声称已存在。
- 覆盖正常条目、非法时间／链接、来源能力未知、任务失败、SQL 并发及重启；原入口和回归保持。
- 验收命令：C1—C3；`venv/bin/python scripts/briefing.py --offline --scenario pipeline`，应显示合成标记、来源、任务和一次假投递，无真实请求。

### N2：Stock 素材和直接关系证据

前置：N1。可观察结果：按持仓关注范围取得条目与直接关联；每条有来源和时间，失败、未知及旧关系可见。

- 创建 `lib/stock_briefing/`，复用 IBKR／图片和缓存的作用域，不合并来源，不需要券商密码或真实账户就能离线验收。
- 实现预配置官方公告／SEC／RSS 适配，记录健康状态、访问及处理限制。具体来源在接入时查官方资料，不经 Google 接地收集爬取链接；不绕过付费墙。
- 关系从实际材料提取、核验和保存，不用模型记忆冒充来源，不做多层递归。无法确认的关系／ETF 资料说明缺失。
- 覆盖限流、超时、错误 XML／JSON、无时间、同名实体、无关系证据、资产不适用、来源不允许处理、部分来源故障。
- 验收命令：C1—C3；`venv/bin/python scripts/briefing.py --offline --scenario sources`，应同时展示成功来源与失败源、直接关系及拒绝无证据关系。

### N3：新进展识别、Agent 解释和预算降级

前置：N2。可观察结果：同事件不重复堆砌，新进展保留；研究 Agent 产生能核查的简报，预算不足仍返回明确的部分结果。

- 实现 URL／内容／事件标识和跨日报历史对照；区分重复转载与实质更新，保留事件和发布时间，不仅凭标题相似度删除。
- 将日报研究接入现有 LangChain 研究角色，校验事实与引用 ID、关系时点、输出数值和缺失标记。模型不能改变来源权限、预算或发送授权。
- 实现 `BRIEFING_DAILY_BUDGET_USD` 等配置和账本；输入／输出及可重试调用计入预留；SDK 能力不足时采取可见的保守估算，不假称账单硬封顶。
- 配置模板提供建议默认，用户 `.env` 不改；零预算不调用日报模型，无效金额在联网前失败；达到预算／调用限制停止追加调查。
- 覆盖相同转载、同标题新进展、恶意外部文本、未知引用、错误数值、空结果与来源失败区别、零预算、跨日及并发预留、超时用量未知。
- 验收命令：C1—C3；`venv/bin/python scripts/briefing.py --offline --scenario research`；`venv/bin/python scripts/briefing.py --offline --scenario budget`，应分别验证证据与推测、去重及配置降级，无真实模型费用。

### N4：本人日报、08:00 与当天补发

前置：N3。可观察结果：假时钟到时产生一份日报，恢复补发仅当天，重启不重复确认成功的发送，未知投递状态可查。

- 在现有本机应用整合日报调度执行器，时区与时间可配，默认自然日均检查；价格监控仍用原交易日历。未启用自动日报时不发消息。
- 以会话／日报日期唯一标识持久化生成与投递状态，保留计划、实际、资料截止和持仓时点；跨多天恢复不补一串历史日报。
- 复用本人授权、转义、分段和发送层；同一文章多种展示规则按来源执行。临时 Google 新闻包仍走原按需展示路径，不进入新历史。
- 覆盖临界时间、时区配置、日界切换、休眠、无网络、来源部分失败、部分发送、unknown、重启和未经授权的会话。只运行一个应用实例。
- 验收命令：C1—C3；`venv/bin/python scripts/briefing.py --offline --scenario schedule`；`venv/bin/python scripts/smoke.py --service telegram --dry-run`，应无真实消息、无 webhook 修改。

### N5：完整验收、配置和学习交付

前置：N4。可观察结果：完整 Stock 离线闭环、预算可调、故障可解释，已有功能保留，用户可以据说明启用真实日报。

- 完成 `venv/bin/python scripts/briefing.py --offline --scenario end-to-end`，覆盖合成持仓、直接关联、新事件及旧进展、预算、计划、补发、重启与假投递。
- 新增 CLI 的 `--dry-run` 应能生成真实服务预览但不发送；真实 `--send` 为用户显式执行入口，缺本人配置时失败；不得为演示自动运行真实发送。
- 更新 README、Learning.md、env.template 与四份项目文档，记录默认、可调参数、数据去向、费用估算／限额、运行与恢复、来源处理能力及限制。
- 最终离线验收：C1—C3、D1—D6、N1—N4 演示、完整新闭环与 L3，全应通过。没有配置 lint／类型／构建时不编造检查结果。
- 真实公开来源和 Gemini 预览可在不含账户敏感信息的条件下显式验证；IBKR 等用户登录；真实 Telegram 与自动日报由用户在本人会话验收。无法运行的检查保留未验证和恢复步骤。
- 无真实测试不能把 B1—B8 全部勾选完成。预算真实成本以用量记录核对，不能用通用假模型测试代替研究质量评估。

## 新命令状态与原有授权

上文 `scripts/briefing.py` 及其所有参数／场景是计划接口，当前文件尚未创建。N1 创建并验证，N2—N5 按阶段扩展。当前可运行的真实命令仍是原 C1—C3、D1—D6 和 L1—L3，未改变。

本次待确认的是 Stock 日报新增范围及可逆默认；既有框架、型号、数据路线和直接关系范围不重新提问。用户确认后按 N1—N5 连续执行，逐独立验证改动提交；重大选择变化或真实外部阻塞才暂停相关工作。
