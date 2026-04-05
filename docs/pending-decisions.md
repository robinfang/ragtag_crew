# 待决策事项

## Telegram 表格渲染

- **问题**：`html.py` 不支持 Markdown 表格，LLM 输出的表格在 Telegram 里显示为乱码纯文本
- **约束**：Telegram 不支持 HTML `<table>` 标签
- **涉及文件**：`src/ragtag_crew/telegram/html.py`
- **候选方案**：
  1. **代码块**：把表格包在 ` ``` ` 里当等宽文本发送，保留对齐，最简单稳定
  2. **结构化列表**：转成列表格式（Header: value），更易读但宽度不一
  3. **智能切换**：小表格用列表，大表格（列数多/行数多）自动降级为代码块
