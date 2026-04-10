# Project Decisions

这里放已经确认的项目级决策。

## 工具系统

- **路径沙箱**：写操作（write/edit/delete_file）限制在工作目录内；读操作（read/grep/find/ls）允许访问任意绝对路径。通过 `resolve_path()` 和 `resolve_read_path()` 分别实现。
- **文件删除保护**：LLM 通过 bash 执行 `rm`/`del`/`rmdir` 会自动删除生成文件，导致后续修改需重写。解决方案：bash 拦截删除命令 + 提供 `delete_file` 工具。delete_file 支持删除文件和空目录，非空目录拒绝。
- **grep 默认大小写不敏感**：`case_insensitive` 参数默认值改为 `True`。

## 日志规范

- **INFO**：所有改变状态的命令（/new, /cancel, /model, /tools, /skill, /memory, /context, /browser, /ext, /mcp）
- **WARNING**：未授权访问 + 操作失败
- **DEBUG**：只读查询命令 + 消息入口（截断至 80 字符）

## Agent 控制

- **用户取消 vs 超时**：`UserAbortedError` 与 `TurnTimeoutError` 分开，Telegram 显示不同提示（「已取消」vs 错误信息）
- **abort 中断粒度**：`_run_loop` 每轮检查 + `_execute_tool` 执行前后检查 + `stream_chat` 每个 chunk 前后检查。消息历史保留残缺内容不清理
- **可用模型列表**：`config.py` 中 `available_models` 逗号分隔，`/model` 无参数时列出
- **`/cancel` 显式反馈**：用户发送 `/cancel` 后立即回复“已发送取消信号”，不只依赖异步 streamer 侧反馈
- **忙碌时进度查询**：会话忙碌时识别“进度/进展/好了没”等询问，返回当前 turn、工具执行数、最近响应预览

## Telegram 渲染

- **表格渲染方案**：采用代码块方案。Markdown 风格表格在发送前自动转换为等宽代码块，避免 Telegram 中错位
- **代码块保护**：fenced code block 内的 `| ... |` 示例文本不参与表格转换，避免破坏原始代码或文档内容

## 上下文系统

- **环境引导**：在 `Project Context` 与 `User Context` 之间注入 `Workspace Snapshot`，提供受控目录树和关键配置文件摘要
- **会话摘要阶段 A**：`session_summary` 保留关键工具参数、调用顺序，并在超限时优先保留更新近的压缩内容

## 开发模式

- **`--dev`**：自动设置 `dev_mode=True` + `log_level=DEBUG`，启动 watchfiles 监听 `src/ragtag_crew/**/*.py`，变更时 `os.execv` 重启。显式 `--log-level` 优先于 `--dev`
- **`--repl`**：不连 Telegram，终端直接对话 AgentSession，支持 /new /model /cancel /tools /plan /skills /skill /quit；具备实时流式输出（ReplStreamer）、执行轨迹收集（TraceCollector）和 JSON 会话持久化（chat_id=0）
- **不放宽超时**：开发模式保持与生产一致的超时配置，避免行为差异
