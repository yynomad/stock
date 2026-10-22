# 从离线示例理解 StockMan

先运行以下命令；没有凭据也能观察真实的 LangChain 工具调用与 SQLite 状态：

```sh
venv/bin/python scripts/demo.py --offline --scenario routing
venv/bin/python scripts/demo.py --offline --scenario end-to-end
venv/bin/python scripts/chat.py --offline "研究 QQQ"
venv/bin/python scripts/tasks.py --data-dir runtime/offline
```

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

业务库 `stockman.sqlite` 保存 tasks、traces、snapshots、rules、events、updates 等。`checkpoints.sqlite` 是框架执行状态，不等于业务事实。账户快照按来源和账户／会话分开；图片恢复上下文保留快照 ID。新闻输出走当前请求内存，不进检查点。

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
