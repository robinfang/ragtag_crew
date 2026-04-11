# 草台班子 · ragtag_crew

> 本项目由作者独立开发，与任何机构科研项目无关。
>
> This project is developed independently by the author and is not related to any institutional research project.

> OpenClaw 平替。参考 [Pi](https://pi.dev/) 一类 coding agent 的设计思路，用更轻量、更可控的方式，把本地 AI agent 接到 Telegram。

## 当前定位

- 正式接入渠道：Telegram（生产）和 REPL 终端（开发/调试）
- Agent 跑在你自己的机器上，模型调用走你自己的 API key
- 用 Python 自建 agent loop，不依赖第三方 agent SDK
- 后续可以再扩展 Web、Discord 等入口，但现在不提前做多前端抽象

## 当前能力

- litellm 统一接入多模型
- 已实现 `read` / `write` / `edit` / `delete_file` / `bash` / `grep` / `find` / `ls` 等基础工具，并补充 `create_workspace` / `list_workspaces` / `delete_workspace` / `cleanup_workspaces` / `write_script` 等工作区管理工具
- 工具路径沙箱：写操作限制在工作目录内，读操作允许访问任意绝对路径
- 文件删除保护：bash 拦截 `rm`/`del`/`rmdir` 等删除命令，统一通过 `delete_file` 工具执行
- 工作目录级 workspace 管理：每个 `working_dir` 下维护独立的 `.ragtag_crew/workspaces/`，默认搜索会隐藏该目录，但可通过专用 workspace 工具稳定复用脚本与临时产物
- 新脚本根目录保护：新建脚本如果目标位于 `working_dir` 根目录，会被视为歧义路径并拒绝直接 `write`；应明确写入项目子目录，或使用 `write_script` 写入 managed script workspace
- Telegram 流式输出、HTML 富文本渲染、消息编辑节流、单用户鉴权已接通
- 已支持运行时进度快照：忙碌时可识别进度询问并返回当前 turn、工具执行数、最近响应预览
- 已支持 `/cancel` 显式确认反馈，取消与超时在运行时语义上分离
- Telegram 表格渲染已做基础适配：Markdown 风格表格会自动转成等宽代码块，避免消息中表格错位
- 完善的命令级日志记录：状态变更 INFO、权限/失败 WARNING、只读查询 DEBUG
- REPL 终端模式已支持实时流式输出、执行轨迹收集、会话持久化和 /plan 命令
- `prompts.py` 提取了共用的 `DEFAULT_SYSTEM_PROMPT`，Telegram 与 REPL 共享同一套系统提示词
- `session_store.py` 已从 `telegram/` 提升到包根目录，REPL 和 Telegram 共用同一套持久化逻辑
- 已支持仓库内本地 Markdown skill 的会话级启用，并改为“名称 + 摘要 + 路径”的轻量注入；完整内容按需读取
- 已接入 `PROJECT.md` / `USER.local.md` / `MEMORY.md` 分层上下文
- 已支持 `/prompt` 会话级临时规则，以及独立的 Protected Content 注入层，用于放置不应被普通会话压缩影响的规则
- 已新增 `Workspace Snapshot` 环境引导：自动注入受控目录树和关键配置文件摘要，降低冷启动探索成本
- 已提供最小 `/memory` 闭环：追加到 `memory/inbox.md`、查看文件、搜索历史记忆、手动 promote 到长期层
- 已支持 `session_summary` 会话压缩：只保留最近消息窗口，其余折叠为摘要，并保留关键工具参数、调用顺序和更高保真度的摘要文本
- 已支持压缩前记忆落盘评估版：可在会话压缩前把显式记忆意图的旧消息去重写入 `memory/inbox.md`（默认关闭）
- 已支持最小 block 化 compression state：每次压缩会生成独立 compression block，并持久化到 session，用于更结构化地表示旧上下文
- 已提供最小 `/context` 命令：查看当前摘要状态，并手动触发一次会话压缩
- 已支持历史查询 CLI：可列出已保存会话，并查看指定 chat_id 的摘要与最近消息
- 已建立阶段 1 外部能力接入层，支持平台工具、`MCP client`、固定 `OpenAPI provider`、`web_search`、`Everything` 与浏览器能力
- MCP 发现和调用链路已补全超时保护；外部能力初始化支持日志化、部分成功保留与失败后自动重试
- 已支持最小联网搜索 API 接入口，可按配置启用 `web_search`
- Windows 下可启用 `Everything` 搜索适配器；可通过 `/mcp` 查看已配置 MCP 状态
- 已支持固定 `OpenAPI provider` 接入，可为未来 search gateway 预留稳定工具入口；可通过 `/ext` 查看外部能力总状态
- 已接入基于 `agent-browser` 的浏览器能力骨架，支持独立浏览器模式与当前 Chromium 浏览器接管模式
- 浏览器第一版安全边界已接入：可配置域名白名单，attached 模式要求显式确认
- 图片输入仍在后续阶段

## 快速开始

```bash
uv run ragtag-crew -h
uv sync
cp .env.example .env
# 编辑 .env，填入 TELEGRAM_BOT_TOKEN、ALLOWED_USER_IDS 和至少一个模型 API Key

uv run ragtag-crew
# 或
uv run python -m ragtag_crew.main
```

可选：

- 在仓库根目录创建或更新 `skills/<name>.md`，这就是本地 skill 的“部署”；再通过 `/skills` 和 `/skill use <name>` 启用
- 编辑 `PROJECT.md`、`MEMORY.md` 和本地私有的 `USER.local.md` 来调节长期上下文
- 用 `/memory add <note>` 快速把一条长期信息记到 `memory/inbox.md`
- 用 `/memory search <query>` 在 `MEMORY.md` 和 `memory/*.md` 中按需检索历史记忆
- 用 `/memory promote [target]` 把 `inbox.md` 中待整理条目并入 `MEMORY.md` 或指定记忆文件
- 用 `/context` 查看当前会话摘要状态，必要时用 `/context compress` 手动收口
- 用 `/prompt set <text>` 设置当前会话临时规则，用 `/prompt protect <text>` 写入受保护内容
- 可用 `ragtag-crew --history-list` 列出已保存会话，用 `ragtag-crew --history <chat_id>` 查看会话摘要与最近消息
- 会话忙碌时直接问“进度”“进展”“好了没”等，机器人会返回实时快照
- 用 `/cancel` 中止当前任务，机器人会立即确认已发送取消信号
- 如需保存可复用脚本或临时工作区，优先使用 `write_script`、`create_workspace`、`list_workspaces` 等 workspace 工具；普通 `find/grep/ls` 默认不会把 `.ragtag_crew/` 混入项目浏览结果
- 复制 `mcp_servers.example.json` 为 `mcp_servers.local.json` 后，可通过 `/mcp` 查看 MCP server 状态
- 复制 `openapi_tools.example.json` 为 `openapi_tools.local.json` 后，可通过 `/ext` 查看固定 OpenAPI provider 状态
- 配置 `WEB_SEARCH_*` 后，可把 `web_search` 挂到 `coding` / `readonly` 预设中
- 安装 `agent-browser` 并启用 `AGENT_BROWSER_*` / `BROWSER_*` 配置后，可通过 `/browser` 和 `/ext` 管理浏览器能力
- 如需限制浏览器能力范围，可配置 `BROWSER_ALLOWED_DOMAINS`；attached 模式默认要求先执行 `/browser confirm-attached`
- 当前浏览器接管支持两条路径：`BROWSER_ATTACHED_CDP_URL`（手动 CDP，较稳）和 `BROWSER_ATTACHED_AUTO_CONNECT=true`（自动发现，较省事）

## Skill 说明

- 当前只支持仓库内 `skills/` 目录下的本地 Markdown skill，不支持 `/skill install` 一类安装/注册流程
- agent 启用 skill 后，system prompt 只注入 skill 的名称、摘要和文件路径，不会把全文直接塞进 prompt
- skill 摘要来自该 Markdown 文件里第一条非空、非标题行；`/skills` 展示的就是这条摘要
- 如果模型需要完整说明，应通过 `read` 工具读取对应的 `skills/<name>.md`

## 目录结构

```text
ragtag_crew/
├── src/
│   └── ragtag_crew/
│       ├── main.py               # 入口（含 REPL 模式）
│       ├── config.py             # 配置加载
│       ├── agent.py              # 自建 agent loop
│       ├── llm.py                # litellm 封装
│       ├── context_builder.py    # system prompt 分层组装
│       ├── session_summary.py    # 会话压缩与摘要
│       ├── session_store.py      # 会话持久化（Telegram 与 REPL 共用）
│       ├── prompts.py            # 共用系统提示词常量
│       ├── repl_streamer.py      # REPL 实时流式输出
│       ├── trace.py              # 执行轨迹收集
│       ├── env_bootstrap.py      # 工作区快照与环境引导
│       ├── workspace_manager.py  # working_dir 级 workspace 元数据与生命周期
│       ├── telegram/
│       │   ├── bot.py            # Telegram 接入层
│       │   ├── html.py           # Telegram HTML 渲染
│       │   └── stream.py         # 流式输出与消息编辑
│       ├── external/
│       │   ├── manager.py        # 外部能力初始化编排
│       │   ├── mcp_client.py     # MCP 发现与调用
│       │   ├── openapi_provider.py # 固定 OpenAPI provider
│       │   ├── web_search.py     # 联网搜索
│       │   ├── everything.py     # Windows Everything 适配
│       │   └── browser_agent.py  # 浏览器能力接入
│       └── tools/
│           ├── __init__.py       # 工具注册与预设
│           ├── file_tools.py     # read / write / edit / delete_file
│           ├── shell_tools.py    # bash（含删除命令拦截）
│           ├── search_tools.py   # grep / find / ls
│           ├── workspace_tools.py # workspace / write_script
│           └── path_utils.py     # 路径沙箱工具函数
├── tests/                        # 测试
├── archive/
│   └── pi-sdk-validation/    # 早期 Pi SDK 验证资料
├── PROJECT.md                # 仓库共享的项目背景
├── MEMORY.md                 # 长期记忆索引
├── memory/                   # 长期记忆正文
├── skills/                   # 本地 skill
├── python-telegram-agent-proposal.md
├── pyproject.toml
└── .env.example
```

## 设计说明

- `ragtag_crew` 是产品名，也是 Python 包名
- `src/` 只是源码容器目录；真正的包在 `src/ragtag_crew/`
- `telegram/` 明确表示这是当前唯一前端，而不是整个项目名

## 相关文档

- `python-telegram-agent-proposal.md`：当前 Python 方案设计文档
- `project-roadmap.md`：当前项目级路线图与阶段安排
- `search-gateway-plan.md`：搜索内置层与独立 gateway 的专项设计
- `archive/pi-sdk-validation/`：早期 Pi SDK 方向的验证记录，仅保留参考

## License

Apache-2.0
