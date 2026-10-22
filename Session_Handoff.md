# StockMan 会话交接

更新：2026-10-22，Asia/Shanghai。用户已经确认需求、选型及文档，并要求“确认 开始实现”“继续”；持续执行授权有效，不重新请求开始确认。

## 当前状态与位置

M1—M6 的离线实现与验收已完成。真实 IBKR、完整 Gemini／搜索、SEC、Yahoo 数据及 Telegram 用户会话验收仍有待办，产品整体尚未全部真实验收。

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
| 包含本记录的提交 | 完整 D6、CLI、学习文档、有限重试、部分失败与投递状态、图片恢复与更多回归测试 |

入口在 lib/application.py；bot.py 的 main 已接入新应用。lib/agents.py 是实际框架工具循环；lib/storage.py 与 checkpoints.sqlite 分别保存业务及框架状态。lib/offline.py 所有数据／模型均为合成，禁止冒充真实验证。

## 验证结果

- C1 pip check、C2 直接 unittest 和 C3 `venv/bin/python scripts/validate.py`：当前 60 个离线测试通过，AST／文档／配置与 pip check 通过；禁止 socket 连接、隔离凭据及数据目录。
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

真实入口和配置步骤见 [README.md](README.md)，代码学习见 [Learning.md](Learning.md)，逐项验收命令见 [Plan.md](Plan.md)。下一步是用户配置／登录后按外部验收步骤逐项核实，继续修复真实联调暴露的问题。当前无需再做需求确认，不把外部待办写成完成。
