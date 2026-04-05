# Project Decisions

这里放已经确认的项目级决策。

## 工具系统

- **路径沙箱**：写操作（write/edit/delete_file）限制在工作目录内；读操作（read/grep/find/ls）允许访问任意绝对路径。通过 `resolve_path()` 和 `resolve_read_path()` 分别实现。
- **文件删除保护**：LLM 通过 bash 执行 `rm`/`del`/`rmdir` 会自动删除生成文件，导致后续修改需重写。解决方案：bash 拦截删除命令 + 提供 `delete_file` 工具。delete_file 支持删除文件和空目录，非空目录拒绝。
- **grep 默认大小写不敏感**：`case_insensitive` 参数默认值改为 `True`。

## 日志规范

- **INFO**：所有改变状态的命令（/new, /model, /tools, /skill, /memory, /context, /browser, /ext, /mcp）
- **WARNING**：未授权访问 + 操作失败
- **DEBUG**：只读查询命令 + 消息入口（截断至 80 字符）
