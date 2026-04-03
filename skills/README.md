# Local Skills

把本地 skill 放在这个目录下，每个 skill 一个 `*.md` 文件。

示例：

- `review.md`
- `refactor.md`

Telegram 命令：

- `/skills` 查看可用 skill 与当前启用状态
- `/skill use review` 启用某个 skill
- `/skill drop review` 停用某个 skill
- `/skill clear` 清空当前会话 skill

当前实现里，skill 的 Markdown 内容会作为额外 system prompt 注入到当前会话。
