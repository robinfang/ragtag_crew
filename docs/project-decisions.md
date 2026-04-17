# Project Decisions

这里放已经确认的项目级决策。

## 工具系统

- **路径沙箱**：写操作（write/edit/delete_file）限制在工作目录内；读操作（read/grep/find/ls）允许访问任意绝对路径。通过 `resolve_path()` 和 `resolve_read_path()` 分别实现。
- **文件删除保护**：LLM 通过 bash 执行 `rm`/`del`/`rmdir` 会自动删除生成文件，导致后续修改需重写。解决方案：bash 拦截删除命令 + 提供 `delete_file` 工具。delete_file 支持删除文件和空目录，非空目录拒绝。
- **grep 默认大小写不敏感**：`case_insensitive` 参数默认值改为 `True`。
- **working_dir 级 workspace 管理**：每个 `working_dir` 下维护独立的 `.ragtag_crew/workspaces/`；普通 `find/grep/ls` 默认隐藏该目录，脚本和临时产物通过 `create_workspace` / `list_workspaces` / `write_script` 等专用工具管理与复用。
- **新脚本根目录保护**：新建脚本若目标位于 `working_dir` 根目录，无论传入形式是 `foo.py`、`./foo.py` 还是工作目录内绝对路径，都视为歧义目标并拒绝直接 `write`。要么明确写入项目子目录，要么改用 `write_script` 写入 managed script workspace。

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
- **执行原则注入层**：在 `Planning Protocol` 与 `Project Context` 之间注入 `Execution Principles`，明确要求先确认歧义、优先最小改动、只改相关代码，并以可验证结果收尾

## 会话路由

- **统一 session key**：会话持久化统一使用 `session_key`；Telegram 默认 key 为聊天整数 ID，微信默认 key 为 `weixin:<user_id>`，REPL 为 `0`
- **当前聊天窗口绑定**：通过 `session_routes.py` 维护 `frontend peer -> current_session_key` 的 override 路由；默认路由不落盘，只持久化 override
- **跨端共享策略**：Telegram、微信、REPL 默认不自动互通；只有显式执行 `/session use ...` 时才共享同一个 session
- **`/new` 语义**：只清空当前绑定的 session，不自动 reset 路由；如果希望恢复默认绑定，需要执行 `/session reset`
- **忙碌保护**：当前绑定 session 忙碌时，拒绝执行 `/session use` 与 `/session reset`
- **序号切换**：`/session use <index>` 以 `/sessions` 当前展示顺序解析；若参数能精确匹配完整 `session_key`，则优先按完整 key 使用

## 开发模式

- **`--dev`**：自动设置 `dev_mode=True` + `log_level=DEBUG`，启动 watchfiles 监听 `src/ragtag_crew/**/*.py`，变更时 `os.execv` 重启。显式 `--log-level` 优先于 `--dev`
- **`--repl`**：不连 Telegram/微信，终端直接对话 AgentSession，支持 /new /model /cancel /tools /plan /skills /skill /quit；具备实时流式输出（ReplStreamer）、执行轨迹收集（TraceCollector）和 JSON 会话持久化（session_key=0）
- **不放宽超时**：开发模式保持与生产一致的超时配置，避免行为差异
