# Claude Code v2.1.88 源码逆向分析 → ragtag_crew 改进启示

> 基于 [逆向深扒 Claude Code 源码](https://mp.weixin.qq.com/s/hskSjAkezaV2epVzUq6ziw) 一文的系统性对照分析。

## 1. Claude Code 12 层渐进式 Harness 概览

Claude Code 的核心竞争力是 12 层渐进式工程包装（Progressive Harness），将一个最小 Agent Loop 逐层升级为工业级自主编码代理。

核心工程哲学：**核心循环本身从不改变。添加新功能 = 添加新工具或新的包装层。**（开闭原则在 Agent 系统中的完美实践。）

| 层级 | 机制 | ragtag_crew 对应 | 状态 |
|------|------|-----------------|------|
| s01 | Agent Loop（while-true + tool_use 检查） | `agent.py` `_run_loop` | ✅ 已有 |
| s02 | Tool Dispatch（工厂 + 统一注册表） | `tools/__init__.py` registry | ✅ 已有 |
| s03 | Planning（先列步骤再执行） | — | ❌ **完全缺失** |
| s04 | Sub-Agents（fork 隔离） | — | ❌ 缺失 |
| s05 | Knowledge On Demand（按需注入 Skill） | `skill_loader.py` | ⚠️ 预加载模式 |
| s06 | Context Compression（三层策略） | `session_summary.py` | ⚠️ 单一策略 |
| s07 | Persistent Tasks | — | ❌ 缺失 |
| s08 | Background Tasks | — | ❌ 缺失 |
| s09 | Agent Teams / Swarm | — | ⏭️ 单用户不需要 |
| s10 | Team Protocols | — | ⏭️ 单用户不需要 |
| s11 | Autonomous Agent coordination | — | ⏭️ 单用户不需要 |
| s12 | Worktree Isolation | — | ⏭️ 单用户不需要 |

## 2. 关键差距分析

### 2.1 Planning 机制 — 完全缺失，但投资回报率最高（P0）

**Claude Code 的发现：** 源码注释明确指出，仅添加"先列步骤再执行"机制就使任务完成率翻倍。这与 Meta-Harness 论文（arXiv:2603.28052）的 Plan-and-Execute Agent 研究结论一致。

**我们当前的问题：** agent 收到请求后直接进入 `_run_loop` 执行，没有规划阶段。对 Telegram 场景尤其不利——用户发一句"帮我重构这个模块"，agent 应该先输出计划再动手。

**建议：** 在 agent loop 中增加显式的 plan phase。不引入复杂框架，而是在 system prompt 层面和 agent 循环逻辑中增加规划意识。

### 2.2 Skill 注入方式 — 方向需要调整（P1）

**Claude Code 的做法：** 通过 `tool_result` **按需注入**，不污染 system prompt。agent 只在决定需要某个 skill 时才加载它。

**我们当前的做法：** `render_skill_prompt()` 把所有 enabled skill **全文塞进 system prompt**（`context_builder.py:57-59`）。

**问题：** 假设用户启用了 paper-search + paper-collector + review 三个 skill，system prompt 就多了 93+154+N 行，每次 API 调用都带这些 token。

**建议：** 逐步从 pre-load 改为 on-demand。skill 全文不进 system prompt，只在 system prompt 中列出 skill 名称和一行摘要；agent 通过一个 skill 注入工具按需获取完整内容。

### 2.3 Function Result Clearing — 完全缺失（P2）

**Claude Code 的做法：** 只保留最近 N 个工具结果，更早的自动清除。system prompt 中还提示模型"在结果还新鲜时主动记笔记"，防止信息在清除后丢失。

**我们当前的问题：** 所有 tool result 永远留在 `messages[]` 里，直到 `compact_history` 整体压缩。长对话中大量过时的工具输出（搜索结果、文件内容）白白占 context window，但还没到触发 compaction 的阈值。

**建议：** 在 compaction 之前增加一层细粒度的 tool result 清理。最近 N 个 tool result 保留原文，更早的只保留摘要或丢弃。

### 2.4 上下文压缩策略单一（P2）

**Claude Code 的做法：** 三层策略：
- **autoCompact**：token 超阈值时调用 API 生成摘要
- **snipCompact**：清理僵尸消息（已被替换的旧版本等）
- **contextCollapse**：重构上下文组织方式

**我们当前的做法：** 只有 `compact_history()` 一种 client-side compaction（正则截取，不调 LLM API）。

**建议：** 短期可增加 snipCompact 思路（清理已知无效消息）；中期考虑 autoCompact（对旧消息调 LLM 生成摘要，比纯截取保留更多信息）。

### 2.5 并行工具执行（P3）

**Claude Code 的做法：** `StreamingToolExecutor` 并发执行多个 `tool_use` block。

**我们当前的做法：** `_run_loop` 中工具是顺序执行的。

**评估：** 优先级低。影响取决于 LLM provider 是否支持在单次响应中返回多个 `tool_use` block。对当前主要使用的 GLM 模型，这个特性可能尚不成熟。

## 3. 已有但可增强的部分

### 3.1 System prompt 缓存友好性

Claude Code 刻意将 system prompt 分为**静态前缀 + 动态后缀**，利用 API 侧的 Blake2b 哈希前缀缓存（静态部分跨用户/跨会话共享，大幅降低 token 成本）。

我们的 `context_builder.py` 拼接顺序：
```
base → PROJECT.md → USER.local.md → MEMORY.md → skills → policy → session_prompt → session_summary
```

前四段相对静态，后三段每次调用都变。没有刻意做分界标记。这个优化取决于 litellm 转发的后端是否支持 prefix cache，暂不强制但值得了解。

### 3.2 权限系统

Claude Code 的权限系统四层纵深：rules + hooks + interactive + YOLO 模式，且 `buildTool()` 工厂采用 fail-closed 默认值（忘记声明就自动串行执行、自动触发权限检查）。

我们已有：delete 阻断 regex、path sandboxing、browser domain allowlisting。

缺失：用户可配置的 hooks、session memory 记住授权决定、更细粒度的操作分级（可自由执行 / 需确认 / 高度警惕）。

### 3.3 Prompt 工程细节

Claude Code 的几个值得借鉴的 prompt 指令：

- **"Don't over-engineer"**：4 条明确规则对抗 LLM 天然的过度工程化倾向
- **工具调用优先级**：优先使用专用工具而非 Bash，让权限系统和 UI 渲染正常工作
- **行动安全框架**：将操作分为三级——可自由执行（编辑文件）、需确认（git push）、高度警惕（rm -rf）

我们的 system prompt 中部分已有类似精神，但没有如此明确的三级分级。

## 4. 对 Roadmap 的具体修改建议

### 4.1 建议新增

| 改动 | 优先级 | 插入位置 | 理由 |
|------|--------|---------|------|
| **Planning 机制** | **P0** | 阶段 4 和 5 之间，作为阶段 4.5 | Claude Code + Meta-Harness 双重验证，完成率翻倍 |
| **Skill 按需注入** | P1 | 阶段 7 上下文深化 | 从 pre-load 改为 on-demand，节省 token |
| **Function Result Clearing** | P2 | 阶段 7 上下文深化 | 在 compaction 之前先做细粒度清理 |
| **"记笔记" prompt 指令** | P2 | 阶段 5 或 7 | 配合 FRC 使用，防止信息在清理时丢失 |
| **三层上下文压缩** | P2 | 阶段 7 | 替换单一 compaction 策略 |
| **并行工具执行** | P3 | 阶段 7 之后 | 依赖 LLM provider 支持 |

### 4.2 建议调整

| 现有规划 | 调整 |
|---------|------|
| 阶段 6 的 trace collection | 提升认知：不仅是"控制面"的一部分，更是**所有后续 harness 优化的数据基础** |
| 环境引导（env bootstrap） | 维持原优先级不变，Claude Code 也没有专门做这个 |
| "明确不做"列表 | 维持不变，Swarm/Remote Agent 对单用户场景无意义 |

### 4.3 不需要动的

- 阶段 0-4：已经完成的底座，方向正确
- "先补能力来源，再补能力承接"的核心思想：完全正确
- Meta-Harness 的 trace collection 认识：前瞻性很好

## 5. 总结

roadmap 方向正确，但缺少了一个**投资回报率最高的特性：Planning 机制**。Claude Code 用"先列步骤再执行"这一个机制就把完成率翻倍，我们连这层都没有就直接进入了执行循环。

其次，Skill 全文注入 system prompt 的方式应该逐步改为按需注入，这是 token 效率和上下文相关性两方面的重要改进。

Claude Code 的 12 层架构证明了另一个重要观点：**Agent 系统的竞争力不在于核心循环的复杂度，而在于围绕核心循环叠加的包装层厚度和质量。** 我们的核心循环已经足够好，接下来的重点应该是在 Harness 层逐步叠加生产级特性。

## 参考来源

- [逆向深扒 Claude Code 源码](https://mp.weixin.qq.com/s/hskSjAkezaV2epVzUq6ziw)（分析版本 v2.1.88，~512,664 行代码）
- Meta-Harness: arXiv:2603.28052
