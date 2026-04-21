# 草台班子 · ragtag_crew

> 单用户、自托管、本机运行的 AI coding agent。
>
> 用 Python 自建 agent loop，把本地 agent 接到 Telegram、微信和 REPL；默认走你自己的 API key，也可复用 OpenCode 的 Codex / ChatGPT 登录态。

## 当前定位

- 适合想在自己机器上跑 agent，通过 IM 或终端驱动代码、文件和命令行的个人开发者。
- 适合希望保留模型、工具、上下文和工作目录控制权的本地工作流。
- 不适合多用户协作、权限分层、托管 SaaS 或网页版控制台场景。
- 不把它定位成通用聊天机器人；核心目标仍是本地 coding / automation agent。

## 当前状态

### 前端入口

| 入口 | 状态 | 说明 |
| --- | --- | --- |
| Telegram | stable | 主入口；流式输出、后台执行、命令集最完整 |
| 微信 | stable | 后台执行 + 主动通知；命令集更精简，不做 Telegram 式单消息流式编辑 |
| REPL | dev | 开发 / 调试入口；支持流式输出、基础命令和会话持久化 |

### 核心能力

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| Plan mode | stable | 两阶段协议；用户确认前不执行工具 |
| 会话与上下文 | stable | session 持久化、路由、压缩、记忆已可用 |
| 外部能力 | optional | `MCP`、固定 `OpenAPI`、`web_search`、Everything 按配置启用 |
| 浏览器能力 | experimental | 基于 `agent-browser`；attached 模式需显式确认 |

## 快速开始

PowerShell:

```powershell
uv sync
Copy-Item .env.example .env

# 至少启用一个前端
# Telegram: TELEGRAM_BOT_TOKEN=...
# 微信: WEIXIN_ENABLED=true，并配置 WEIXIN_CREDENTIALS_PATH
# 至少配置一种可用模型凭据
# 如需复用 OpenCode 登录态，可设 OPENAI_AUTH_MODE=codex

uv run ragtag-crew --check
uv run ragtag-crew
```

其他 shell 把 `Copy-Item` 换成 `cp` 即可。也可以用 `uv run python -m ragtag_crew.main` 启动。

可选增强：

- 如需接入 `GPT-5.4`，可在 `AVAILABLE_MODELS` 中保留或加入 `openai/gpt-5.4`。
- 如需固定 Codex 代理，可设置 `CODEX_PROXY=http://localhost:1087`。
- Windows 下启用微信时，建议把 `WEIXIN_CREDENTIALS_PATH` 写成 `C:/Users/<username>/.weixin-bot/credentials.json`。
- 复制 `mcp_servers.example.json` 为 `mcp_servers.local.json` 后，可启用 `MCP`。
- 复制 `openapi_tools.example.json` 为 `openapi_tools.local.json` 后，可启用固定 `OpenAPI provider`。
- 配置 `WEB_SEARCH_*` 后，可启用 `web_search`。
- 安装 `agent-browser` 并配置 `AGENT_BROWSER_*` / `BROWSER_*` 后，可启用浏览器能力。
- 开发模式可用 `uv run ragtag-crew --dev`。

## 一个最短流程

1. 输入 `/plan on` 打开 plan mode。
2. 发送一个需要读写文件或调用工具的复杂请求。
3. 系统先返回编号计划；回复“继续”后才真正执行。
4. 长任务期间可以直接问“进度如何”，也可以用 `/cancel` 中止。

## 常用操作

**跨前端通用**

- `/help` 查看常用命令。
- `/new` 清空当前绑定的会话。
- `/cancel` 取消当前任务。
- `/plan` 查看当前模式；`/plan on|off` 切换 plan mode。
- `/sessions` 列出最近保存的 session。
- `/session current|use <session_key>|use <index>|reset` 查看、切换或恢复默认 session 绑定。

**Telegram / REPL**

- `/model` 查看或切换模型。
- `/tools` 查看当前工具预设。
- `/skills`、`/skill use|drop|clear` 管理仓库内本地 Markdown skills。

**Telegram**

- `/memory list|show|search|add|promote` 管理长期记忆。
- `/context show|compress` 查看或手动压缩上下文。
- `/prompt ...` 管理会话级临时规则与 protected content。
- `/mcp`、`/ext`、`/browser` 查看或管理外部能力。

**REPL**

- `/quit` 退出。
- `Ctrl+C` 可中止当前回复，等效 `/cancel`。

## 能力简介

- **多模型接入**：通过 `litellm` 统一接入多模型；`openai/gpt-5.4` 可选走 Codex OAuth 路线。
- **工具与沙箱**：提供 `read`、`write`、`edit`、`delete_file`、`bash`、`grep`、`find`、`ls` 及 workspace 工具，默认按工作目录做写入边界控制。
- **Plan mode**：从“提示词约束”升级成“运行时协议”；复杂请求先出计划，确认后才进入真实执行。
- **会话管理**：Telegram、微信、REPL 默认彼此独立；通过 `session_routes` 可把当前聊天窗口绑定到任意 `session_key`，手动共享同一会话。
- **上下文系统**：支持 `PROJECT.md` / `USER.local.md` / `MEMORY.md` 分层上下文、本地 skills、`/prompt`、`/memory` 和 `session_summary`。
- **运行体验**：Telegram 普通消息走后台 task 并持续流式更新；微信普通消息走后台 task、主动通知和进度查询；REPL 支持流式输出、trace 和持久化。
- **外部能力**：支持 `MCP client`、固定 `OpenAPI provider`、`web_search`、Windows Everything 与浏览器能力接入。

## 需要先知道的行为边界

- `plan mode` 是运行时强约束，不只是 system prompt 里的建议。
- Telegram、微信、REPL 默认不自动共享历史；只有手动绑定到同一 `session_key` 才会共用会话。
- 微信当前的目标是“后台执行 + 主动通知 + 进度查询”，不是 Telegram 那种单消息流式编辑体验。
- 普通 `find` / `grep` / `ls` 默认不会把 `.ragtag_crew/` workspace 目录混入项目浏览结果。

## 安全边界

- 写操作限制在工作目录内；读操作允许访问任意绝对路径。
- `bash` 会拦截 `rm`、`del`、`rmdir` 等删除命令，统一通过 `delete_file` 工具执行。
- workspace 产物进入工作目录下的 `.ragtag_crew/workspaces/`，避免与项目源码混杂。
- 浏览器 attached 模式默认要求先执行 `/browser confirm-attached`。

## 目录结构

```text
ragtag_crew/
├── src/
│   └── ragtag_crew/
│       ├── main.py             # CLI / REPL / --dev
│       ├── agent.py            # 自建 agent loop 与 plan mode
│       ├── llm.py              # litellm 封装与 Codex 路由
│       ├── codex_auth.py       # OpenCode OAuth / Codex 凭据复用
│       ├── session_store.py    # Telegram / 微信 / REPL 共用持久化
│       ├── session_summary.py  # 会话压缩与摘要
│       ├── telegram/           # Telegram 接入层
│       ├── weixin/             # 微信接入层
│       ├── external/           # MCP / OpenAPI / web_search / Everything / browser
│       └── tools/              # file / shell / search / workspace tools
├── tests/
├── docs/
├── skills/
├── memory/
├── PROJECT.md
├── MEMORY.md
└── .env.example
```

## 文档导航

- `python-telegram-agent-proposal.md`：当前 Python 方案设计稿。
- `project-roadmap.md`：项目路线图、阶段判断和后续优先级。
- `search-gateway-plan.md`：搜索能力与独立 gateway 的专项设计。
- `docs/weixin-plan-mode-implementation-plan.md`：微信后台执行与 plan mode 强约束改造方案。
- `docs/background-job-plan.md`：长任务后台任务化的后续设计，已补充微信先行落地说明。
- `docs/web-frontend-plan.md`：未来 Web 第四前端接入方案。
- `PROJECT.md`：仓库共享的项目背景与默认约束。
- `MEMORY.md`：长期记忆索引；正文位于 `memory/` 目录。

## License

Apache-2.0

本项目由作者独立开发，与任何机构科研项目无关。
