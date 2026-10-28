# 从离线示例理解 StockMan

先运行以下命令；没有凭据也能观察真实的 LangChain 工具调用与 SQLite 状态：

```sh
venv/bin/python scripts/demo.py --offline --scenario routing
venv/bin/python scripts/demo.py --offline --scenario end-to-end
venv/bin/python scripts/chat.py --offline "研究 QQQ"
venv/bin/python scripts/tasks.py --data-dir runtime/offline
```

## 先理解后台主流程

```sh
venv/bin/python service.py --offline --scenario config
venv/bin/python service.py --offline --scenario broker
venv/bin/python service.py --offline --scenario channel
venv/bin/python service.py --offline --scenario service
venv/bin/python service.py --offline --scenario end-to-end
```

新主线从 service.py 开始：读取 INI → 检查环境引用 → 创建 BackendService → 注入券商／通道／模型 → 自动同步／定时研究 → 推送。IM 文字接收是独立辅助路径；图片消息被忽略，不进入业务任务。新增同步和服务健康记录在 broker_health、service_health 中；检查点和原业务表继续复用。

lib/service_config.py 只用标准库读配置，不加载 .env。lib/brokers/base.py 负责完整快照、账户作用域、缓存和串行化；lib/channels/base.py 说明发送能力与接收能力分开。lib/service.py 用相同 PortfolioService、ResearchService、AgentRuntime 与 StockBriefing 组装后台，不重写模型框架。普通同步和轮询不调用模型。

完整演示使用真实线程／SQLite／LangChain 调用循环与假外部依赖，覆盖无人提问自动发报、主动研究、忽略媒体输入、重启去重及未知投递。原 scripts/demo.py 和下面的旧对话学习路径仍保留。

## 一次请求经过哪里

`scripts/chat.py` 或 `lib/application.py` 接收输入，创建本次任务及会话作用域。`lib/agents.py` 使用 LangChain `create_agent` 创建主控，主控的三个工具分别包装三个子 Agent。子 Agent 再使用自己的业务工具，结果由主控汇总。

```text
用户 → Application → 主控 Agent
                     ├ delegate_portfolio → 持仓 Agent → PortfolioService
                     ├ delegate_research  → 研究 Agent → ResearchService
                     └ delegate_monitor   → 监控 Agent → MonitoringService
```

这三条分支按需要选择，不要求每次全部运行。框架负责工具调用循环；我们用普通 Python 处理快照校验、指标、轮询、SQLite 和消息投递。没有手写 LangGraph 状态图，底层 SQLite checkpointer 用来保存主控会话。

`OfflineModel` 是有意可预测的假模型，调用 LangChain 的 `bind_tools` 并返回带 `tool_calls` 的 `AIMessage`。它让你调试工具循环与边界；真实 Gemini 是否正确分工必须另行验证。离线“研究 QQQ”的常见路径是主控→委派工具→研究子 Agent→研究工具→返回子 Agent→返回主控，共四次逻辑模型调用。

## 查看调用记录

每次输出有 `task_id`。复制它并查看：

```sh
venv/bin/python scripts/tasks.py --data-dir runtime/offline --task <任务编号>
```

`traces` 中的 `agent`、`tool`、`phase`、`payload` 与 `at` 表示参与者、工具、执行阶段、参数／耗时／错误和时间。这里没有模型隐藏推理。工具失败时记录类型，任务标为 `partial`；超过预算则任务失败，不继续无限调用。

业务库 `stockman.sqlite` 保存 tasks、traces、snapshots、rules、events、updates 等。`checkpoints.sqlite` 是框架执行状态，不等于业务事实。当前账户快照按券商来源和账户分开；历史图片记录保留，但不进入持仓流程，旧图片任务明确拒绝恢复。新闻输出走当前请求内存，不进检查点。

## 看清哪些步骤无需 Agent

`lib/monitoring.py` 的 `check` 只比较交易时段、报价时间、价位与历史状态，不调用模型。有效事件才交给运行时分析。D6 用假行情强制触发一次、重复检查、关闭并重启数据库、再次检查和停止规则，验证只投递一次。

`lib/portfolio.py` 校验股数、成本、币种和资产类别，调用原有 PA／仓位算法；这些计算交给确定性代码。`lib/research.py` 处理 SEC 报告期与引用；缺数据保留缺失。Agent 负责选择工具、组织解释，不能代替数值校验或授权检查。

## 失败与恢复练习

```sh
STOCKMAN_MAX_MODEL_CALLS=1 venv/bin/python scripts/chat.py --offline "研究 AAPL"
venv/bin/python scripts/tasks.py --data-dir runtime/offline
```

第一条应以退出码 2 结束，任务为 `failed`，原因类型为 `CallLimit`，不是程序假装完成。移除临时环境变量再发起新任务即可。不要将 `failed` 或已完成任务当成待恢复任务；`--resume-task` 只接受仍有待执行检查点的 `interrupted` 任务。

可修改一个合成依赖、写一个可观察断言，然后运行 `scripts/validate.py`。例如把 `SyntheticMarket.quote` 改为无时间戳报价，应没有提醒；真实适配器无需改动。先练习测试与工具边界，再扩展更复杂的分支与恢复。

当前限制与真实接入步骤见 [README.md](README.md)，实际状态见 [Session_Handoff.md](Session_Handoff.md)。

## 每日简报为什么没有再加一堆 Agent

```sh
venv/bin/python scripts/briefing.py --offline --scenario pipeline
venv/bin/python scripts/briefing.py --offline --scenario sources
venv/bin/python scripts/briefing.py --offline --scenario research
venv/bin/python scripts/briefing.py --offline --scenario budget
venv/bin/python scripts/briefing.py --offline --scenario schedule
venv/bin/python scripts/briefing.py --offline --scenario end-to-end
```

这六个演示使用临时库，分别观察通用契约、来源及关系、研究、预算降级、假时钟、完整闭环。`synthetic: true` 表示模型与网络均替换为合成依赖；不是实盘或真实研究质量证明。

```text
普通代码：本人／日期认领 → 明确持仓来源 → SEC/RSS → 时间／权限检查
                                       → 直接关系证据 → 已发送历史去重 → 新增事件分组
LangChain 研究角色：只读取证据 → 选择／解释 → 输出 JSON
普通代码：原文／引用／数字检查 → 中文简报 → 本机历史 → Telegram → sent/failed/unknown
```

`lib/briefing/` 提供不依赖股票的 Evidence、SourcePolicy、History、Budget、Scheduler；`lib/stock_briefing/` 负责持仓、官方素材、关系和简报结构。现有 `create_research_agent` 工厂被按需研究和日报共同使用，日报直接调用研究角色，无需让主控重复分类已知任务。没有手写 LangGraph 状态图。

值得读的路径：`sources.py` 的未知时间拒绝；`relations.py` 如何拒绝仅有公司共现；`agent.py` 如何限制为原文引用；`budget.py` 的 BEGIN IMMEDIATE 如何防并发超额；`schedule.py` 为什么在外部发送前写 sending。超时后无法知道服务端是否完成，所以 unknown 不自动重试。

练习时把 budget 场景的配置从零改为小金额，观察“没有发出模型调用”和“调用超时、用量未知”的区别；把同一 RSS 的正文改一个事实，确认旧标题仍能保留新进展。每次配合测试，运行统一检查。要扩展公司 RSS，先核对许可和完整公司名称，写 fixture 测试，再用真实预览核查，不给模型自己决定来源权限或发送授权。

日报只向模型提供公开材料、股票代码与关系原句，不提供账号、股数或成本。生成、去重和发送的状态由普通代码维护，新闻中的指令无法修改预算或目标会话。研究解释仍需人核查；引用检查不是投资结论正确率测试。
