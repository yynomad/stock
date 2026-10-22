# StockMan 会话交接

更新：2026-10-22，Asia/Shanghai。
当前阶段：M1 完成；继续 M2，M3—M6 未开始。

## 位置、仓库与授权

- 物理项目目录：`/Users/yao/personal/stock/`，已通过 `pwd -P` 核对。
- 依据：这是现有项目的功能扩展，保留原位置；聊天附带的 `/Users/yao/Documents/ChatGPT/stock 2/` 不作为物理项目目录。
- 没有执行目录移动或创建兼容 symlink。当前工具工作区不含该实际目录的默认写权限；文档写入和提交须通过权限机制。
- remote：`https://github.com/yynomad/stock.git`。
- 规划基线：`84009ce82f2bae6fe98d10a5fe5b5fefa30c962a`，此前检查工作区干净。
- 授权：可以讨论需求、调查代码及官方资料、编写／验证／提交规划文档；用户已明确回复“确认 开始实现”并在中断后要求继续；应用实现已授权，默认值随文档确认。
- 相关文档提交：包含本记录的提交。应用代码仍保持基线。

## 已确认范围与决定

| 项目 | 已确认内容 |
| --- | --- |
| 用途 | 自用并学习多 Agent 开发 |
| MVP | 持仓复盘、单标的研究、价格事件监控的最小闭环 |
| 架构 | 一个主控＋持仓、研究、监控三个业务 Agent |
| 框架 | LangChain 主控／子 Agent，高层接口优先，不手写 LangGraph 编排 |
| 模型 | Gemini，具体选 `gemini-3.5-flash-lite`；用户明确选 B，不能改回此前推荐的 3.8 Flash |
| 入口 | Telegram＋本地执行记录，保留本地图片入口 |
| 标的 | 第一版仅美股及美股 ETF |
| 持仓 | IBKR API 同步，并保留现有图片识别；用户明确要求不删除图片方式 |
| IBKR | 股票在用户和妻子的联名账户；为用户本人添加程序专用第二用户名，主用户名继续日常使用 |
| 行情 | Yahoo/yfinance，检查数据时间，过期不触发提醒 |
| 财务 | SEC 官方披露＋Yahoo 补充资料，ETF 单独解释适用资料 |
| 新闻 | Gemini＋Google 搜索接地 |
| 存储 | SQLite |
| 运行 | Mac 本机；沿用现有 Python 项目 |

详细选项、推荐、用户选择和原因见 `Product.md`，不得重复提问已确认事项。具体轮询参数、时段、来源优先级及执行上限为文档中明确标注的提议默认值，尚不冒充会话确认。

## IBKR 当前真实状态

- 用户已提交程序专用用户名申请。此前截图显示该用户名关联用户本人“第一持有人”，有分配的 `SecondHolder` 角色；角色名不证明只读。
- 截图先显示待定且无待办，随后新用户登录页明确“用户申请已提交待处理”。尚未收到用户确认获批。
- 页面表示正常情况下美东工作日上午 11 点前的申请会在下一工作日结束前处理；这是页面通常时限，不应转换成保证完成时间。
- 尚未验证新用户名能读联名账户、实际权限、手机与 Gateway 并发、Read-Only API 或 API 查询。
- 实施时先用模拟 Gateway；账号就绪后检查账户白名单、持仓一致性和只读配置。不要读取空个人账户并据此报告联名持仓为空，不把不同账户或截图仓位相加。

## 代码现状与已知问题

- 主要模块：`bot.py`、`premarket_grid_calculator.py`、`analyze_image.py`、`lib/{config,positions,yahoo_data,pa_strategy,position_decision,monitor,watchlist,output}.py`。
- 现有持仓获取仅 Gemini 截图；没有 IBKR、LangChain 或 SQLite 的新实现。
- `lib/config.py` 的当前默认模型仍为 `gemini-2.5-flash`，这只是旧实现，尚未改为已选的 3.5 Flash-Lite。
- Gemini 条件导入而无条件定义工具，导致无凭据时导入失败；Telegram 测试还引用已移除函数且使用全局依赖 stubs。
- 固定优先盘前价可能选到旧数据；图片处理存在重复识图路径；持仓未知值、快照来源和运行状态持久化需要补齐。
- README 与当前命令、关注列表行为不一致。仓库无统一依赖清单或验证脚本。修复及新增均列入计划，当前未改。

## 实际验证证据

在 `/private/tmp` 执行：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/Users/yao/personal/stock python3 -B -m unittest discover -s /Users/yao/personal/stock/tests -v
```

结果：5 个仓位决策测试通过，Telegram 测试模块导入失败，退出码 1。当前错误为 `NameError: name 'gemini_types' is not defined`。不能声明所有测试通过；M1 是首个修复阶段。

`git status --short`：规划前无改动。`git rev-parse HEAD` 和 `git remote -v` 已核对。未安装依赖，未启动 Bot，未连接 IBKR，未进行真实 Gemini／搜索／Telegram 验收。

本轮四份文档已在临时目录完成 70 项文档一致性／现有 Python AST 语法检查，退出码 0；涵盖文档结构、交叉链接、明确选定的模型、验收条目、阶段及授权状态。原项目写入时需再次核对文件内容和 Git 差异。实际代码测试的已有失败不因文档校验通过而消失。

## 运行入口与恢复步骤

现有入口仍为：

```sh
venv/bin/python premarket_grid_calculator.py
venv/bin/python premarket_grid_calculator.py --image <图片路径>
venv/bin/python analyze_image.py <图片路径>
venv/bin/python bot.py
```

这些入口会使用实际服务，不能作为无副作用测试。新架构的统一验证、演示和 smoke 脚本尚未创建，准确的计划命令在 `Plan.md`。

## 下一步

1. 本轮落盘及提交以“包含本记录的提交”为凭据；交付前核对四份文档与草稿一致，确认提交仅含四份规划文档。若写入或提交受权限阻塞，明确记录失败，不能宣称已落盘或提交。
2. 用户评审四份文档及 `Product.md` 的提议默认值。向用户提供文档链接、M1—M6 摘要与真实基线失败说明，明确请求文档确认。不再重复请求实现确认。
3. 用户确认后，重新检查物理目录和 Git 状态，先完成 M1，再按计划持续推进；每阶段验证、提交、更新交接。
4. 外部服务不可用时继续已授权且独立的离线工作，保留真实验收的精确阻塞和恢复条件。

## 实施更新：M1 已完成

用户已明确确认文档并授权持续实施；原始规划状态和基线错误段落仅为历史记录。当前首个完成阶段为 M1：12 个离线测试、AST／文档校验与 pip check 通过。依赖锁定在 requirements.txt；现有 Python 3.10.14 可用。未启动真实 Bot 或连接外部金融／模型服务。相关提交：包含本记录的提交。下一步 M2：LangChain Agent-as-tool 和 SQLite 状态、任务及调用记录。
