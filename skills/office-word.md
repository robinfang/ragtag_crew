# Office Word

基于 `word-power-tools` 的本地 Word 办公处理技能，适用于文档提取、格式转换、排版检查、统一格式、合并拆分和模板骨架生成。

## 依赖

- 工具根目录：`Z:\agentworkspace\word_related_skill\word-power-tools`
- 主入口：`Z:\agentworkspace\word_related_skill\word-power-tools\scripts\word_tool.py`
- 依赖文件：`Z:\agentworkspace\word_related_skill\word-power-tools\requirements.txt`
- 详细文档：
  - `Z:\agentworkspace\word_related_skill\word-power-tools\docs\USAGE.md`
  - `Z:\agentworkspace\word_related_skill\word-power-tools\docs\TROUBLESHOOTING.md`
  - `Z:\agentworkspace\word_related_skill\word-power-tools\docs\SECURITY.md`

## 适用场景

- 用户要查看 `.docx` / `.doc` / `.rtf` 的文档概览、标题结构、样式信息
- 用户要从 Word 提取正文、表格、图片
- 用户要把 Word 转成 `docx` / `pdf` / `md` / `html` / `txt`
- 用户要做批量替换、写入元数据、插入目录
- 用户要做格式检查、统一排版、论文或报告格式修正
- 用户要合并文档、按标题拆分文档、按模板骨架生成新文档

## 使用约定

- 优先调用 `word_tool.py`，不要临时编写 `python-docx` 脚本或一次性处理代码。
- 当前 `ragtag_crew` 的 `bash` 工具固定在 agent `working_dir` 执行，不要假设可以先 `cd` 到 `word-power-tools` 目录；直接调用绝对路径脚本。
- 所有会改写文档的操作都输出到新文件，不覆盖原文件。
- 排版类任务遵循“先 `lint`，后 `format`”。先给出问题报告，再决定是否应用修复。
- 处理 `.doc` / `.rtf` 时，优先先转换为 `.docx`，再做提取、替换、排版等后续操作。
- 如果任务涉及不受信任文档、外部来历不明附件或批量处理，优先在受控目录中操作，并保留原始输入文件。
- 如果 `doctor` 提示缺少 `pandoc` 或 `soffice`，要明确告知用户对应功能会降级或不可用。

## 推荐工作流

1. 先确认输入文件路径、目标输出格式和是否允许修改原件副本。
2. 首次使用或环境可疑时，先运行 `doctor`。
3. 需要先理解文档结构时，先跑 `info` 或 `outline`，必要时加 `--list-styles`。
4. 提取类任务直接使用 `extract-text`、`extract-tables`、`extract-images`。
5. 转换类任务优先使用 `convert`；对旧格式文档先转干净的 `.docx`。
6. 排版类任务先 `lint` 生成报告，再根据报告决定是否执行 `format`。
7. 合并、拆分、目录、复杂模板生成完成后，提醒用户做人工复核，尤其关注页眉页脚、分页、目录域和复杂样式。

## 常用命令

建议优先使用 `python`；如果本机没有 `python` 命令，再考虑 `py -3`。

```text
python "Z:\agentworkspace\word_related_skill\word-power-tools\scripts\word_tool.py" doctor

python "Z:\agentworkspace\word_related_skill\word-power-tools\scripts\word_tool.py" info "<input.docx>"
python "Z:\agentworkspace\word_related_skill\word-power-tools\scripts\word_tool.py" info "<input.docx>" --list-styles
python "Z:\agentworkspace\word_related_skill\word-power-tools\scripts\word_tool.py" outline "<input.docx>"

python "Z:\agentworkspace\word_related_skill\word-power-tools\scripts\word_tool.py" extract-text "<input.docx>" -o "<out.txt>"
python "Z:\agentworkspace\word_related_skill\word-power-tools\scripts\word_tool.py" extract-tables "<input.docx>" -o "<tables.xlsx>" --format xlsx
python "Z:\agentworkspace\word_related_skill\word-power-tools\scripts\word_tool.py" extract-images "<input.docx>" -o "<images_dir>"

python "Z:\agentworkspace\word_related_skill\word-power-tools\scripts\word_tool.py" convert "<input.doc>" --to docx -o "<clean.docx>"
python "Z:\agentworkspace\word_related_skill\word-power-tools\scripts\word_tool.py" convert "<input.docx>" --to pdf -o "<output.pdf>"
python "Z:\agentworkspace\word_related_skill\word-power-tools\scripts\word_tool.py" convert "<input.docx>" --to md -o "<output.md>"

python "Z:\agentworkspace\word_related_skill\word-power-tools\scripts\word_tool.py" replace "<input.docx>" --find "<old>" --replace "<new>" -o "<replaced.docx>"
python "Z:\agentworkspace\word_related_skill\word-power-tools\scripts\word_tool.py" lint "<input.docx>" --config "<format.yaml>" -o "<report.md>"
python "Z:\agentworkspace\word_related_skill\word-power-tools\scripts\word_tool.py" format "<input.docx>" --config "<format.yaml>" -o "<fixed.docx>"

python "Z:\agentworkspace\word_related_skill\word-power-tools\scripts\word_tool.py" merge "<a.docx>" "<b.docx>" -o "<merged.docx>"
python "Z:\agentworkspace\word_related_skill\word-power-tools\scripts\word_tool.py" split "<input.docx>" --by heading1 -o "<split_dir>"
python "Z:\agentworkspace\word_related_skill\word-power-tools\scripts\word_tool.py" new --template "<template.yaml>" -o "<new.docx>"
```

## 输出要求

- 不要只贴命令；要说明做了什么、输出产物在哪里、哪些限制仍然存在。
- 如果生成了新文件，回复中明确给出输出路径。
- 如果执行了 `lint`，优先总结主要问题，再决定是否建议继续 `format`。
- 如果转换或排版能力因环境缺失而降级，要明确指出缺的是 `pandoc`、`soffice` 还是 Python 依赖。

## 安装与故障处理

- 先检查：

```text
python "Z:\agentworkspace\word_related_skill\word-power-tools\scripts\word_tool.py" doctor
```

- 如果缺少 Python 包，可提示用户安装：

```text
python -m pip install -r "Z:\agentworkspace\word_related_skill\word-power-tools\requirements.txt"
```

- 如果 `soffice` 不在 PATH，`doc/docx/rtf -> pdf/docx` 转换可能失败；Windows 常见位置是 `C:\Program Files\LibreOffice\program\soffice.exe`。
- 如果 `pandoc` 不在 PATH，`docx -> md/html` 转换可能失败。
- 如果替换后局部格式丢失，说明文档存在跨 run 匹配问题；可考虑改用更保守的替换方式，或提醒用户人工复核。

## 风险边界

- 这不是完整的 Word 引擎，不能保证复杂 Word 特性 100% 等价。
- 对以下内容要保守表述：Track Changes、复杂公式对象、嵌入对象、复杂分节、页眉页脚、分页逻辑、需要 Word UI 更新的域。
- `toc` 只会插入目录字段，真正页码通常仍需在 Word 或兼容编辑器中更新域。
- `merge`、`split`、`format` 更适合结构化处理和批量修正，不应被表述为最终交付质量保证。
- 如果用户要求“完整复制模板格式”或“完全保真复刻”，要先明确说明此工具不应做出该承诺。

## 何时读取上游文档

- 需要查看某个子命令的完整参数时，读取 `docs\USAGE.md`。
- 需要判断环境缺失、字体异常、替换后格式变化、目录更新等问题时，读取 `docs\TROUBLESHOOTING.md`。
- 需要评估不受信任文档、转换链和隔离处理策略时，读取 `docs\SECURITY.md`。
