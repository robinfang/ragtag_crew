# 微信体验补齐与 Plan Mode 强约束改造方案

## 背景

当前仓库已经有 3 个前端入口：`Telegram`、`微信`、`REPL`。

从现状看，`Telegram` 已经是完整前端，`微信` 仍是最小接入层，`REPL` 是开发/调试入口。近期 trace 和代码排查已经确认两类问题：

1. 微信端体验明显弱于 Telegram，不只是“没有流式显示”，更底层的问题是普通消息处理直接 `await session.prompt(text)`，会阻塞微信 SDK 的长轮询处理链，导致长任务期间新的 `/cancel` 和进度追问不一定能及时进入系统。
2. `plan mode` 当前只是 prompt 层约束，不是执行层协议。即使 `planning_enabled=True`，`AgentSession._run_loop()` 依然会在首轮收到 `tool_calls` 后立刻执行工具，因此会出现“用户未确认，系统已开工”的行为。

这两个问题需要一起处理，但实现位置不同：

- `plan mode` 必须落在 `AgentSession` 层，保证 Telegram、微信、REPL 行为一致。
- 微信体验补齐必须落在 `weixin/bot.py` 侧，优先解决后台执行、可取消和过程可见性。

## 目标

本方案目标是用最小正确改动补齐一个可落地的 V1：

1. `planning_enabled=True` 时，非 trivial 任务进入明确的“先计划、后确认、再执行”两阶段协议。
2. 在用户确认之前，agent 不允许执行任何工具。
3. 微信普通消息改为后台任务执行，消息 handler 尽快返回，不再被长任务阻塞。
4. 微信端在长任务期间具备可靠的 `/cancel`、进度查询和阶段性主动通知。
5. 整个改造保留现有 `AgentSession`、`TraceCollector`、`session_store` 结构，不引入新的复杂框架。

## 非目标

本轮明确不做以下内容：

1. 不把微信做成和 Telegram 完全等价的“单消息流式编辑”体验。
2. 不依赖 `weixin-bot-sdk` 中尚未打通的 `MessageState.GENERATING` 作为主方案。
3. 不引入 Redis、数据库、外部任务队列或多进程 worker。
4. 不一次性补齐微信所有 Telegram 命令。
5. 不重构成统一的“前端框架层”。

## 设计原则

1. 最小正确改动：先把协议和关键交互做闭环，不提前做泛化抽象。
2. 状态显式化：`plan mode` 不再依赖模型自觉，必须有运行时状态字段承接。
3. 前端薄封装：微信端尽量复用 `AgentSession` 事件，不复制 agent 逻辑。
4. 可验证优先：每个新状态和关键路径都要有测试覆盖。
5. 先解决底层阻塞，再做展示优化：微信 V1 的第一优先级是后台执行，而不是伪流式渲染。

## 当前问题复盘

### 1. Plan mode 只是提示词，不是运行时协议

当前链路如下：

- `context_builder.py` 在 `planning_enabled=True` 时只是在 system prompt 注入 `## Planning Protocol`。
- `AgentSession._build_messages()` 仅把 `planning_enabled` 透传给 `build_system_prompt()`。
- `AgentSession._run_loop()` 不区分“计划阶段”和“执行阶段”；只要 `stream_chat()` 返回 `tool_calls`，就立即执行。

这意味着当前 trace 中出现 `planning_enabled:true` 同时直接调用工具，是现有代码允许的行为，而不是状态丢失。

### 2. 微信长任务会阻塞消息接收

当前微信普通消息链路如下：

1. `handle_incoming_message()` 进入非 busy 分支。
2. 创建 `TraceCollector`。
3. `await bot.send_typing(...)`。
4. `await session.prompt(text)`。
5. 最后 `bot.reply(message, result)`。

由于 `weixin-bot-sdk` 的长轮询 dispatch 会等待 handler 完成，这种写法会导致：

1. 长任务期间 handler 被占住。
2. 后续 `/cancel` 和“进度怎么样了”可能无法及时进入。
3. 体验上看起来像“系统失联”或“直接执行但没有过程反馈”。

因此，微信体验补齐的第一优先级是“后台执行并及时返回 handler”，而不是直接照搬 Telegram streamer。

## 总体策略

采用“两条线并行、同一轮完成”的方案：

1. 在 `AgentSession` 增加 plan 阶段状态机，把 `plan mode` 做成强约束。
2. 在 `weixin/bot.py` 增加最小后台任务执行层，把普通消息改为后台运行，并通过 `bot.send()` 主动推送过程状态。

这两部分都尽量复用现有结构：

- 继续使用 `TraceCollector` 收集轨迹。
- 继续使用 `session_store.py` 持久化会话。
- 继续使用 `AgentSession.subscribe()` 监听运行事件。

## 第一部分：Plan Mode 强约束改造

## 设计目标

把现在的“提示词建议”升级为真正的运行时协议：

1. 当 `planning_enabled=False` 时，保持现有行为，直接执行。
2. 当 `planning_enabled=True` 时：
   - trivial 请求仍可直接执行。
   - 非 trivial 请求必须先输出计划，并进入等待用户确认状态。
   - 在确认前，禁止工具执行。
   - 用户确认后，才进入正常执行阶段。

## 状态设计

建议在 `AgentSession` 新增以下持久状态字段：

- `awaiting_plan_confirmation: bool = False`
- `pending_plan_text: str = ""`
- `pending_plan_request_text: str = ""`

必要时可加一个轻量辅助字段：

- `plan_generated_at: float | None = None`

字段职责：

- `awaiting_plan_confirmation`：当前是否处于“已给计划，等待用户确认”的状态。
- `pending_plan_text`：最近一次待确认的计划正文，供前端展示和持久化恢复。
- `pending_plan_request_text`：生成该计划时对应的原始用户请求，便于进度文案和 trace 标记。

不建议本轮新增更重的 enum 状态机。当前布尔值加两个文本字段已经足够支撑 V1。

## 行为协议

### 1. 新请求进入时的分流

在 `AgentSession.prompt(text)` 开头增加分流逻辑：

1. 若 `awaiting_plan_confirmation=True`：
   - 先判断当前输入是否为确认语句。
   - 若是确认语句，则清除等待状态，并把这条用户输入当作“允许执行”的确认消息进入正常执行。
   - 若不是确认语句，则视为用户拒绝/改写计划，清除等待状态，并把当前输入当作新的正常请求重新走判断流程。
2. 若 `awaiting_plan_confirmation=False`：
   - 走常规逻辑；若 `planning_enabled=True`，先尝试 plan 阶段。

### 2. trivial / 非 trivial 判定

V1 不建议上复杂分类器，直接复用现有 system prompt 的原则做启发式判断即可。

建议新增一个最小辅助方法，例如：`_should_require_plan(text: str) -> bool`。

判定思路：

1. 默认只在 `planning_enabled=True` 时启用。
2. 对明显简单请求直接放行，例如：
   - 很短的问答
   - 纯说明性问题
   - 不涉及修改、搜索、执行、生成文件、跑命令等动作的请求
3. 对以下信号命中时要求先计划：
   - 文本较长
   - 含“实现 / 修改 / 重构 / 修复 / 新增 / 生成 / 跑测试 / 看下代码 / 搜索 / 排查 / 方案 / 开工”等动作词
   - 明显是多步骤任务

这里的目标不是“100% 分类正确”，而是把大多数非 trivial 请求纳入强约束协议。

### 3. 计划阶段的执行方式

建议新增一个专门方法，例如：`_run_planning_phase(text: str) -> str`。

行为如下：

1. 调用 `stream_chat()` 时传 `tools=None`，硬性禁用工具。
2. 组装 messages 时，在当前 system prompt 基础上再附加一段一次性 planning instruction，明确要求：
   - 仅输出计划。
   - 使用编号列表。
   - 不执行工具。
   - 末尾明确提示等待用户确认。
3. 若模型返回空内容，则回退成固定文案，避免进入空白等待态。
4. 将结果保存到：
   - `pending_plan_text`
   - `pending_plan_request_text`
   - `awaiting_plan_confirmation=True`
5. 把计划文本作为 assistant message 写入 `self.messages`，保持会话历史完整。
6. `prompt()` 直接返回该计划文本，不进入 `_run_loop()`。

### 4. 确认语句识别

建议新增一个最小辅助方法，例如：`_is_plan_confirmation(text: str) -> bool`。

匹配集合可覆盖：

- 中文：`继续`、`开始`、`执行`、`开工`、`按这个做`、`确认`、`可以`、`继续吧`
- 英文：`go`、`continue`、`proceed`、`run`、`start`、`yes`

规则上建议：

1. 做标准化：去空格、小写。
2. 优先匹配整句或短句，不要对长文本做宽松包含，以免误判。

### 5. 执行阶段的行为

用户确认后进入正常 `_run_loop()`，不需要引入第二套执行逻辑。

实现方式建议是：

1. 清除 `awaiting_plan_confirmation`、`pending_plan_text`、`pending_plan_request_text`。
2. 把用户的确认语句作为普通 user message 写入历史。
3. 直接进入现有 `_run_loop()`。

这样模型能看到：

- 之前给出的计划
- 用户的明确确认

不需要在代码里手工重放计划。

## 代码改动点

### `src/ragtag_crew/agent.py`

需要新增或修改：

1. `AgentSession.__init__()`
   - 增加 plan-wait 状态字段。
2. `reset()`
   - 清空 plan-wait 状态，避免 `/new` 后残留。
3. `render_progress_text()`
   - 当 `awaiting_plan_confirmation=True` 且当前不 busy 时，优先返回“当前正在等待你确认计划”的文案，而不是“当前没有进行中的任务”。
4. `prompt()`
   - 增加等待确认分流。
   - 增加 planning phase 入口。
5. 新增辅助方法：
   - `_should_require_plan()`
   - `_is_plan_confirmation()`
   - `_run_planning_phase()`
   - `_clear_pending_plan_state()`

### `src/ragtag_crew/session_store.py`

需要持久化以下字段：

- `awaiting_plan_confirmation`
- `pending_plan_text`
- `pending_plan_request_text`
- `plan_generated_at`（如果最终保留）

这样在进程重启后，仍能恢复“计划已给出、等待确认”的会话状态。

### `src/ragtag_crew/trace.py`

建议补充最小 trace 字段，便于后续排查：

- 顶层：
  - `awaiting_plan_confirmation_at_start`
  - `prompt_phase`，取值可为 `planning` / `execution`
- turn 级：
  - `tools_enabled`

V1 不要求做得很重，但至少要能区分：

1. 这次 prompt 是在产出计划还是在真正执行。
2. 首轮是否禁用了工具。

### `src/ragtag_crew/context_builder.py`

本轮不需要大改，只需保留现有 `Planning Protocol` 文案。因为真正的强约束会落在运行时；prompt 仍然保留，作为模型侧补充约束。

## 测试计划

### `tests/test_agent.py`

至少补以下场景：

1. `planning_enabled=True` 且命中非 trivial 请求时，`prompt()` 首次只返回计划，不进入工具执行。
2. planning phase 调用 `stream_chat()` 时，传入 `tools=None`。
3. 进入等待确认后，`render_progress_text()` 返回等待确认文案。
4. 用户发送确认语句后，进入正常 `_run_loop()` 并执行工具。
5. 用户在等待确认时发送新的修改意见，不直接执行旧计划，而是清空旧等待态并重新生成计划或重新判断。
6. `reset()` 会清空等待确认状态。

### `tests/test_session_store.py`

补一条 roundtrip：

1. 保存包含 plan-wait 状态的 session。
2. 重新加载后断言字段完整恢复。

### `tests/test_trace.py`

如果补了 trace 字段，需要补对应 JSON 结构断言。

## 第二部分：微信体验补齐

## V1 目标

微信端本轮目标不是追求“消息编辑式流式体验”，而是补齐 4 个关键能力：

1. 普通消息后台执行，避免阻塞长轮询。
2. 长任务期间可可靠接收 `/cancel` 与进度询问。
3. 主动发送阶段性状态消息，让用户知道系统还活着。
4. 最终结果用 `bot.send()` 或 `bot.reply()` 发回，长回复按更合理的语义边界切分。

## 交互策略

### 1. 普通消息受理

微信收到普通消息后：

1. 若 session 正在执行：
   - 若是进度查询，回复 `render_progress_text()`。
   - 否则回复“当前任务仍在执行，请等待或发送 /cancel”。
2. 若 session 未执行：
   - 立即回复一条受理确认，例如：`已收到，开始处理。可随时发送 /cancel。`
   - 启动后台 `asyncio.Task` 执行真正的 `session.prompt(text)`。
   - handler 尽快返回，释放 SDK 长轮询。

这里的重点是：受理确认和真正执行解耦。

### 2. 后台执行期间的状态推送

微信 V1 不做单消息编辑，而是做“阶段消息”。建议新增一个最小事件桥接器，例如：`WeixinProgressNotifier`。

它订阅 `AgentSession` 事件，并按节流策略主动 `bot.send(user_id, text)`。

V1 建议只推 4 类消息：

1. 开始执行：`开始处理，请稍候。`
2. 首次工具执行：`正在执行工具: <tool_name>`
3. 长时间无更新时的保活消息：例如每 15-30 秒推一次 `任务仍在执行` + 进度摘要
4. 结束消息：成功 / 失败 / 已取消

不建议 V1 对每个 `message_update delta` 都推送，否则微信会被大量碎片消息淹没。

### 3. 最终结果发送策略

完成后优先使用 `bot.send(user_id, text)` 主动发送最终结果；首次受理确认可以用 `bot.reply(message, text)`，保持与原始消息有直接关联。

原因：

1. 后台任务结束时，原始 handler 已返回。
2. `WeixinBot.send()` 依赖用户最近消息缓存下来的 `context_token`，当前 SDK 已支持。

### 4. 长回复切分策略

SDK 已经会按 2000 字符硬切，但 V1 建议在仓库内再加一层轻量切分函数，例如：

- 优先按空行分段
- 其次按单行边界分段
- 最后才退回字符数切割

这样可以减少代码块、列表、表格被截断得过于难看。

不建议本轮引入复杂 Markdown 解析器。

## 后台执行实现

### 状态管理

建议在 `src/ragtag_crew/weixin/bot.py` 增加一个最小的进程内任务索引，例如：

- `_active_prompt_tasks: dict[str, asyncio.Task[Any]] = {}`

key 使用当前 `session_key` 即可，不必额外引入 job id。原因是当前微信端仍然是单用户、单会话串行交互模型；一个 session 同时只跑一个任务已经足够。

### 运行链路

建议新增一个后台 runner，例如：`_run_session_prompt_in_background(...)`。

职责如下：

1. 创建并绑定 `TraceCollector`。
2. 订阅 `WeixinProgressNotifier`。
3. 执行 `await session.prompt(text)`。
4. 成功后主动发送最终结果。
5. 取消时发送“已取消”。
6. 异常时发送错误说明。
7. 最后统一：
   - `collector.finalize()`
   - `save_session(...)`
   - `session.unsubscribe(...)`
   - 从 `_active_prompt_tasks` 移除 task

### `/cancel` 的实现

当前 `/cancel` 只调用 `session.abort()`，V1 建议保留并增强：

1. 仍先调用 `session.abort()`，让 LLM 和工具链尽快停。
2. 若 `_active_prompt_tasks[session_key]` 存在，则同时 `task.cancel()`。
3. 对用户回复保持简单明确：`已发送取消信号。`

这样取消路径会比现在更可靠，尤其是在后台任务层已经存在的情况下。

### 进度查询

微信端仍复用 `session.render_progress_text()`，但因为 handler 已不再被长任务阻塞，所以这条路径会真正变得可靠。

为了让 waiting-plan 场景也有合理反馈，`render_progress_text()` 改造后会自动覆盖：

1. 正在执行的进度快照
2. 等待计划确认的提示

## 命令补齐策略

本轮不建议一次性补全 Telegram 全部命令。建议分两层：

### 本轮必须做

1. 保持已有：`/help /new /cancel /plan /sessions /session`
2. 更新 `/help` 文案，反映：
   - 微信现在支持后台执行
   - 长任务可用 `/cancel`
   - plan mode 现在是“先计划、待确认、再执行”

### 下一轮再做

1. `/model`
2. `/tools`
3. `/skills`
4. `/context`
5. `/prompt`
6. `/memory`
7. `/browser`

这些能力属于控制面补齐，但不是这次体验兜底的阻塞项。

## 代码改动点

### `src/ragtag_crew/weixin/bot.py`

需要新增或修改：

1. 文件头文案从“minimal command support”更新为更符合现状的描述。
2. 新增 `_active_prompt_tasks`。
3. 新增后台 runner 函数。
4. 新增最小事件通知器 `WeixinProgressNotifier`。
5. 普通消息链路改成：
   - 先受理回复
   - 再 `asyncio.create_task(...)` 后台执行
   - 立即返回
6. `/cancel` 改成同时触发 `session.abort()` 和后台 task 取消。
7. 如有必要，新增轻量文本分段函数。

### `tests/test_weixin_bot.py`

至少补以下测试：

1. 普通消息不再直接 `await session.prompt()`，而是创建后台 task。
2. 受理后会立即 `reply()` 一个确认文本。
3. 后台 runner 成功后会用 `send()` 主动发结果，并保存 session。
4. `/cancel` 在 session busy 且存在后台 task 时，会同时触发 `abort()` 和 `task.cancel()`。
5. 忙碌时的进度查询仍返回 `render_progress_text()`。
6. 后台 runner 在异常和取消时会清理 `_active_prompt_tasks`。

## 第三部分：实施顺序

建议严格按以下顺序开工，减少返工：

1. 先改 `AgentSession` 的 plan-wait 状态和 planning phase。
2. 补 `tests/test_agent.py`，把 plan mode 强约束行为锁住。
3. 改 `session_store.py` 持久化 plan-wait 字段，并补 roundtrip 测试。
4. 视需要补 `trace.py` 字段和对应测试。
5. 再改 `weixin/bot.py`，把普通消息切到后台执行。
6. 补 `tests/test_weixin_bot.py` 覆盖后台运行、取消、最终发送和清理。
7. 最后做一次相关测试和全量测试。

原因：

1. plan mode 是核心行为变更，应该先稳定内核。
2. 微信端只是在外围消费 `AgentSession` 新状态和事件，排在后面改动更顺。

## 验收标准

### Plan mode

满足以下条件即可视为完成：

1. `planning_enabled=True` 且请求命中非 trivial 任务时，首轮只返回计划文本。
2. 该首轮不会执行任何工具。
3. 用户未确认前，进度查询会明确提示“正在等待计划确认”。
4. 用户回复确认语句后，才进入真实执行。
5. 重启后可恢复等待确认状态。

### 微信体验

满足以下条件即可视为完成：

1. 微信普通消息提交后，handler 能快速返回，不再同步等待整个任务结束。
2. 长任务期间再次发消息，可以可靠收到进度或忙碌提示。
3. `/cancel` 可以在长任务期间可靠生效。
4. 任务结束后，微信能主动推送结果，而不是必须依赖原始 reply 链路。
5. 相关测试通过。

## 测试与验证

建议至少执行：

```powershell
uv run pytest tests/test_agent.py tests/test_session_store.py tests/test_trace.py tests/test_weixin_bot.py
```

如果改动过程中影响面扩大，再执行：

```powershell
uv run pytest tests/
```

## 风险与取舍

### 1. trivial 判定可能不完全准确

这是可接受的 V1 风险。相比继续维持“plan mode 形同虚设”，启发式误判的代价更小。

### 2. 微信阶段消息可能偏少

这也是有意取舍。V1 先保证可靠性和可取消，再看是否需要更细的状态推送颗粒度。

### 3. 后台 task 仍是进程内状态

当前单进程架构下这是合理选择。本轮目标不是跨进程持久后台任务，而是补齐单进程内长任务体验。

## 建议的交付边界

如果下一步要直接开工，建议把交付分成一个 PR，但内部按两个提交逻辑组织：

1. `AgentSession` + `session_store` + `trace` + 对应测试
2. `weixin/bot.py` + 对应测试

这样 review 时边界清晰，出问题也更容易定位。

## 结论

这次改造的最小闭环不是“把微信做得像 Telegram”，而是：

1. 把 `plan mode` 从软提示改成强协议。
2. 把微信从“同步阻塞的一次性回复”改成“后台执行 + 可取消 + 有过程反馈”的可用前端。

只要按本文档顺序实施，下一步就可以直接开始编码，不需要再额外补架构设计。
