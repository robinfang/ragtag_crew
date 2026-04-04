# AGENTS.md

OpenAI Codex 自动 PR review 会读取此文件。请按照以下指南审查代码变更。

## 项目概述

ragtag_crew 是单用户自托管 Telegram AI 编程助手。

- 语言：Python 3.12（上限 `<3.13`），包管理用 uv
- 框架：python-telegram-bot + litellm，无第三方 agent SDK
- 入口：`src/ragtag_crew/`，测试：`tests/`
- 运行：`uv run ragtag-crew` 或 `uv run python -m ragtag_crew.main`

## 代码风格

- 中文注释和 commit message
- 不加无意义注释；注释说明「为什么」而非「做什么」
- 类型注解覆盖函数签名，尽量用 builtin 类型（`str | None` 而非 `Optional[str]`）
- 异步优先：IO 操作用 `async/await`，避免阻塞事件循环
- 配置统一走 `pydantic-settings`，不硬编码

## 审查重点

请侧重以下方面，按严重程度降序：

1. **错误**：逻辑缺陷、异常处理遗漏、类型不匹配、竞态条件
2. **安全**：路径穿越、命令注入、未校验的外部输入、敏感信息泄露
3. **健壮性**：边界条件、超时处理、资源释放、并发安全
4. **可维护性**：过度抽象、重复代码、命名不清

## 不需要审查的

- 纯排版调整（空行、import 排序）
- 中文措辞优化
- docstring 格式

## 测试

- 运行测试：`uv run pytest tests/`
- 新功能或修复应伴随对应测试用例
