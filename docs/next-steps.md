# 下一步工作分析

> 更新日期：2026-04-08
> 依据：项目文档（roadmap、context-system-design、claude-code-harness-analysis、research-agent-toolchain、pending-decisions）+ 源码深度审查

## 1. 当前状态总览

### 1.1 已完成的里程碑

| 里程碑 | 内容 | 状态 |
|--------|------|------|
| M1 | 统一外部工具底座 | ✅ |
| M2 | 稳定联网搜索 | ✅ |
| M3 | Windows Everything 搜索适配 | ✅ |
| M4 | MCP client | ✅ |
| M5 | 固定 OpenAPI 工具 | ✅ |
| M7（部分） | 执行轨迹收集、/cancel、/model、dev 模式、Planning 机制、运行时进度追踪、提示词改进、env bootstrap、表格渲染 | ✅ |

### 1.2 近期提交

| 提交 | 内容 |
|------|------|
| `9fa25e9` | 提示词改进：narrate intent、planning 触发条件、进度查询响应 |
| `d0ee0a5` | 运行时进度追踪：agent 状态字段 + render_progress_text |
| `88afab2` | 修复 `_find_with_rg` 返回绝对路径问题 |
| `5ed58ec` | 修复 on_delta 阻塞导致 LLMTimeoutError |
| `887f554` | 新增 /plan 命令：会话级动态切换规划模式 |

233/233 测试通过。

### 1.3 已确认的技术决策

（摘自 `docs/project-decisions.md`）

- 路径沙箱：写操作限制 working_dir，读操作允许任意绝对路径
- 文件删除保护：bash 拦截删除命令，统一走 `delete_file` 工具
- grep 默认大小写不敏感
- 用户取消 vs 超时分开处理
- dev 模式保持与生产一致的超时配置

### 1.4 最新已落地项

- `mcp_client.py`：MCP 发现与调用链路已补全超时保护，单个工具注册失败不再中断整台 server 的发现
- `manager.py`：外部能力初始化已支持日志化、部分成功保留，以及“全失败不置 initialized、后续自然重试”
- `env_bootstrap.py`：已引入 `Workspace Snapshot`，自动注入目录树与关键配置文件摘要
- `session_summary.py`：阶段 A 已完成，摘要会保留关键工具参数、调用顺序，并优先保留新近压缩内容
- `telegram/bot.py`：`/cancel` 已增加显式确认反馈
- `telegram/html.py`：Telegram 表格渲染已采用代码块方案，并修复 fenced code block 不应被重写的问题

---

## 2. 已完成工作回顾

### P0.1 MCP 全链路超时（已完成）

**问题**：`mcp_client.py` 全文无任何 `asyncio.wait_for`。`stdio_client()` 启动、`session.initialize()` 握手、`session.list_tools()` 三个阶段全部裸露。项目其他模块（shell_tools、llm、search_tools）都有超时保护，唯独 MCP 没有。

**具体位置**：

| 位置 | 问题 | 严重度 |
|------|------|--------|
| `_list_tools_for_server:92` | 子进程启动无超时，进程不存在或 hangs 时永久阻塞 | 高 |
| `_list_tools_for_server:94` | MCP 握手无超时 | 高 |
| `_list_tools_for_server:95` | list_tools 调用无超时（对比 call_tool 有 `read_timeout_seconds`） | 高 |
| `_call_tool_on_server:124,126` | call_tool 路径中 stdio_client 启动和 initialize 同样无超时 | 高 |
| `discover_mcp_tools:160` | 串行遍历，一个 server 挂住则全部阻塞 | 中 |
| `discover_mcp_tools:186-188` | 工具注册循环无 try/except，单个工具异常中断全部发现 | 中 |

**现状**：已完成。`_list_tools_for_server()` 和 `_call_tool_on_server()` 均已补上外层总超时；`discover_mcp_tools()` 的工具注册循环已具备错误隔离；相关超时与异常测试已补齐。

### P0.2 初始化错误处理（已完成）

**问题**：`manager.py` 存在三个缺陷：

1. `ensure_external_capabilities_initialized:67` 中 `create_task()` 返回值被丢弃，fire-and-forget 错误静默丢失
2. `initialize_external_capabilities()` 无 try/except，MCP 失败导致前面已注册的 browser/openapi statuses 全部丢失
3. 整个 `external/` 目录无任何 logging

**竞态风险**：`create_task()` 延迟初始化，函数立即返回但初始化还在后台执行。后续代码可能读到空的 `_capability_statuses`。当前依赖 bot 中四处 `await initialize_external_capabilities(force=True)` 作为补偿。

**现状**：已完成。后台初始化异常现在会进入日志；provider 初始化已按段隔离；当所有 provider 都失败且没有任何 status 产出时，不再把系统标记成已初始化，后续普通路径可自然重试。

### P0.3 Session Summary 阶段 A（已完成）

**问题**：`session_summary.py` 的 `compact_history` 是纯机械文本裁剪，非语义摘要。具体缺陷：

| 缺陷 | 位置 | 影响 |
|------|------|------|
| 工具调用只保留工具名，丢失参数（改了哪个文件？搜了什么？） | `_summarize_message:58-62` | 高 |
| 工具结果截断到 220 字符 | `_summarize_message:67` | 高 |
| 旧摘要每次压缩到 `max_chars // 2`，多次压缩后指数退化 | `_merge_summary:35` | 中 |
| 新摘要在拼接末尾，溢出时新内容先被截断（方向反了） | `_merge_summary:43` | 中 |
| user/assistant 消息也截断到 220 字符 | `_summarize_message:49,54` | 中 |
| 多工具调用顺序丢失（逗号拼接） | `_summarize_message:62` | 低 |

**阶段 A 实现结果**：

- 已保留 assistant tool call 中的 `path`、`file`、`query`、`pattern`、`url` 等关键字段
- 摘要默认截断阈值已从 220 提高到 500 字符
- 多工具调用顺序已显式保留
- `_merge_summary()` 已调整为溢出时优先保留更新近的压缩内容

**后续仍可评估的阶段 B**：

**阶段 A — 改进机械摘要**（~30 行）：
- 保留工具调用参数中的文件路径和查询词
- 提高截断阈值（220 → 500 字符）
- 调整拼接方向（旧摘要在前，新摘要在后，溢出时先截旧内容）
- 保留工具调用顺序

**阶段 B — LLM 语义摘要**（后续评估）：
- 用 small_model 生成语义摘要
- 质量高但增加 API 成本和延迟
- 需要先验证阶段 A 的改进幅度

**现状**：阶段 A 已完成；阶段 B（small model 语义摘要）保留为后续评估项。

---

## 3. 高 ROI 功能（P1）

### P1.1 环境引导（env bootstrap，已完成）

**来源**：Meta-Harness 论文 (arXiv:2603.28052) + `context-system-design.md` 第 4C 层

**效果**：启动时采集工作目录快照注入 system prompt，每次请求节省 2-4 轮探索性调用。仅 80 行代码，15 秒超时，静默失败。

**实现结果**：

已新建 `src/ragtag_crew/env_bootstrap.py`，并在 `context_builder.py` 中作为 `Workspace Snapshot` 注入到 `Project Context` 和 `User Context` 之间。

采集内容：
- 深度 3 的目录树（`os.scandir`，不用 Everything，跨平台兼容）
- 关键配置文件探测（pyproject.toml、package.json 等，读前 200 字符）
- 60 秒 TTL 缓存，避免每轮重复扫描
- 2000 token 预算上限，可配置

新增配置项（`config.py`）：
```
env_bootstrap_enabled: bool = True
env_bootstrap_max_depth: int = 3
env_bootstrap_max_tokens: int = 2000
env_bootstrap_skip_dirs: str = ".git,.venv,__pycache__,node_modules,..."
```

**现状**：已完成，并已补齐空目录、深度控制、隐藏文件、token 预算与配置开关等测试。

### P1.2 Skill 按需注入

**来源**：`docs/claude-code-harness-analysis.md` 第 2.2 节

**问题**：当前 `context_builder.py` 把所有 enabled skill 全文塞进 system prompt。启用 3 个 skill 可能增加 200+ 行 token 开销。

**方案**：system prompt 只列出 skill 名称 + 一行摘要，agent 通过工具按需获取完整内容。属于 P1 但改动较大，建议在 P0 完成后再做。

---

## 4. 控制面补全（P2）

### P2.1 `/ext` 命令展示 CapabilityStatus（已完成）

当前 `/ext` 展示已满足 kind、ready、detail、tool_names 的基本需求，无需额外代码改动。

### P2.2 `/cancel` 用户确认反馈（已完成）

当前 `/cancel` 已在发送取消信号后立即返回显式确认文本。

### P2.3 Telegram 表格渲染（已完成）

**来源**：`docs/pending-decisions.md`

**已决策**：采用代码块方案。

当前实现会把 Markdown 风格表格自动转换为等宽代码块，并已补回归测试，确保 fenced code block 内的示例表格文本不会被错误重写。

### P2.4 Function Result Clearing（待做）

**来源**：`docs/claude-code-harness-analysis.md` 第 2.3 节

Claude Code 只保留最近 N 个工具结果，更早的自动清除。当前 ragtag_crew 所有 tool result 永远留在 messages 里直到 compact_history 触发。可在 compaction 前增加细粒度清理。

---

## 5. 下一阶段主线

### P3 两阶段调用（draft + verify）

**来源**：Meta-Harness 论文 + `project-roadmap.md` M7 待做项

**方案**：先生成变更，再独立验证（lint、类型检查、测试），确认后算完成。

依赖 P0.3（session summary 质量），因为两阶段调用会显著增加每轮消息数。

---

## 6. Roadmap 后续里程碑

### M6：外部结果进入上下文系统

暂不急。`context_builder.py` 已有 prompt 级引导（External Result Policy 段），外部工具使用频率低。

需要回答的问题（来自 roadmap）：
- 哪些结果只属于当前轮临时证据
- 哪些结果应该进入 session_summary
- 哪些结果适合被手动或自动写入 memory
- 哪些结果需要保留来源信息

### M7 剩余：历史查询 CLI

Meta-Harness 附录 D 建议"提供小 CLI 查询历史，方便人工介入和调试"。

### M8：上下文系统高级增强

`context-system-design.md` 规划的后续阶段：

1. Protected content 规则
2. Block 化 compression state（替代单一 session_summary 字符串）
3. Memory search（当前 memory 只做 add/view/promote）
4. `/prompt` 会话级临时规则
5. 压缩前记忆落盘策略
6. 外部检索层评估（qmd 等）

---

## 7. 代码质量与技术债

### 7.1 重复代码

| 内容 | 位置 |
|------|------|
| `_OUTPUT_LIMIT = 50_000` + `_truncate()` + `_clip()` | `web_search.py`、`openapi_provider.py`、`everything.py` 三处重复 |

应提取为共享常量和工具函数。

### 7.2 工具链升级

**来源**：`docs/research-agent-toolchain.md`

| 优先级 | 项 | 状态 |
|--------|-----|------|
| P0 | rg 自动下载机制 | `config.py` 已有 `rg_command` 和 `tools_cache_dir` 配置，但未实现自动下载 |
| P1 | 去掉 grep/find 路径限制（只读操作不应受 resolve_path 限制） | 未做 |
| P2 | fd 作为可选外部工具 | `config.py` 已有 `fd_enabled`/`fd_command` 配置，但未实现 |

### 7.3 System Prompt 缓存友好性

**来源**：`docs/claude-code-harness-analysis.md` 第 3.1 节

当前 `context_builder.py` 拼接顺序：
```
base → planning → PROJECT.md → USER.local.md → MEMORY.md → skills → policy → session_prompt → session_summary
```

前五段相对静态，后四段每次调用都变。如果后端支持 prefix cache（如 Claude 的 prompt caching），应明确分界标记。取决于 litellm 转发后端是否支持，暂不强制。

---

## 8. 建议执行顺序

```
P2.4 Function Result Clearing   中小改动
       ↓
P3    两阶段调用（draft + verify）  较大改动
       ↓
M6    外部结果进入上下文系统
       ↓
P1.2 Skill 按需注入
       ↓
M8    上下文系统高级增强
```

**现阶段原则**：前一轮稳定性、环境引导、摘要质量和基础控制面已经完成；下一轮应优先做“让结果更可治理、更可验证、更能被后续轮次复用”的能力。

---

## 9. 已完成的一周迭代

结果：这一轮迭代已完成以下 5 个工作包，并全部通过回归验证。

### 工作包 1：P0.1 MCP 全链路超时 ✅

**目标**：避免 MCP server 在启动、握手或 `list_tools` / `call_tool` 阶段挂起时拖死整个启动流程或工具调用。

**涉及文件**：
- `src/ragtag_crew/external/mcp_client.py`

**计划改动**：
- 给 `_list_tools_for_server()` 的 `stdio_client()` + `session.initialize()` + `session.list_tools()` 增加统一 `asyncio.wait_for(...)` 超时包裹
- 给 `_call_tool_on_server()` 的 `stdio_client()` + `session.initialize()` + `session.call_tool()` 增加外层总超时保护
- 保留现有 `read_timeout_seconds=settings.external_tool_timeout`，让 RPC 读超时和链路总超时同时存在
- 在 `discover_mcp_tools()` 的工具注册循环加 `try/except`，避免单个 remote tool 异常中断整台 server 的发现

**验收标准**：
- MCP server 不存在、启动失败或握手卡住时，系统能在配置超时内返回失败状态，而不是永久阻塞
- 单个 MCP server 失败不影响其他 server 的发现
- 单个 remote tool 注册失败不影响同 server 其他工具注册
- 测试全部通过

**风险 / 依赖**：
- 需要确认 `mcp` SDK 在外层 `wait_for` 取消时不会留下僵尸子进程；如发现问题，需要补清理逻辑

### 工作包 2：P0.2 外部能力初始化错误处理 ✅

**目标**：让外部能力初始化过程从“静默失败”变成“可观测、可诊断、部分成功可保留”。

**涉及文件**：
- `src/ragtag_crew/external/manager.py`

**计划改动**：
- 引入模块级 logger
- `ensure_external_capabilities_initialized()` 中保存 `create_task()` 返回值，并通过 `add_done_callback` 记录后台初始化异常
- 将 `initialize_external_capabilities()` 拆成更细粒度的注册步骤，逐段捕获异常并记录日志
- 即使 MCP 初始化失败，也保留 web search、Everything、browser、OpenAPI 已成功注册的 `CapabilityStatus`

**验收标准**：
- 在已有 event loop 中延迟初始化失败时，日志可见明确报错
- 任一 provider 初始化失败时，其它已成功 provider 的状态仍可通过 `/ext` 或状态接口读到
- 启动路径与测试路径行为一致，不再依赖“后续 force=True 补救”作为唯一保障
- 测试全部通过

**风险 / 依赖**：
- 需要注意 `_initialized` 何时置位，避免“部分初始化成功但永远不再重试”或“已成功却每次重复初始化”两种反向问题

### 工作包 3：P1.1 环境引导（env bootstrap） ✅

**目标**：在不侵入 agent 主循环的前提下，让模型在进入项目时自动获得一份轻量工作目录快照，减少 2-4 轮探索性调用。

**涉及文件**：
- `src/ragtag_crew/env_bootstrap.py`（新文件）
- `src/ragtag_crew/context_builder.py`
- `src/ragtag_crew/config.py`

**计划改动**：
- 新建 `env_bootstrap.py`，基于 `os.scandir` 生成深度受控的目录树快照
- 复用现有目录跳过思路，默认跳过 `.git`、`.venv`、`__pycache__`、`node_modules` 等目录，并扩展到 `.pytest_cache`、`.mypy_cache`、`dist`、`build` 等常见噪音目录
- 探测关键配置文件（如 `pyproject.toml`、`package.json`、`Cargo.toml`、`go.mod`、`Dockerfile`），读取前少量内容作为技术栈提示
- 引入 TTL 缓存与 token / 字符预算控制，避免每轮请求重复扫描和上下文膨胀
- 在 `context_builder.py` 中把 `Workspace Snapshot` 插入 `Project Context` 和 `User Context` 之间
- 在 `config.py` 中新增 `env_bootstrap_enabled`、`env_bootstrap_max_depth`、`env_bootstrap_max_tokens`、`env_bootstrap_skip_dirs`

**验收标准**：
- 新会话首次调用时，system prompt 中包含工作目录快照和关键配置文件提示
- 对中小仓库默认可读，对大仓库不会无限膨胀上下文
- 关闭配置后完全不注入快照
- 不修改 `agent.py`、`bot.py`、`session_store.py` 主流程也能生效
- 新增测试覆盖空目录、普通目录、超预算截断和配置关闭场景

**风险 / 依赖**：
- 需要控制好预算，避免 env bootstrap 抢占 session summary 和技能提示的上下文空间

### 工作包 4：P0.3 Session Summary 阶段 A ✅

**目标**：在不引入额外模型调用成本的前提下，先把现有机械摘要从“可用但严重丢信息”提升到“足够支撑长会话连续性”。

**涉及文件**：
- `src/ragtag_crew/session_summary.py`
- 可能补充 `tests/test_agent.py` 或单独新增 summary 相关测试文件

**计划改动**：
- 在 assistant tool call 摘要中保留关键参数，优先提取 `path`、`file`、`query`、`pattern`、`url` 等高价值字段
- 把正文截断阈值从当前 220 字符提高到更合理的水平，优先保留文件路径、搜索词、错误信息、返回摘要头部
- 调整 `_merge_summary()` 的拼接和截断策略，避免“新近压缩的信息反而先被裁掉”
- 保留多工具调用顺序，而不是仅用逗号拼接成无序列表
- 维持现有纯本地实现，不引入 small model 调用；LLM 语义摘要作为阶段 B 继续评估

**验收标准**：
- 压缩后的 summary 中能看出改过哪些文件、搜过什么、调用过哪些关键工具
- 多轮 compaction 后，最近一次被压缩进去的信息仍然可见
- 不增加额外 API 成本，不改变当前持久化结构
- 测试覆盖工具参数保留、截断策略和多次 merge 行为

**风险 / 依赖**：
- 机械摘要的上限依然存在，这一工作包的目标是“显著减损”，不是“彻底解决”；若后续两阶段调用带来更高消息量，仍需评估阶段 B

### 工作包 5：P2 控制面补全 ✅

**目标**：补齐用户可见的运行反馈，让能力状态、取消动作和结果展示更透明。

**涉及文件**：
- `src/ragtag_crew/telegram/bot.py`
- 如有需要，补充相关 formatter / helper 文件与测试

**计划改动**：
- 检查并完善 `/ext` 输出，确保 `CapabilityStatus` 的 kind、ready、detail、tool_names 均能稳定展示
- 给 `/cancel` 增加显式确认回复，而不是只依赖 streamer 事件侧反馈
- 结合 `docs/pending-decisions.md`，先落一版保守的 Telegram 表格渲染方案；若决策未最终确认，优先采用代码块方案
- 评估是否顺手引入轻量版 Function Result Clearing，至少在 compaction 前清理陈旧大 tool result

**验收标准**：
- `/ext` 输出能帮助定位是 MCP、browser、OpenAPI 还是 web search 出问题
- `/cancel` 对用户立即有可见反馈
- Telegram 中常见结构化结果可读，不出现大面积错位或难以阅读的渲染

**风险 / 依赖**：
- 表格渲染的最终方案仍受 `docs/pending-decisions.md` 中产品决策约束；本周可先落默认实现，再根据体验微调

### 本轮结束时的里程碑检查

当前项目状态已达到以下结果：

- 外部能力层从“可用但脆弱”提升为“可超时、可诊断、部分失败不拖垮整体”
- 新会话具备基础项目环境引导，减少冷启动探索成本
- 长会话摘要质量显著改善，为后续两阶段调用和 M6/M8 打基础
- Telegram 控制面更完整，用户能更清楚地看到系统状态与取消反馈
- 相关回归测试已扩充到 233 个，并全部通过

### 下一轮衔接建议

本周完成后，建议按以下顺序进入下一阶段：

1. P3 两阶段调用（draft + verify）
2. M6 外部结果进入上下文系统
3. P1.2 Skill 按需注入
4. M8 上下文系统高级增强

原因：P3 的收益最高，但它会显著增加消息量，必须建立在本周的摘要质量和稳定性改进之上；M6 和 M8 则依赖更稳定的上下文基础设施。

---

## 10. 不做的事项

（来自 `project-roadmap.md` 第 7 节，维持不变）

- MCP server（当前只做 client）
- 动态 OpenAPI spec 导入
- qmd 直接接入主链路
- block 化 compression state（等基础摘要稳定后再评估）
- 图片 / 文件输入
- 多前端抽象
- Swarm / Remote Agent / Worktree Isolation（单用户不需要）
