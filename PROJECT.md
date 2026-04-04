# Project Context

- `ragtag_crew` 是一个单用户、自部署的 Telegram coding agent。
- 当前唯一正式入口是 Telegram，不提前为多前端做复杂抽象。
- 实现目标优先级是：上下文清晰、行为可解释、代码简单可维护。
- 默认尽量保持上下文短，把长内容放到按需读取的层里，而不是每轮全部注入。
