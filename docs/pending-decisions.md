# 待决策事项

当前没有新的阻塞性待决策事项。

## 已完成决策归档

### Telegram 表格渲染

- **已决策**：采用代码块方案
- **原因**：实现最简单稳定，能保留列对齐，且与 Telegram 对 `<table>` 不支持的限制兼容
- **当前实现**：Markdown 风格表格会自动转成等宽代码块；fenced code block 内的示例表格文本不会被重写
- **涉及文件**：`src/ragtag_crew/telegram/html.py`
