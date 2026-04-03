# 草台班子 · ragtag_crew

> OpenClaw 平替。参考 [Pi](https://pi.dev/) 一类 coding agent 的设计思路，用更轻量、更可控的方式，把本地 AI agent 接到 Telegram。

## 当前定位

- 当前唯一正式接入渠道是 Telegram
- Agent 跑在你自己的机器上，模型调用走你自己的 API key
- 用 Python 自建 agent loop，不依赖第三方 agent SDK
- 后续可以再扩展 Web、CLI、Discord 等入口，但现在不提前做多前端抽象

## 当前能力

- litellm 统一接入多模型
- 已实现 `read` / `write` / `edit` / `bash` / `grep` / `find` / `ls` 七个基础工具
- Telegram 流式输出、HTML 富文本渲染、消息编辑节流、单用户鉴权已接通
- 已支持 LLM 超时、整轮超时和 JSON 会话持久化
- 已支持本地 Markdown skill 的会话级启用
- 图片输入仍在后续阶段

## 快速开始

```bash
uv sync
cp .env.example .env
# 编辑 .env，填入 TELEGRAM_BOT_TOKEN、ALLOWED_USER_IDS 和至少一个模型 API Key

uv run ragtag-crew
# 或
uv run python -m ragtag_crew.main
```

可选：在仓库根目录创建 `skills/*.md`，再通过 `/skills` 和 `/skill use <name>` 启用。

## 目录结构

```text
ragtag_crew/
├── src/
│   └── ragtag_crew/
│       ├── main.py           # 入口
│       ├── config.py         # 配置加载
│       ├── agent.py          # 自建 agent loop
│       ├── llm.py            # litellm 封装
│       ├── telegram/
│       │   ├── bot.py        # Telegram 接入层
│       │   └── stream.py     # 流式输出与消息编辑
│       └── tools/
│           ├── __init__.py   # 工具注册与预设
│           ├── file_tools.py
│           ├── shell_tools.py
│           └── search_tools.py
├── archive/
│   └── pi-sdk-validation/    # 早期 Pi SDK 验证资料
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
- `archive/pi-sdk-validation/`：早期 Pi SDK 方向的验证记录，仅保留参考

## License

Apache-2.0
