# 长任务后台任务化方案

## 现状说明

本文档仍主要面向 Telegram 侧的未来 job 化设计。

截至当前仓库状态，微信前端已经先行落地了一个更轻量的后台执行版本：普通消息会转入进程内 `asyncio.Task` 后台运行，支持主动状态推送、进度查询和 `/cancel`。相关实现边界与设计取舍见 `docs/weixin-plan-mode-implementation-plan.md`。

## 背景

当前 Telegram 长任务处理链路是前台同步执行：`src/ragtag_crew/telegram/bot.py` 在收到普通消息后直接 `await session.prompt(text)`。这意味着一次对话请求会同时承担以下职责：

- 接收用户输入
- 持有 Telegram 占位消息并持续流式更新
- 驱动整个 agent loop 直到完成
- 在同一条请求链路里处理超时、取消和错误

这套模型对短任务足够简单，但对长任务存在明显瓶颈：

1. 整个 agent 回合受 `settings.turn_timeout` 限制，默认 `360s`，长任务容易在整体编排尚未结束时被截断。
2. `/cancel` 当前本质上只是给 `AgentSession` 发中止信号；如果此时正在执行长工具，无法保证立即停止。
3. 任务执行和聊天消息处理强耦合，用户追问“进度如何”虽然能拿到快照，但并没有真正的后台作业对象可供查询。
4. 任务完成后缺少稳定的“主动通知 + 可手查”闭环，无法形成真正的长任务体验。

结合现有日志与 `data/traces/*.jsonl` 的执行轨迹，当前多次长任务失败都呈现同一模式：单个工具可能成功，但多轮 LLM 与工具累计耗时最终撞上整轮超时，而不是单点错误。

## 目标

本方案的目标不是一次性做成分布式任务系统，而是在当前单进程架构内补齐最小可用的后台任务能力：

1. 普通长任务可以脱离当前消息处理链路，在后台继续执行。
2. 用户提交任务后立即收到“已受理”的确认，而不是等待完整执行结束。
3. 任务执行期间支持手动查询状态、进度、最近工具和结果摘要。
4. 任务完成、失败或取消后，机器人会主动推送结果通知。
5. `/cancel` 不再只是设置抽象标记，而是能直接取消后台 runner task，从而更快打断长工具。

## 非目标

本阶段明确不做以下内容：

1. 不引入 Redis、Celery、RQ 等外部任务队列。
2. 不做多进程 / 多机 worker 调度。
3. 不做运行中任务跨进程重启自动续跑。
4. 不重写 `AgentSession` 主循环，只在外围补一层后台任务编排。
5. 不把所有前端统一抽象成通用 job framework；先优先落在 Telegram。

## 设计原则

1. 最小正确改动：尽量复用现有 `AgentSession`、`TelegramStreamer`、`TraceCollector`。
2. 任务对象显式化：后台任务必须有稳定的 `job_id`、状态、开始时间、结束时间和结果摘要。
3. 前后台解耦：消息 handler 负责提交任务，后台 runner 负责执行任务。
4. 可观测优先：任务状态、取消原因、超时原因、最近活动都要能落到内存态或持久化元数据。
5. 失败可解释：失败结果不能只剩异常栈，至少要能向用户返回任务失败原因与最近执行阶段。

## 当前链路中的关键问题

### 1. 前台同步执行

`telegram/bot.py` 的 `_handle_message()` 在非 busy 情况下创建占位消息、注册 streamer 和 trace collector 后，直接执行：

```python
await session.prompt(text)
```

这导致 handler 生命周期和 agent 生命周期完全一致，任何长任务都会把这条消息处理链路占满。

### 2. 缺少真正的任务实体

现在系统里有“session 忙碌态”，但没有“任务对象”。因此：

- 用户只能知道“当前忙不忙”，不能知道“这是第几个任务、何时提交、何时结束、结果在哪”
- 无法稳定保留最近一次后台任务的终态记录
- 无法自然扩展出 `/tasks`、`/task <id>` 这类查询命令

### 3. 取消粒度不够

当前 `/cancel` 调用 `session.abort()`。这对 LLM 流式阶段有效，但如果后台已经进入长工具执行，往往要等工具本身返回或抛出异常后才能完全退出。

如果把执行单元提升为独立的后台 `asyncio.Task`，则可以在保留 `session.abort()` 的同时，直接取消 runner task，并让底层工具链路通过 `CancelledError` 尽快回收。

### 4. 全局超时与长任务需求冲突

当前 `turn_timeout` 是整轮超时保护，对交互式短任务是合理的，但对“下载、编译、测试、基准、抓取、批量处理”这类任务偏紧。

因此不应简单粗暴地把全局 `turn_timeout` 一起放大，而应为后台任务引入独立的执行超时语义，例如 `job_timeout`。

## 方案概览

引入一个 Telegram 侧最小后台任务层：

1. 长任务通过显式后台入口提交，而不是直接复用当前普通消息链路。
2. handler 创建一个 `JobRecord`，生成 `job_id`，立即回复“任务已创建”。
3. `JobManager` 使用 `asyncio.create_task(...)` 启动后台 runner。
4. runner 内部复用现有 `AgentSession`、`TelegramStreamer`、`TraceCollector` 完成真正执行。
5. 任务状态在运行期间可通过命令查询。
6. 任务结束后由 runner 主动向 Telegram 推送完成通知或失败通知。

## 交互策略决策

为避免短任务体验明显回退，MVP 不建议把“所有普通消息”都改成后台 job，而是采用“同步直答 + 显式后台入口”并存的策略。

### 第一阶段：显式后台入口

- 普通消息继续走现有同步 `await session.prompt(text)` 链路。
- 长任务通过明确入口提交，例如：`/task start <需求>`。
- 只要进入后台模式，就必须具备 `job_id`、主动通知和可查询状态。

这样做的原因是：

1. 短任务通常几秒内即可完成，没有必要为了后台化而强制变成“两段式交互”。
2. 当前 Telegram 入口仍以同步消息编辑为主要体验，直接全量切到 job 化会让普通问答也先收到“任务已创建”，体验会倒退。
3. 先把长任务闭环跑通，再决定是否要增加“自动判定长任务并转后台”或“会话级默认后台模式”，风险更小。

### 第二阶段：再评估自动后台化

如果第一阶段稳定，再考虑以下增强，但都不应放进 MVP：

- 对明显长任务做启发式自动转后台
- 提供“当前聊天默认后台执行”的开关
- 把同样模式推广到微信或 Web 前端

## 模块拆分建议

建议新增 `src/ragtag_crew/jobs.py`，只承载最小任务编排逻辑，不提前抽象成复杂框架。

### `JobRecord`

建议字段：

- `job_id: str`
- `session_key: str`
- `chat_id: int`
- `user_input: str`
- `status: str`
- `created_at: float`
- `started_at: float | None`
- `finished_at: float | None`
- `last_active_at: float | None`
- `result_preview: str`
- `error_text: str`
- `active_tool_name: str | None`
- `completed_turns: int`
- `completed_tools: int`
- `runner_task: asyncio.Task | None`
- `trace_id: str | None`
- `notification_message_id: int | None`

状态建议限制为：

- `queued`
- `running`
- `completed`
- `failed`
- `cancelled`
- `interrupted`

### `JobManager`

建议职责：

1. 创建任务并分配 `job_id`
2. 保存内存态任务索引
3. 启动后台 runner
4. 响应任务查询
5. 响应任务取消
6. 保存终态结果摘要
7. 进程关闭时把运行中任务标记为 `interrupted`

初期可以只做进程内单例管理，不引入外部存储。

## Telegram 侧交互设计

### 1. 任务提交

后台任务入口建议为显式命令，例如：`/task start <需求>`。

普通文本消息继续保持当前同步直答；只有用户显式使用后台入口时，才走 job 提交流程。

后台入口进入后：

1. 创建任务
2. 立即回复：

```text
任务已创建。
任务 ID: job_xxx
可用 /task job_xxx 查看进度，或用 /cancel job_xxx 取消。
```

3. 后台 runner 开始执行

### 2. 任务查询

新增命令：

- `/tasks`
- `/task <job_id>`

`/tasks` 返回最近若干任务列表，至少包含：

- `job_id`
- 状态
- 已运行时长或完成耗时
- 当前工具或最近一步
- 请求摘要

`/task <job_id>` 返回更详细状态，建议复用 `AgentSession.render_progress_text()` 的已有字段风格，补上任务维度信息。

### 3. 主动通知

任务终态后，由 runner 主动发送消息：

- 完成：返回简短结果摘要，并提示用户如需完整结果可继续追问
- 失败：返回失败原因、最近工具、trace 标识或最近活动摘要
- 取消：明确区分“用户取消”与“系统中断”

### 4. 取消命令

建议兼容两种形式：

- `/cancel`：取消当前聊天最近一个运行中任务
- `/cancel <job_id>`：取消指定任务

取消动作分两步：

1. `session.abort()`，通知 agent loop 尽快停止
2. `runner_task.cancel()`，直接取消后台执行 task

这样可以兼顾 LLM 流式阶段和工具执行阶段。

### 5. 同步与后台的关系

- 同步链路继续承担普通问答、轻量读写、短工具调用。
- 后台链路专门承担下载、构建、测试、批处理、长抓取这类更容易跨越 `turn_timeout` 的任务。
- 忙碌时的自然语言进度问询仍然保留；若当前聊天存在运行中的 job，则优先返回 job 维度状态。

## 执行链路设计

后台 runner 伪流程：

```text
create job record
-> mark running
-> attach streamer / collector
-> run session.prompt(text) under job timeout
-> collect result or error
-> persist final state
-> push telegram notification
-> detach subscriptions / cleanup
```

关键点：

1. runner 必须自己持有占位消息或状态消息，不能依赖原始 handler 生命周期。
2. 任务开始后，即使用户继续发消息，也不能重新占用同一个 session 做第二个并发 agent turn。
3. 对同一 chat 的同一 session，仍建议保持“同一时刻最多一个运行中任务”，避免并发污染对话上下文。

## 取消语义边界

`runner_task.cancel()` 值得做，但文案和验收标准都不应承诺“所有任务都能立即停下”。当前更现实的表述应是“可取消链路更快，不可立即取消的链路至少受已有超时边界约束”。

建议在实现与文档中明确区分以下几类：

1. 进程型工具，例如 `bash`
   - 当前实现已经在 `CancelledError` 时 kill 子进程并回收。
   - 这类任务通常能较快响应 `runner_task.cancel()`。

2. agent / LLM 流式阶段
   - `session.abort()` 已能让主循环和流式输出较快停止。
   - 对这类阶段，后台 runner 取消主要起到补保险和收尾作用。

3. `asyncio.to_thread(...)` 包装的阻塞 I/O
   - 例如当前 `web_search` 这类同步 HTTP 请求。
   - 外层 task 被取消后，线程里的阻塞调用不一定立刻停止；更现实的语义是“不会再继续向用户推进流程，但底层阻塞最多持续到工具超时或请求返回”。

4. 第三方适配器调用，例如 MCP / OpenAPI / 浏览器 CLI
   - 取消效果取决于各自适配器是否传播 `CancelledError`、是否有子进程或超时控制。
   - MVP 不应默认宣称这些路径都具备秒级取消能力。

因此，验收上应要求：

- `bash`、LLM 流式等可取消链路的停止延迟明显优于当前实现。
- 对不可立即取消的阻塞 I/O，任务状态能及时转入“取消中”或“已请求取消”，并在已有 timeout 边界内收敛到终态。

## 与现有 `AgentSession` 的关系

本方案不重写 `AgentSession`，只补外围编排，但需要增加少量适配能力。

### 建议保留不变的部分

1. `prompt()` 仍是单次执行入口。
2. 现有事件流：`message_update`、`tool_execution_start`、`error`、`agent_end` 等继续沿用。
3. `render_progress_text()` 继续作为 session 级运行快照输出。

### 建议新增或调整的部分

1. 增加更稳定的“最近运行信息”更新接口，便于 job 层读取。
2. 让 job 层能区分：
   - 用户取消
   - 任务超时
   - 工具超时
   - 模型超时
   - 其他异常
3. 如有必要，为后台任务引入单独超时包装，而不是复用全局前台 `turn_timeout`。

## 超时策略建议

建议把超时分成三层，而不是只靠一个 `turn_timeout`：

1. `llm_timeout`
   - 单次 LLM 调用总超时
   - 继续沿用现有语义

2. `bash_timeout`
   - 单个 shell 工具超时
   - 继续沿用现有语义

3. `job_timeout`
   - 整个后台任务总超时
   - 仅对后台任务生效

建议初值：

- 前台交互仍保留当前 `turn_timeout`
- 后台任务新增 `job_timeout`，例如 `1800s` 或更长

这样既不破坏短任务体验，也不会让长任务频繁撞上 360 秒上限。

## 持久化策略建议

初期建议做“轻持久化”，避免任务元数据完全丢失。

### 第一阶段

保存内容：

- `job_id`
- `session_key`
- `status`
- 时间戳
- 输入摘要
- 结果摘要
- 错误摘要

保存位置可参考现有 session / trace 设计，放在 `.ragtag_crew` 或独立 `data/jobs/` 下。

### 重启语义

进程启动时若发现上次有 `running` 或 `queued` 状态任务，统一标记为 `interrupted`，不自动恢复执行。

这是当前阶段最务实的做法，能避免“看起来像在跑，其实已经丢了执行上下文”的假象。

## 命令与文案建议

### 新命令

1. `/tasks`
   - 查看当前聊天最近任务列表
2. `/task <job_id>`
   - 查看指定任务详情

### 调整命令

1. `/cancel`
    - 从“取消当前 session 正在执行的 prompt”升级为“取消当前聊天最近运行中的后台任务”
2. `/task start <text>`
   - 显式提交后台任务，避免普通消息体验回退
2. 保留忙碌时自然语言进度问询
    - 如果当前 chat 存在运行中 job，则优先返回 job 维度状态

### 文案要求

文案应明确区分：

- 任务已受理
- 任务运行中
- 任务已完成
- 任务失败
- 任务已取消
- 任务因重启中断

避免继续使用单一的 `Thinking...` 表达全部生命周期。

## 建议改动点

### 必改文件

1. `src/ragtag_crew/telegram/bot.py`
    - 新增显式后台任务入口，例如 `/task start <text>`
    - 普通消息继续保留当前同步执行链路
    - 新增 `/tasks`、`/task` 处理器
    - `/cancel` 适配 job 语义

2. `src/ragtag_crew/agent.py`
   - 补充 job 层需要的状态暴露或运行信息钩子

3. `src/ragtag_crew/config.py`
   - 新增后台任务相关配置，例如 `job_timeout`

4. `src/ragtag_crew/trace.py`
   - 允许关联 `job_id`，便于从任务回溯执行轨迹

5. `src/ragtag_crew/jobs.py`
   - 新增最小后台任务管理模块

### 建议补充测试

1. Telegram 消息提交后立即返回任务已创建，而不是等待 `prompt()` 结束。
   - 该断言只针对显式后台入口，不改变普通消息的同步直答行为。
2. `/tasks` 能看到运行中任务。
3. `/task <job_id>` 能返回任务详情。
4. `/cancel` 能取消后台 runner task，并把任务状态置为 `cancelled` 或“取消中后收敛为终态”。
5. 任务完成后会主动通知。
6. 任务失败后会主动通知，并保留失败原因。
7. 进程重启后的运行中任务会被标记为 `interrupted`。

## 风险与约束

1. 同一 session 仍不适合并发执行多个任务，否则会污染共享消息历史。
2. 如果主动通知消息发送失败，任务本身不能因此回滚；应把通知失败与任务失败分开记录。
3. 仅靠进程内内存态任务管理，重启时无法保留完整运行现场，因此至少要补终态元数据持久化。
4. 如果长任务产生超长结果，最终通知只应发送摘要，完整内容仍通过后续追问或文件产物承载。
5. 如果后台入口与普通消息都操作同一 session，必须先定义忙碌保护规则，否则容易产生“前台又发起一次 prompt 抢占上下文”的冲突。

## MVP 验收标准

满足以下条件即可视为第一阶段完成：

1. 用户通过显式后台入口发起长任务后，Telegram 在数秒内返回任务已创建的确认消息。
2. 后台任务可以继续运行，不阻塞当前消息处理链路。
3. 用户可通过 `/tasks` 和 `/task <job_id>` 查看状态。
4. 用户可通过 `/cancel` 取消运行中任务；对 `bash`、LLM 流式等可取消链路，取消延迟明显优于当前实现；对阻塞 I/O，任务能在已有 timeout 边界内收敛到终态。
5. 任务完成、失败、取消后，机器人会主动推送终态通知。
6. 短任务原有交互不发生明显回归。

## 推荐实施顺序

1. 新增 `JobRecord` / `JobManager`，只做内存态运行。
2. 增加显式后台入口，例如 `/task start <text>`，保持普通消息同步链路不变。
3. 增加 `/tasks`、`/task`、扩展 `/cancel`。
4. 增加任务终态主动通知。
5. 新增 `job_timeout` 与轻量元数据持久化。
6. 最后再看是否需要加入自动后台判定，或把同样模式推广到微信、Web 或 REPL。

## 结论

长任务能力不足的根因不是单一超时参数偏小，而是当前系统仍采用“前台同步对话执行”模型。只要任务对象、后台 runner 和终态通知闭环没有建立，放宽超时只能缓解一部分问题，不能从结构上解决长任务体验。

因此下一步最值得做的，是先在 Telegram 入口补齐最小后台任务层，用最小改动把“同步 prompt”升级为“异步 job”。在此基础上，再分别优化超时、取消、进度和结果交付，收益会更稳定。
