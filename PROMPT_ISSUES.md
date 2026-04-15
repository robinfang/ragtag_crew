# 提示词问题诊断与修复记录

## 背景

本次结论不再只基于 `data/traces/2026-04-05.jsonl` 到 `2026-04-07.jsonl` 的表象，
而是同时结合了运行时实现与测试：

- trace 能证明什么行为发生过
- prompt 文本能约束什么行为
- Telegram / Agent 运行时实际上允许什么交互

这样可以避免把工程层问题误判成纯提示词问题。

---

## 结论一：简洁性规则确实压制了行动叙述

**来源：** `src/ragtag_crew/telegram/bot.py` 旧 `_SYSTEM_PROMPT` Rule 4

**旧文本：**
> "Be concise: respond in as few words as possible. Avoid preamble, summaries, and explanations unless asked."

**证据：**

多个 trace 在发起工具调用前只有极短文本：

```
// trace c2225dfeb7ac, turn 1
{"llm_time_ms":108687, "response_len":15, "tool_calls":["write"]}

// trace 17edfd7bddbe, turn 1
{"llm_time_ms":39594, "response_len":15, "tool_calls":["write"]}

// trace 0bc051b47426, turn 1
{"llm_time_ms":17562, "response_len":20, "tool_calls":["read","read","read"]}
```

**判断：**

这一诊断基本成立。旧规则把“少说”放在高优先级位置，模型很容易把
`avoid preamble / explanations` 解释成“行动前尽量不要说话”。

**修复：**

将 Rule 4 改为“叙述意图，不叙述过程”：

- 行动前用一短句说明意图
- 非平凡动作结束后，必要时补一句结果
- 明确禁止空洞客套和复述用户输入

这是合理的 prompt 修复，因为它直接消除了原有冲突，而不是继续要求模型自己权衡。

---

## 结论二：Planning Protocol 之前确实偏模糊，但原始证据不够干净

**来源：** `src/ragtag_crew/context_builder.py` 的 `Planning Protocol`

**旧文本：**
> "For non-trivial tasks (requiring 3+ steps, touching multiple files, or involving design decisions)..."

**原始问题判断：**

文档最初把 “未输出计划” 直接归因为 `non-trivial` 边界模糊。这个方向有道理，
但证据链不完整，原因有两个：

1. trace 之前**不记录** `planning_enabled`，无法排除当时 plan mode 已被关闭
2. 被点名的 `ff802777bb00` 这条 trace 中，记录到的 `user_input` 并不是
   “写一个完整的统计分析 Python 包”的原始需求，而是模型状态文本，说明会话延续上下文也在影响行为

**修复：**

本次仍然保留 prompt 优化，但把结论降级为“高概率原因”，并同步补工程观测：

- prompt 触发条件改为更具体的枚举：
  - creating new files
  - editing 3+ files
  - architecture / design decision
  - approach unclear
- 执行阶段要求每个主要步骤完成后简短汇报
- trace 新增 `planning_enabled` 字段，后续再看“为什么没出计划”时不需要继续猜

**判断：**

这个修复是合理的，但它是“提高可触发性 + 提高可观测性”，不是对旧结论的严格证明。

---

## 结论三：用户中途问进度，主要是运行时问题，不是纯 prompt 问题

**原始现象：**

```json
// trace 17edfd7bddbe
{"total_turns":4, "total_time_ms":365047, "status":"error", "error_info":"TurnTimeoutError"}
```

用户发送“进展如何”后，系统继续执行已有任务并最终超时。

**重新核实后的根因：**

这里不能简单归因为“模型没遵守 prompt”。真正的关键限制在 Telegram 路由层：

- 当 session 处于 busy 状态时，新消息原本会被直接拒绝
- 旧行为是固定回复：`Please wait for the current response to finish.`
- 也就是说，所谓“用户中途问进度，模型先回答再继续”在原架构里根本没有入口

因此，原先把这件事作为 Planning Protocol 的一条 prompt 修复是不充分的。

**本次工程修复：**

这次改成运行时直接支持：

1. `AgentSession` 增加运行时进度快照
   - 当前请求
   - 已运行时长
   - 当前 turn
   - 已完成 turn / tool 数
   - 正在执行的工具
   - 最近输出片段

2. Telegram busy 分支识别进度询问
   - 普通消息仍然拒绝
   - 若识别为 `进展如何 / 怎么样了 / progress / status / update` 等进度询问，
     直接返回 `session.render_progress_text()`

3. prompt 规则同步改写为更准确的表述
   - 不再写“mid-task answer first, then continue”这种会让人误解成并发中断的说法
   - 改为：当收到进度问题时，先用 done / in-progress / next 的结构回答

**判断：**

这是本次最关键的“科学修复”。因为它把问题落到了真正出问题的层级：运行时调度，而不是继续把架构限制甩给 prompt。

---

## 工程观测改进

除 prompt 外，本次顺手补了一个会影响分析质量的观测缺口：

- `TraceCollector` 新增 `planning_enabled`

这样之后再分析“计划输出率”时，可以分清：

- 是 prompt 没触发
- 还是用户本来就关闭了 plan mode

---

## 最终改动

### `src/ragtag_crew/telegram/bot.py`

- 保留并强化“行动前短句说明意图”
- 将进度规则改为更准确的描述
- 在 busy 分支中新增进度询问识别与即时回复
- trace 上报时加入 `planning_enabled`

### `src/ragtag_crew/agent.py`

- 新增运行时进度快照能力 `render_progress_text()`
- 在执行期间维护 turn / tool / 最近输出 / 运行时长等状态

### `src/ragtag_crew/context_builder.py`

- 将 Planning Protocol 改为更具体的触发条件
- 保留步骤性汇报要求
- 将进度问题规则改写为与实际运行时一致的表述

### `src/ragtag_crew/trace.py`

- trace 记录新增 `planning_enabled`

---

## 测试

本次新增或更新的覆盖点：

- busy 状态下的进度询问会返回运行时快照
- `AgentSession.render_progress_text()` 输出包含关键运行时状态
- trace 文件包含 `planning_enabled`

执行结果：

- 与本次改动直接相关的新增测试通过
- 全量 `uv run python -m pytest tests/ -v` 仍有 1 个既有失败：
  `tests/test_tools.py::SearchToolTests::test_find_and_ls_skip_internal_directories`
  该失败与本次 prompt / progress / trace 修改无关
