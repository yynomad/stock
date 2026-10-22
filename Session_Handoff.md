# StockMan 会话交接

更新：2026-10-22，Asia/Shanghai。用户已经确认需求、选型及文档，并要求“确认 开始实现”“继续”；持续执行授权有效，不重新请求开始确认。

## 当前状态与位置

M1—M6 的离线实现与验收已完成，真实外部服务验收仍有待办。当前会话进入每日持仓信息简报的需求澄清：用户明确希望工具每天代为跟踪并解释持仓及上下游消息，已选择北京时间 08:00。日报尚未实现或部署。

物理目录 `/Users/yao/personal/stock/`：既有项目扩展，按用户规则保留原目录。聊天 cwd `/Users/yao/Documents/ChatGPT/stock 2/` 不是物理项目位置。已核对 pwd、Git HEAD、remote 和工作区；没有移动目录或创建 symlink。remote 为 `https://github.com/yynomad/stock.git`。原目录写入通过工具权限机制，未更换位置。

## 已确认决定

个人使用并练习多 Agent。Python；LangChain `create_agent` 主控＋持仓、研究、监控三个子 Agent；不手写 LangGraph 图，底层 SQLite checkpoint 保留。Gemini 默认 `gemini-3.5-flash-lite`，各 Agent 有独立可选配置；不擅自换型号。SQLite、Mac 本机、Telegram＋本地记录、美股／美股 ETF。IBKR 只读与截图共存；Yahoo 行情、SEC 财务、Gemini Google 搜索新闻。详细选项与理由见 [Product.md](Product.md)。

默认值已随文档获批准：5 分钟检查、2% 缓冲、每标的纽约日最多 5 次、常规时段提醒、报价最大 15 分钟、持仓缓存 24 小时标过期、8 次逻辑模型／12 次工具预算、暂时失败最多重试 2 次。SDK 重试不计为新的逻辑模型调用，也不承诺账单硬上限。

## 实现与提交

| 提交 | 完成内容 |
| --- | --- |
| `a8902af` | 具体需求、计划、规则和交接文档 |
| `4ab3060`、`3de6de0` | 修复旧基线导入／测试，锁依赖，隔离统一检查 |
| `ecfd5d1` | LangChain 主控＋三子 Agent、SQLite、预算、结构化调用记录 |
| `4a8f57b` | 限定账户 IBKR 完整快照、截图独立来源、持仓校验与复盘 |
| `068081d` | 带事件时间行情、SEC 报告期、ETF、接地引用与临时新闻输出 |
| `85e9140` | 本人 Telegram 入口、图片、持久监控、交易时段、去重与投递状态 |
| `542b370` | 完整 D6、CLI、学习文档、有限重试、部分失败与投递状态、图片恢复与更多回归测试 |
| `5942907` | 将截图准备纳入任务生命周期；失败有任务／调用记录，识图计入共享模型预算 |
| 包含本记录的提交 | 记录用户最新痛点、每日简报优先级及已选发送时间；仅文档变更 |

入口在 lib/application.py；bot.py 的 main 已接入新应用。lib/agents.py 是实际框架工具循环；lib/storage.py 与 checkpoints.sqlite 分别保存业务及框架状态。lib/offline.py 所有数据／模型均为合成，禁止冒充真实验证。

## 验证结果

- C1 pip check、C2 直接 unittest 和 C3 `venv/bin/python scripts/validate.py`：当前 62 个离线测试通过，AST／文档／配置与 pip check 通过；禁止 socket 连接、隔离凭据及数据目录。
- D1—D6 六个演示通过；D6 完成双来源持仓、股票与 ETF 研究、规则创建、事件假发送、重启去重和停止。`scripts/chat.py --offline`＋`scripts/tasks.py` 读写通过。
- L3 Telegram dry-run 通过，6 段合成文本，没有发送真实消息。
- L2 真实 Gemini 搜索：首次长时间无输出已停止；加总时限后再次返回 `unverified: Gemini probe exceeded 75-second total deadline`，退出码 2。没有来源／用量可验证，不能报接入成功。
- 基础网络诊断：Google API 根地址 HTTP 404（连接可达，不证明模型可用）；Yahoo 根地址 HTTP 429（实际行情未验证）。模型元数据只读探测出现 ConnectTimeout。网络表现不稳定，无法据此判断模型是否有权限或不存在。
- L1 未运行。用户最新明确回复“仍在等待或尚未登录 Gateway”，因此真实持仓与手机并发保持待办。
- 配置存在性检查只记录是否配置：Gemini Key、Telegram Token 和 Chat ID 存在；IBKR_ACCOUNT、SEC_USER_AGENT 未配置。未打印密钥或修改 `.env`。
- 未自动启动真实 Bot、未删 webhook、未发真实 Telegram 消息、未推送 Git 或发布。

## 已知限制和恢复步骤

1. **IBKR 外部前置条件**：等新用户名获批并登录 Gateway，核对联名账户访问权限、Socket API 和 Read-Only API，填 IBKR_ACCOUNT 后运行 L1；人工对比持仓。角色名不能证明只读，SDK 与实际 Gateway 版本兼容性仍未知。
2. **Gemini／搜索**：保留已选模型，检查网络代理、项目权限、额度；重跑 L2 和真实本地 Agent 请求。根地址可达不能解释生成超时的全部原因，没有用未经验证的猜测代替结论。
3. **SEC／Yahoo**：填写 SEC_USER_AGENT 的应用名及联系邮箱，再验收 AAPL／QQQ 的财务、基金资料、行情和来源。Yahoo 限流时缺失提示或跳过事件，不能造数。
4. **Telegram 人工验收**：用户自己启动 bot.py，在本人会话检查复盘、研究、截图、监控状态和停止；打开新闻 HTML 附件验证原文／来源／搜索建议。临时新闻不写入库或检查点，重启不会重发旧附件。
5. **运行边界**：同一数据目录只运行一个进程。监控关键位在创建／重新开始时更新，不自动每日刷新；展示创建和 K 线日期。仅常规时段有效报价触发，无后台持续新闻扫描。
6. **恢复边界**：工具异常任务标 partial；任务、消息投递分开。未知或部分投递不自动重发。中断任务仅有待执行检查点且无后续会话任务时允许手动 resume；截图恢复保留来源 ID。无检查点不能宣称恢复成功。

## 运行与下一步

```sh
cd /Users/yao/personal/stock
venv/bin/python scripts/validate.py
venv/bin/python scripts/demo.py --offline --scenario end-to-end
venv/bin/python scripts/chat.py --offline "研究 QQQ"
venv/bin/python scripts/tasks.py --data-dir runtime/offline
```

真实入口和配置步骤见 [README.md](README.md)，代码学习见 [Learning.md](Learning.md)，逐项验收命令见 [Plan.md](Plan.md)。原版下一步仍是外部服务验收。新增范围按 Product.md 的每日简报要求继续逐项澄清，先准备样例，再选择数据来源／保存、成本及本机运行可靠性方案；不重复已选技术，不将新增范围当成旧授权自动覆盖的实现。

## 本次需求记录与验证

- 用户原意：上班无暇关注几只持仓股票的消息，看到孤立新闻也缺少上下游背景；不要求绝对结论，希望每天推送自己需要知道的事情。
- 用户已明确选择北京时间早上 08:00（Asia/Shanghai）。这是产品发送时间需求，不等于已创建定时任务。
- 现有代码仅支持按需研究及价格事件监控；没有每日新闻采集、上下游关系维护、跨日事件去重与日报调度。
- Product.md 区分明确需求、提出的方案和未决技术；Plan.md 新增 N0—N3 需求依赖记录。尚未改变模型、供应商、数据库或部署。
- 本次仅修改四份项目文档；已运行 venv/bin/python scripts/validate.py：62 个原有离线测试、AST、文档链接／围栏及 pip check 全部通过；git diff --check 通过。原有测试不构成日报验收证据。

## 最新架构决定：通用简报工具（已选 B）

用户将 AI News 与 Stock 识别为相近简报需求，询问是否应抽共用开源工具供 Agent 使用。已检查实际代码：lib/telegram.py 引用 lib/research.py 的 news_html；GoogleNews.fetch 绑定股票代码及股票研究提示；当前尚无通用简报引擎。

已在 Product.md 记录 A 股票单体、B 同仓库模块化复用、C 立即独立库双仓库；用户已明确回复 B。当前物理目录继续为 /Users/yao/personal/stock，既有项目内整理模块，不移动或建立新仓库。用户最新明确“先做 stock 这个项目”，首版仅 Stock，AI News 实现及示例后置。下一步只选择新增日报的数据采集路线，既有 Gemini 型号及按需搜索不重选。维持 Python、LangChain、Gemini、SQLite 等已有选择；没有创建新目录／仓库、服务、依赖或应用代码。若将来明确新项目，仍须按位置规则在 /Users/yao/personal 下创建并预检。

本轮只修改三份文档；venv/bin/python scripts/validate.py 通过：62 个原有离线测试、AST、文档链接／围栏和 pip check 均通过；git diff --check 通过。保留每天北京时间 08:00 的已确认需求，日报来源／保存、成本及运行可靠性仍需澄清。


本轮架构确认记录：只变更 Product.md、Plan.md、AGENTS.md、Session_Handoff.md。用户 B 确认的是同仓库共用模块及分领域 Agent，不自动确认新数据服务、部署、预算或两条完整产品线的首版范围。已运行 venv/bin/python scripts/validate.py：62 个原有离线测试、AST、文档与 pip check 通过；git diff --check 通过。日报功能尚未实现。


## 最新范围确认：先做 Stock

用户明确先做 Stock。继续在现有物理目录 /Users/yao/personal/stock 内工作，保留已选 B 的模块边界，AI News 应用及示例均不纳入第一版。下一项推荐预配置官方公告／SEC／可用 RSS 素材＋Gemini 分析，备选为新增付费新闻 API；尚未选择。本轮只更新文档并核对公开官方资料，未写应用代码、安装依赖或新增外部定时发送。已运行 venv/bin/python scripts/validate.py：62 个原有离线测试、AST、文档及 pip check 通过；git diff --check 通过。这不是新增日报功能的验收。
