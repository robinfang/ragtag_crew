# Python Telegram Agent 方案（路径 C：自建 Agent 引擎）

> 纯 Python 实现，自建 agent loop + litellm 多模型 + python-telegram-bot 接入 Telegram
> 创建时间：2026-04-03

---

## 1. 目标与定位

**开源项目**，用户自行部署到本地电脑，**单用户使用**。

不依赖任何外部 agent SDK（不依赖 Pi SDK、不依赖 claude-agent-sdk），用 Python 自建轻量 agent 引擎，搭建一个最小可用的 Telegram AI Agent。

**核心价值：**
- 纯 Python，无需安装 Node.js
- 多模型支持（Claude、GPT、Gemini、GLM、DeepSeek、Ollama...），通过 litellm 统一接入
- 完全可控，agent loop 代码透明，不受任何 SDK 版本变动影响
- 代码量约 1000-1400 行，仍然是小项目

**与原 TS 方案的核心差异：**

| 维度 | TS + Pi SDK | Python 自建 |
|---|---|---|
| 语言 | TypeScript | Python |
| Agent 引擎 | Pi SDK（封装好的） | 自建 tool loop |
| LLM 层 | pi-ai（15+ provider） | litellm（100+ 模型） |
| Telegram 框架 | grammy | python-telegram-bot |
| 运行时 | Node.js 22+ | Python 3.12+ |
| 代码量 | ~400-500 行 | ~1000-1400 行 |
| SDK 风险 | headless 模式不确定性 | 无（全部自建） |

---

## 2. 架构

```
┌─────────────────────────────────────────────┐
│                  用户                        │
│            (Telegram App)                    │
└──────────────┬──────────────────────────────┘
               │ Telegram Bot API (long polling)
               ▼
┌─────────────────────────────────────────────┐
│        Telegram 接入层 (python-telegram-bot)  │
│  ┌─────────────┐  ┌───────────────────────┐ │
│  │ 消息接收     │  │ 流式回复/编辑消息      │ │
│  │ + userId鉴权 │  │ + 节流 + 分段         │ │
│  └──────┬──────┘  └───────────▲───────────┘ │
└─────────┼─────────────────────┼─────────────┘
          │                     │
          ▼                     │
┌─────────────────────────────────────────────┐
│              会话路由                        │
│        Map[chat_id → AgentSession]           │
└─────────┬───────────────────────▲───────────┘
          │ run(prompt)           │ on_event()
          ▼                     │
┌─────────────────────────────────────────────┐
│            自建 Agent 引擎                    │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐ │
│  │ LLM 层   │  │ Agent    │  │ Tools      │ │
│  │ (litellm)│  │ Loop     │  │ (可配置)   │ │
│  │          │  │ (核心循环)│  │            │ │
│  └──────────┘  └──────────┘  └───────────┘ │
└─────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────┐
│              Config 层                       │
│  .env (secrets) + config.yaml (模型/工具)     │
└─────────────────────────────────────────────┘
```

---

## 3. 核心模块

### 3.1 Agent Loop（核心引擎）

**职责：** 管理对话状态、驱动 LLM ↔ Tool 调用循环、产生事件流

这是整个方案的核心，替代 Pi SDK 的 `AgentSession`。本质是一个 while 循环：

```python
class AgentSession:
    def __init__(self, model: str, tools: list[Tool], system_prompt: str = ""):
        self.model = model
        self.tools = tools
        self.system_prompt = system_prompt
        self.messages: list[dict] = []        # 对话历史
        self._callbacks: list[Callable] = []  # 事件订阅

    async def prompt(self, text: str) -> None:
        """发送用户消息，驱动 agent loop"""
        self.messages.append({"role": "user", "content": text})
        self._emit("agent_start")

        while True:
            self._emit("turn_start")

            # 1. 调用 LLM（流式）
            self._emit("message_start")
            response = await self._stream_llm_call()
            self._emit("message_end")

            # 2. 检查是否有 tool call
            if not response.tool_calls:
                break  # 无工具调用，agent 完成

            # 3. 执行工具
            tool_results = []
            for tool_call in response.tool_calls:
                self._emit("tool_execution_start", tool_call)
                result = await self._execute_tool(tool_call)
                self._emit("tool_execution_end", tool_call, result)
                tool_results.append(result)

            # 4. 把工具结果加入对话历史，继续循环
            self.messages.append({"role": "assistant", "tool_calls": response.tool_calls})
            for result in tool_results:
                self.messages.append({"role": "tool", "content": result})

            self._emit("turn_end")

        self._emit("agent_end")

    async def _stream_llm_call(self):
        """流式调用 LLM，逐 token 触发 message_update 事件"""
        stream = await litellm.acompletion(
            model=self.model,
            messages=self._build_messages(),
            tools=self._build_tool_schemas(),
            stream=True,
        )
        full_response = ""
        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                full_response += delta.content
                self._emit("message_update", delta=delta.content)
        return stream.final_response  # 完整 response 含 tool_calls

    def subscribe(self, callback: Callable) -> None:
        """订阅事件流"""
        self._callbacks.append(callback)
```

**关键设计决策：**

| 决策点 | 选择 | 理由 |
|---|---|---|
| 流式 vs 非流式 | 流式 | 用户体验；Telegram 编辑消息需要增量更新 |
| tool call 格式 | OpenAI function calling 标准 | litellm 统一了所有 provider 到此格式 |
| 对话历史存储 | 内存 list[dict] | Phase 1 足够；Phase 3 加持久化 |
| 最大循环次数 | 20 轮 | 防止无限循环（工具调用死循环） |
| 单轮超时 | 120s | 单次 LLM 调用 + 工具执行总超时 |

**与 Pi SDK AgentSession 的功能对应：**

| Pi SDK | Python 自建 | 实现复杂度 |
|---|---|---|
| `session.prompt(text)` | `agent.prompt(text)` | 低 — while 循环 |
| `session.subscribe(cb)` | `agent.subscribe(cb)` | 低 — callback list |
| `session.abort()` | `agent.abort()` | 低 — asyncio.Event |
| `session.setModel(model)` | `agent.model = "new_model"` | 极低 — 属性赋值 |
| `session.steer(msg)` | `agent.steer(msg)` | 中 — 需要在流式过程中注入 |
| `session.followUp(msg)` | `agent.follow_up(msg)` | 中 — 排队下一轮 |
| `session.newSession()` | `agent.reset()` | 低 — 清空 messages |
| `session.setActiveToolsByName()` | `agent.set_tools(names)` | 低 — 过滤 tools 列表 |
| `message_update` 事件 | `message_update` 事件 | 低 — stream chunk 回调 |
| `tool_execution_start/end` | 同名事件 | 低 — 工具执行前后触发 |

**估算代码量：** ~300-400 行（含事件系统、abort、steer、错误处理）

### 3.2 LLM 调用层（litellm）

**职责：** 统一所有 LLM provider 的 API 调用

**为什么选 litellm：**
- 一个 `completion()` 函数支持 100+ 模型，自动处理各 provider API 差异
- 内置 streaming、tool_use、重试、fallback
- 活跃维护（GitHub 20k+ stars）
- OpenAI function calling 格式作为统一输出，不需要自己处理各 provider 的 tool_use 差异

**支持的 Provider（远超 Pi SDK 的 15+）：**

| Provider | litellm 模型前缀 | 示例 |
|---|---|---|
| Anthropic Claude | `anthropic/` | `anthropic/claude-sonnet-4-20250514` |
| OpenAI | `openai/` 或直接写 | `gpt-4o` |
| Google Gemini | `gemini/` | `gemini/gemini-2.5-pro` |
| AWS Bedrock | `bedrock/` | `bedrock/anthropic.claude-v2` |
| GLM (智谱) | `openai/` + base_url | 见下方配置 |
| DeepSeek | `deepseek/` | `deepseek/deepseek-chat` |
| Ollama | `ollama/` | `ollama/llama3` |
| OpenRouter | `openrouter/` | `openrouter/anthropic/claude-3.5-sonnet` |
| Groq | `groq/` | `groq/llama-3.1-70b` |
| Mistral | `mistral/` | `mistral/mistral-large-latest` |
| 任意 OpenAI 兼容 | `openai/` + base_url | 自定义 |

**GLM 接入方式（litellm）：**
```python
import litellm

response = await litellm.acompletion(
    model="openai/glm-4-plus",
    messages=[{"role": "user", "content": "hello"}],
    api_base="https://open.bigmodel.cn/api/paas/v4",
    api_key=os.getenv("ZAI_API_KEY"),
)
```

或通过环境变量配置：
```bash
# .env
GLM_API_KEY=your_key
GLM_API_BASE=https://open.bigmodel.cn/api/paas/v4
```

**关键优势 vs pi-ai：**
- **不需要 `models.json` 配置文件**：litellm 通过模型前缀自动路由到正确 provider
- **不存在 `openai-completions` vs `openai-responses` API 类型困惑**：litellm 自动处理
- **tool_use 格式统一**：所有 provider 的 tool call 都归一化为 OpenAI function calling 格式

**估算代码量：** ~50-100 行（配置加载 + litellm 封装）

### 3.3 工具系统

**职责：** 定义工具 schema、执行工具、管理工具预设

#### 3.3.1 工具定义

```python
from dataclasses import dataclass
from typing import Any, Callable, Awaitable

@dataclass
class Tool:
    name: str
    description: str
    parameters: dict          # JSON Schema
    execute: Callable[..., Awaitable[str]]  # 异步执行函数

# 示例：read 工具
read_tool = Tool(
    name="read",
    description="Read file contents. Returns the text content of the specified file.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute or relative file path"},
            "offset": {"type": "integer", "description": "Start line (1-based)", "default": 1},
            "limit": {"type": "integer", "description": "Max lines to read", "default": 2000},
        },
        "required": ["path"],
    },
    execute=read_file,
)
```

#### 3.3.2 内置工具清单

| 工具 | 功能 | Python 实现 | 代码量 |
|---|---|---|---|
| `read` | 读取文件内容 | `open().read()` + 行号 + offset/limit | ~30 行 |
| `write` | 创建/覆盖文件 | `open().write()` | ~15 行 |
| `edit` | 精确替换文件内容 | 字符串查找 + 替换 + 唯一性检查 | ~40 行 |
| `bash` | 执行 shell 命令 | `asyncio.create_subprocess_shell()` | ~35 行 |
| `grep` | 搜索文件内容 | `subprocess` 调用 `rg`，fallback 纯 Python `re` | ~30 行 |
| `find` | 查找文件 | `pathlib.Path.rglob()` + gitignore 过滤 | ~25 行 |
| `ls` | 列出目录内容 | `os.listdir()` + 文件信息 | ~20 行 |

各工具实现要点：

**read：**
```python
async def read_file(path: str, offset: int = 1, limit: int = 2000) -> str:
    path = _resolve_path(path)  # 相对路径 → 绝对路径，安全检查
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    selected = lines[offset - 1 : offset - 1 + limit]
    return "".join(f"{offset + i:6d}\t{line}" for i, line in enumerate(selected))
```

**bash：**
```python
async def run_bash(command: str, timeout: int = 30) -> str:
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=WORKING_DIR,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return f"[TIMEOUT] Command exceeded {timeout}s limit"
    output = stdout.decode() + (f"\nSTDERR:\n{stderr.decode()}" if stderr else "")
    return output[:50000]  # 限制输出长度，避免撑爆 context
```

**edit：**
```python
async def edit_file(path: str, old_string: str, new_string: str) -> str:
    path = _resolve_path(path)
    content = open(path, "r").read()
    count = content.count(old_string)
    if count == 0:
        return f"ERROR: old_string not found in {path}"
    if count > 1:
        return f"ERROR: old_string appears {count} times, must be unique. Provide more context."
    new_content = content.replace(old_string, new_string, 1)
    open(path, "w").write(new_content)
    return f"OK: replaced in {path}"
```

#### 3.3.3 工具预设

```python
TOOL_PRESETS = {
    "coding": ["read", "write", "edit", "bash", "grep", "find", "ls"],
    "readonly": ["read", "grep", "find", "ls"],
}
```

**估算代码量：** ~200-250 行（7 个工具 + 工具注册框架 + 路径安全检查）

### 3.4 Telegram 接入层

**职责：** 接收消息、流式回复、命令处理、userId 鉴权

**框架选择：python-telegram-bot v21+**

| 维度 | python-telegram-bot | aiogram v3 |
|---|---|---|
| Stars | 26k+ | 5k+ |
| 文档 | 非常完善 | 完善 |
| asyncio | 原生 | 原生 |
| 稳定性 | 高（10+ 年） | 高 |
| 社区 | 最大 | 较大 |
| API 覆盖 | 完整 | 完整 |

选 `python-telegram-bot` 因为社区最大、文档最全。

**关键实现：**

```python
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters

ALLOWED_USERS = set(map(int, os.getenv("ALLOWED_USER_IDS", "").split(",")))

async def auth_check(update: Update) -> bool:
    """userId 鉴权 — 最外层拦截"""
    if update.effective_user.id not in ALLOWED_USERS:
        await update.message.reply_text("Unauthorized.")
        return False
    return True

async def handle_message(update: Update, context) -> None:
    if not await auth_check(update):
        return

    chat_id = update.effective_chat.id
    text = update.message.text
    session = get_or_create_session(chat_id)

    # 检查是否正在处理中
    if session.is_busy:
        await update.message.reply_text("Please wait for the current response to finish.")
        return

    # 发送占位消息
    placeholder = await update.message.reply_text("Thinking...")

    # 启动流式回复
    streamer = TelegramStreamer(placeholder)
    session.subscribe(streamer.on_event)
    try:
        await session.prompt(text)
    except Exception as e:
        await placeholder.edit_text(f"Error: {e}")
    finally:
        session.unsubscribe(streamer.on_event)
```

**流式输出 → Telegram 消息编辑：**

```python
class TelegramStreamer:
    THROTTLE_INTERVAL = 1.5  # 秒，Telegram 编辑消息频率限制

    def __init__(self, message):
        self.message = message
        self.buffer = ""
        self.last_edit_time = 0
        self.last_sent_text = ""

    async def on_event(self, event_type: str, **kwargs):
        if event_type == "message_update":
            self.buffer += kwargs["delta"]
            now = time.time()
            if now - self.last_edit_time >= self.THROTTLE_INTERVAL:
                await self._flush()

        elif event_type == "tool_execution_start":
            tool = kwargs["tool_call"]
            self.buffer += f"\n⏳ Running: {tool.name}({_summarize_args(tool.args)})\n"

        elif event_type == "agent_end":
            await self._flush()  # 确保最后一个 chunk 不被节流丢掉

    async def _flush(self):
        text = self.buffer[:4096]  # Telegram 限制
        if text == self.last_sent_text:
            return  # 内容没变，跳过编辑，避免 "message is not modified" 错误
        try:
            await self.message.edit_text(text, parse_mode=None)  # Phase 1 先不用 Markdown
            self.last_sent_text = text
            self.last_edit_time = time.time()
        except telegram.error.RetryAfter as e:
            await asyncio.sleep(e.retry_after)  # 429 处理
        except telegram.error.BadRequest:
            pass  # 忽略编辑失败
```

**消息格式：Phase 1 先用纯文本（`parse_mode=None`）。**

原因：LLM 输出的标准 Markdown 与 Telegram MarkdownV2 严重不兼容（`.`, `-`, `(`, `)`, `!` 等大量字符需要转义）。Phase 2 再加 HTML 转换层。

**估算代码量：** ~200-250 行（bot 配置 + 消息处理 + 流式输出 + 命令 + 鉴权）

### 3.5 会话管理

```python
sessions: dict[int, AgentSession] = {}

def get_or_create_session(chat_id: int) -> AgentSession:
    if chat_id not in sessions:
        sessions[chat_id] = AgentSession(
            model=DEFAULT_MODEL,
            tools=get_tools_for_preset(DEFAULT_TOOL_PRESET),
            system_prompt=SYSTEM_PROMPT,
        )
    return sessions[chat_id]
```

单用户场景，不需要"池"或"管理器"，一个 dict 足够。

**估算代码量：** ~30 行

### 3.6 配置管理

```python
# config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Telegram
    telegram_bot_token: str
    allowed_user_ids: str = ""           # 逗号分隔

    # LLM
    default_model: str = "anthropic/claude-sonnet-4-20250514"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    glm_api_key: str = ""
    glm_api_base: str = "https://open.bigmodel.cn/api/paas/v4"

    # Agent
    working_dir: str = "."               # Agent 工作目录
    default_tool_preset: str = "coding"  # coding / readonly
    bash_timeout: int = 30               # bash 工具超时秒数
    max_turns: int = 20                  # agent loop 最大轮次

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
```

**估算代码量：** ~40 行

---

## 4. 数据流

### 4.1 用户发消息（流式）

```
1. Telegram → python-telegram-bot callback → 提取 text + chat_id + user_id
2. userId 鉴权（ALLOWED_USER_IDS 检查）
3. 检查 session.is_busy → 是则拒绝，提示等待
4. 查找/创建 AgentSession
5. 发送 "Thinking..." 占位消息
6. agent.prompt(text) 启动 agent loop
7. agent loop 内部循环：
   a. 流式调用 LLM → message_update 事件 → 节流 1.5s → editMessageText
   b. 如有 tool_call → tool_execution_start → 显示状态 → 执行 → tool_execution_end
   c. 工具结果加入 messages → 继续循环 (回到 a)
8. 无 tool_call → agent_end → 最终 flush（确保最后文本不丢失）
```

### 4.2 长消息分段

```
流式阶段：只编辑一条消息（截断到 4096 字符）
agent_end 后：如果总长度 > 4096，按段落/换行拆分为多条消息发送
```

### 4.3 工具调用流程

```
用户: "读一下 config.yaml 的内容"
  → Agent loop: LLM 返回 tool_call: read(path="config.yaml")
  → tool_execution_start → Telegram: "⏳ Running: read(config.yaml)"
  → Python: open("config.yaml").read() → 返回内容
  → tool_execution_end → 内容加入 messages
  → Agent loop: 继续调用 LLM，LLM 基于文件内容生成回复
  → message_update 流式 → Telegram 编辑消息
  → agent_end
```

---

## 5. 安全设计

### 5.1 Telegram userId 鉴权（最关键）

```python
# .env
ALLOWED_USER_IDS=123456789,987654321
```

在消息处理最外层检查。未授权用户的消息直接丢弃。

### 5.2 工作目录限制

```python
WORKING_DIR = Path(settings.working_dir).resolve()

def _resolve_path(path: str) -> Path:
    """将相对路径解析为绝对路径，确保不越界"""
    resolved = (WORKING_DIR / path).resolve()
    if not str(resolved).startswith(str(WORKING_DIR)):
        raise PermissionError(f"Access denied: {path} is outside working directory")
    return resolved
```

所有文件操作工具（read/write/edit）都经过此函数。bash 工具通过 `cwd=WORKING_DIR` 限制。

### 5.3 权限分级

```
/tools readonly  → 最安全，禁用 write/edit/bash
/tools coding    → 可读写文件 + 执行命令
```

### 5.4 bash 超时

```python
bash_timeout = settings.bash_timeout  # 默认 30s，可配置
```

超时后强制 kill 进程。

---

## 6. 技术选型

| 组件 | 选择 | 理由 |
|---|---|---|
| 语言 | Python 3.12+ | match/case、asyncio 改进、性能提升 |
| Telegram 框架 | python-telegram-bot v21 | 社区最大（26k stars）、asyncio 原生、文档最全 |
| LLM 统一层 | litellm | 100+ 模型统一 API、内置 streaming + tool_use |
| Agent loop | 自建 | ~300 行核心代码，完全可控 |
| 工具系统 | 自建 | ~200 行，基于 Python 标准库 |
| 配置管理 | pydantic-settings | 类型安全、自动读取 .env |
| 包管理 | uv（推荐）或 pip | uv 速度快 10-100x |
| 进程管理 | systemd (Linux) / 直接运行 (Windows) | 本地部署 |

---

## 7. 目录结构

```
py-telegram-agent/
├── src/
│   ├── __init__.py
│   ├── main.py              # 入口，启动 bot
│   ├── bot.py               # Telegram bot 配置 + 消息处理 + 鉴权
│   ├── agent.py             # AgentSession 核心引擎（agent loop）
│   ├── llm.py               # litellm 封装 + 模型配置
│   ├── tools/
│   │   ├── __init__.py      # Tool 基类 + 工具预设 + 工具注册
│   │   ├── file_tools.py    # read / write / edit
│   │   ├── shell_tools.py   # bash
│   │   └── search_tools.py  # grep / find / ls
│   ├── stream.py            # TelegramStreamer（流式输出 → 编辑消息）
│   └── config.py            # pydantic-settings 配置
├── .env.example              # 环境变量模板
├── .gitignore
├── pyproject.toml            # 项目元数据 + 依赖
└── README.md
```

**与原 TS 方案目录对比：**

| TS 方案 | Python 方案 | 说明 |
|---|---|---|
| `index.ts` | `main.py` | 入口 |
| `bot.ts` | `bot.py` | Telegram 接入 |
| `agent-factory.ts` | `agent.py` | Agent 引擎（Python 版包含完整 loop） |
| `session-manager.ts` | `bot.py` 内 dict | 单用户场景不需要单独模块 |
| `stream-handler.ts` | `stream.py` | 流式输出处理 |
| `tools/` | `tools/` | 工具目录 |
| `config.ts` | `config.py` | 配置 |
| 无 | `llm.py` | TS 版由 Pi SDK 内置，Python 版需要显式封装 |
| `config/models.json` | 无（litellm 自动路由） | litellm 不需要模型配置文件 |

---

## 8. 依赖清单

```toml
[project]
name = "py-telegram-agent"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "python-telegram-bot>=21.0",    # Telegram 接入
    "litellm>=1.60.0",              # LLM 统一调用
    "pydantic-settings>=2.0",       # 配置管理
]
```

**依赖分析：**

| 依赖 | 大小 | 传递依赖 | 说明 |
|---|---|---|---|
| python-telegram-bot | ~2 MB | httpx, anyio | 核心 Telegram 框架 |
| litellm | ~5 MB | openai, anthropic, tiktoken | LLM 统一层 |
| pydantic-settings | ~1 MB | pydantic, python-dotenv | 配置管理 |
| **总计** | **~8-10 MB** | | 不含 Python 本身 |

**对比 TS 方案：**
- TS + Pi SDK: ~15 MB (node_modules)
- Python: ~8-10 MB (site-packages，不含 Python 运行时)
- 都很轻量

---

## 9. 功能分阶段

### Phase 1: MVP（约 5-7 小时）

- [ ] 项目初始化（pyproject.toml、依赖安装）
- [ ] Agent loop 核心实现（LLM 调用 → tool_call 检测 → 工具执行 → 循环）
- [ ] 基础工具（read / write / edit / bash）
- [ ] litellm 集成，至少跑通一个模型（Claude 或 GLM）
- [ ] Telegram Bot 消息收发 + userId 鉴权
- [ ] 流式输出（编辑 Telegram 消息，节流 1.5s，429 处理）
- [ ] 并发消息拒绝（is_busy 检查）
- [ ] 错误处理 + 超时

### Phase 2: 多模型 + 命令 + 搜索工具（+3-4 小时）

- [ ] 补全搜索工具（grep / find / ls）
- [ ] Bot 命令：`/start`、`/new`（新会话）、`/model`（切换模型）、`/tools`（切换工具模式）
- [ ] 多模型切换验证（Claude ↔ GLM ↔ GPT ↔ Ollama）
- [ ] 工具执行状态实时显示
- [ ] Markdown → HTML 转换层（Telegram parse_mode="HTML"）

### Phase 3: 持久化 + 图片（+2-3 小时）

- [ ] 会话序列化 / 反序列化（JSON 文件）
- [ ] 程序重启后恢复会话
- [ ] 会话过期清理
- [ ] 图片 / 文件消息支持（下载 → base64 → 传给支持 vision 的模型）

### Phase 4: 增强（按需）

- [ ] 记忆系统（MEMORY.md 模式）
- [ ] System prompt 自定义（通过 Telegram 命令或文件）
- [ ] MCP server 集成
- [ ] 多 Agent 协调（subagent）

---

## 10. 与原 TS 方案的风险对比

| 风险 | TS + Pi SDK | Python 自建 | 说明 |
|---|---|---|---|
| SDK headless 模式不完整 | **中** — Phase 0 已验证可用，但长期可能有边界情况 | **无** — 无 SDK 依赖 | Python 完全消除此风险 |
| SDK API 变化 | **中** — 0.x 版本，breaking change 可能 | **无** | 自建代码不受外部影响 |
| Telegram Markdown 不兼容 | **高** — 两者都有 | **高** — 两者都有 | 解决方案相同：用 HTML 或纯文本 |
| 多模型兼容性 | **中** — pi-ai 自建 adapter | **低** — litellm 已处理 | litellm 社区维护的 adapter 更可靠 |
| tool_use 格式差异 | **低** — Pi SDK 统一 | **低** — litellm 统一 | 两者都有统一层 |
| 代码量增加 | — | **中** — 多 600-900 行 | 但代码简单、透明 |
| 并发 / 性能 | **低** — Node.js 单线程事件循环 | **低** — asyncio 单线程事件循环 | 架构等价 |
| 部署复杂度 | **低** — 需 Node.js | **低** — 需 Python | 两者都是单运行时 |

**新增风险（Python 独有）：**

| 风险 | 影响 | 缓解 |
|---|---|---|
| litellm tool_use 对某些 provider 支持不完整 | 特定模型无法使用工具 | 先验证目标模型的 tool_use 支持 |
| agent loop 自建的边界情况 | 工具调用死循环、异常未捕获 | max_turns 限制 + 全局 try/except |
| Python asyncio 学习曲线 | 回调地狱、deadlock | 保持简单的 async/await 模式 |

---

## 11. 可行性判断

| 维度 | 评分 | 说明 |
|---|---|---|
| 技术可行性 | **高** | 所有组件在 Python 中都有成熟方案 |
| 工作量 | **中等** | ~1000-1400 行，Phase 1 约 5-7 小时 |
| 维护性 | **高** | 代码完全自有，不依赖外部 agent SDK |
| 多模型支持 | **优于 TS 方案** | litellm 100+ 模型 vs pi-ai 15+ |
| 部署简易度 | **高** | `pip install` + `.env` + `python main.py` |

**总结：完全可行。** 核心代价是需要自建 agent loop（~300-400 行），但这部分代码逻辑清晰——就是一个 while 循环。换来的是零 SDK 依赖风险和更广的模型覆盖。

---

## 12. 参考项目

| 项目 | 价值 | 链接 |
|---|---|---|
| RichardAtCT/claude-code-telegram | Telegram Bot 架构、流式输出、会话管理 | [GitHub](https://github.com/RichardAtCT/claude-code-telegram) |
| Angusstone7/claude-code-telegram | DDD 分层设计 | [GitHub](https://github.com/Angusstone7/claude-code-telegram) |
| terranc/claude-telegram-bot-bridge | 最轻量的 Telegram + Agent 集成 | [GitHub](https://github.com/terranc/claude-telegram-bot-bridge) |
| openai/openai-agents-python | agent loop + tool 系统设计 | [GitHub](https://github.com/openai/openai-agents-python) |
| PleasePrompto/ductor | 多 CLI agent 控制、cron、webhook | [GitHub](https://github.com/PleasePrompto/ductor) |

**重点参考：**
- Telegram 层面的流式编辑、消息分段、Markdown 处理 → 看 `RichardAtCT/claude-code-telegram`
- Agent loop + tool 注册框架 → 看 `openai/openai-agents-python` 的实现

---

## 13. 下一步

1. 确认技术选型决策（litellm vs 直接用各 provider SDK、python-telegram-bot vs aiogram）
2. 确认 Phase 1 实现范围（默认模型、默认工作目录、鉴权方式）
3. 创建项目目录 + 初始化
4. Phase 1 开发
