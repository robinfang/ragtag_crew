# Agent 工具链调研报告：ripgrep / fd / uutils coreutils

> 调研日期：2026-04-04
> 目的：为 ragtag_crew 选择合适的底层命令行工具，替代当前 Python 原生实现和 Git Bash shell 执行

## 一、行业基准：OpenCode 和 Claude Code 怎么做

通过源码分析，两个标杆 AI coding agent 的工具链实现如下：

### 1.1 文件搜索

| 项目 | 底层工具 | 实现方式 |
|------|---------|---------|
| OpenCode | **ripgrep `--files`** 模式 | `Ripgrep.files({ cwd, glob })` |
| Claude Code | **ripgrep `--files`** 模式 | `ripgrep(['--files', '--glob', pattern, '--sort=modified'])` |

两者都**不用 find / fd**，全部用 rg 的 `--files` + `--glob` 做文件名搜索。

### 1.2 内容搜索

| 项目 | 底层工具 | 关键参数 |
|------|---------|---------|
| OpenCode | **ripgrep 直接调用** | `-nH --hidden --no-messages --field-match-separator=\|` |
| Claude Code | **ripgrep 直接调用** | `--hidden --max-columns=500` + 支持 `-B/A/C/type/multiline` |

### 1.3 ripgrep 部署策略

| 项目 | 策略 | rg 版本 |
|------|------|---------|
| OpenCode | 首次使用时自动下载到 `~/.opencode/bin/` | 14.1.1 |
| Claude Code | 三种模式：系统 rg / 内置 vendor / 嵌入 bun | 跟随 npm 包 |

两者都支持 Windows x64/arm64 预编译二进制。

### 1.4 其他内置工具

| 能力 | OpenCode | Claude Code |
|------|----------|-------------|
| 文件读取 | Node.js `createReadStream` + `readline` | Node.js `readFileInRange` |
| 文件写入 | `Filesystem.write()` + 自动格式化 + LSP 诊断 | `FileWriteTool` + LSP |
| 文件编辑 | **9 种替换策略**（精确/空白/锚定/缩进/转义等链式尝试） | oldString/newString 精确匹配 |
| Shell 执行 | Effect.js `ChildProcess` + tree-sitter AST 解析 | `child_process.spawn` + ShellProvider 接口 |
| ls/list | 有独立工具，基于 rg `--files` | 无独立工具（Bash ls / Read 目录路径） |
| 默认超时 | 2 分钟 | **30 分钟** |
| 沙箱 | 无 | 有（Linux bubblewrap） |
| 后台任务 | 无 | 有（run_in_background + 自动后台化） |

### 1.5 Windows 兼容性

两者都有完整的 Windows 支持：
- rg.exe 预编译二进制（x64 + arm64）
- PowerShell 自动检测和适配
- 路径规范化（POSIX ↔ Windows）
- 行尾处理（`\r\n` / `\n`）

## 二、ripgrep (rg)

### 2.1 概述

| 属性 | 值 |
|------|------|
| 仓库 | https://github.com/BurntSushi/ripgrep |
| 语言 | Rust |
| 最新版本 | 14.1.1 |
| Stars | 50k+ |
| 协议 | MIT / Unlicense |
| Windows 支持 | 预编译 x64/arm64 `.zip` |

### 2.2 核心能力

- **文件搜索**：`rg --files --glob "*.py"` — 列出匹配 glob 的文件，按修改时间排序
- **内容搜索**：`rg --line-number --hidden pattern dir/` — 正则搜索文件内容
- **类型过滤**：`rg --type py --type-add 'tex:*.tex'` — 按文件类型搜索
- **智能忽略**：默认遵守 `.gitignore`，可通过 `--no-ignore` 覆盖
- **速度**：基于 mmap + 并行目录遍历，远快于 GNU grep
- **Unicode**：完整 Unicode 支持

### 2.3 AI Agent 场景下的优势

1. **一行命令覆盖两个场景**（文件搜索 + 内容搜索）
2. **行业事实标准** — OpenCode / Claude Code 统一选择
3. **跨平台** — Windows/macOS/Linux 行为一致
4. **无 Git Bash 依赖** — 纯 Windows 原生二进制，不存在路径兼容问题
5. **可自动下载** — 预编译二进制，无需 Rust 编译环境

### 2.4 ragtag_crew 现状

当前 `search_tools.py` 的 grep 工具**已经有 rg 回退逻辑**：
```python
async def _grep_with_rg(pattern, root, include):
    command = ["rg", "--line-number", "--with-filename", pattern, str(root)]
    ...
except FileNotFoundError:
    return None  # 回退到 Python 实现
```

但系统上**没有安装 rg**，所以一直在走 Python 回退路径（逐行 `read_text` + `re.search`），性能差且功能受限。

### 2.5 find 工具

当前 find 工具用 Python `Path.rglob()`，受 `resolve_path()` 限制在 working_dir 内。这是 bot 搜不到 Dropbox 目录的根本原因之一。

## 三、fd (fd-find)

### 3.1 概述

| 属性 | 值 |
|------|------|
| 仓库 | https://github.com/sharkdp/fd |
| 语言 | Rust |
| 最新版本 | 10.4.2 (2026-03-10) |
| Stars | 42k+ |
| 协议 | MIT / Apache-2.0 |
| Windows 支持 | 预编译 x64/arm64 `.zip` |

### 3.2 核心能力

- **按名称搜索**：`fd "pattern"` — 正则匹配文件名
- **按扩展名搜索**：`fd -e tex` — 搜索 .tex 文件（rg 不支持）
- **按类型过滤**：`fd -t f`（文件）/ `-t d`（目录）/ `-t x`（可执行）
- **按深度限制**：`fd --max-depth 3`
- **智能忽略**：默认遵守 `.gitignore` 和 `.fdignore`
- **并行**：多线程并行遍历

### 3.3 与 ripgrep 的区别

| 维度 | ripgrep | fd |
|------|---------|------|
| 文件名搜索 | `rg --files --glob` | `fd "pattern"` |
| 内容搜索 | 核心功能 | 不支持 |
| 扩展名过滤 | `rg --glob "*.tex"` | `fd -e tex` |
| 文件类型过滤 | `rg --type py` | `fd -t d`（目录等） |
| 正则搜索 | 文件名 + 内容都支持 | 仅文件名 |
| 行号输出 | 支持 | 不适用 |
| 速度 | 极快（mmap） | 极快（并行遍历） |

### 3.4 在 Agent 场景的定位

**fd 是 rg 的补充，不是替代**：
- OpenCode 和 Claude Code **都不用 fd**，因为 rg `--files` 已经覆盖了文件名搜索
- fd 的独特价值在于 `-e`（按扩展名）和 `-t`（按类型），这些 rg 的 `--glob` 部分覆盖但不完全
- 如果只选一个，选 rg
- 如果要最完整覆盖，rg + fd 组合最佳

## 四、uutils coreutils

### 4.1 概述

| 属性 | 值 |
|------|------|
| 仓库 | https://github.com/uutils/coreutils |
| 语言 | Rust |
| 最新版本 | 0.7.0 (2026-03-08) |
| Stars | 23k+ |
| 协议 | MIT |
| Windows 支持 | 预编译 x64/arm64/i686 `.zip` + 静态 `.exe` |

GNU coreutils 的完整 Rust 重写，包含 100+ 工具：ls、cp、mv、rm、cat、head、tail、wc、sort、uniq、chmod、mkdir、stat 等。

### 4.2 GNU 兼容性

- GNU 测试套件通过率：629/665 (94.59%)，参考版本 GNU 9.10
- 持续向 GNU 上游贡献补丁，两个项目互利
- Ubuntu 正在将 uutils 作为默认 coreutils（"Oxidizing Ubuntu" 计划）

### 4.3 uutils findutils

| 属性 | 值 |
|------|------|
| 仓库 | https://github.com/uutils/findutils |
| 最新版本 | 0.8.0 (2025-04-06) |
| Stars | 568 |
| 包含工具 | `find`, `xargs`, `locate`, `updatedb` |

独立于 coreutils 的项目，目标替代 GNU findutils。与 fd 不同，uutils find 追求 100% GNU find 兼容。

### 4.4 在 Agent 场景的定位

**不建议直接使用 uutils coreutils 作为 Agent 底层工具**：

1. **粒度过粗** — coreutils 是系统级工具箱，Agent 不需要全部 100+ 工具
2. **安全性不足** — `rm -rf`、`chmod 777` 等危险命令都在里面
3. **rg + fd 已经足够** — Agent 场景需要的文件搜索/内容搜索已被 rg 完全覆盖
4. **Shell 执行层的正确做法** — 不是替换 shell 工具，而是减少对 shell 的依赖（见第五节）

**但 uutils 有一个间接价值**：如果 ragtag_crew 未来需要提供"类 Unix 环境兼容"（例如在 Windows 上运行需要 Unix 工具链的命令），uutils 可以作为工具提供者，而不是让 LLM 直接调用系统 shell。

### 4.5 uutils vs rg + fd 对比

| 维度 | uutils coreutils | rg + fd |
|------|-----------------|---------|
| 文件搜索 | find（GNU 兼容） | rg `--files` + fd |
| 内容搜索 | grep（GNU 兼容） | rg（更快，更智能） |
| 文件操作 | cp/mv/rm/cat/head/tail 等 | Python pathlib（可控） |
| 安全性 | 无限制 | 通过工具 schema 控制 |
| Windows 兼容 | 原生支持 | 原生支持 |
| 体积 | ~5MB（全部工具） | ~2MB（rg） + ~1.3MB（fd） |
| Agent 适用度 | 低（系统工具箱） | 高（专为搜索优化） |

## 五、架构分析：Shell vs 工具 API

### 5.1 当前 ragtag_crew 的架构

```
LLM → tool_call → Python 工具层
                  ├── read/write/edit (pathlib, 纯 Python)
                  ├── grep (Python 回退，rg 可选)
                  ├── find (Python rglob)
                  ├── ls (Python iterdir)
                  └── bash (asyncio.create_subprocess_shell → Git Bash)
```

**问题**：LLM 在 bash 工具中执行 `find | grep` 等搜索命令时，实际走的是 Git Bash，在 Windows 上路径兼容性差，大量轮次浪费在路径探索上。

### 5.2 两种架构路线

#### 路线 A：直接 Shell（当前做法 + 问题）

```
LLM → bash tool → subprocess shell → 系统命令
```

| 优点 | 缺点 |
|------|------|
| prompt 简单 | Windows 行为不一致 |
| LLM 不需要适配 | shell 注入风险 |
| 快速实现 | rm -rf 等危险命令 |
| | 权限不可控 |
| | 依赖外部环境 |

#### 路线 B：纯工具 API（OpenCode / Claude Code 的做法）

```
LLM → tool_call(schema) → Python 工具 → 底层二进制(rg/fd) / Python API
```

| 优点 | 缺点 |
|------|------|
| 跨平台一致 | 工具实现成本高 |
| 可控、可限权 | 需要覆盖 LLM 的各种需求 |
| 可审计 | prompt 可能需要更多引导 |
| 不依赖系统 shell | |

### 5.3 推荐方案：增强路线 B，保留 bash 作为逃生阀

```
LLM → tool_call(schema)
      ├── read / write / edit (pathlib, 保持不变)
      ├── grep → rg (优先) → Python 回退 (当前已有)
      ├── find → rg --files (新) → fd (可选补充)
      ├── ls (Python, 保持不变)
      ├── everything_search (Windows Everything, 已有)
      └── bash (保留，但 LLM 应优先使用上面专用工具)
```

**核心改动**：
1. **让 grep/find 工具优先走 rg** — 系统上安装 rg 后自动启用（当前代码已有回退框架）
2. **新增 rg 自动下载** — 仿 OpenCode，首次使用时下载 rg.exe 到 `~/.ragtag_crew/bin/`
3. **可选集成 fd** — 作为 rg 的补充，覆盖 `-e`（扩展名过滤）场景
4. **bash 工具保持不变** — 但 system prompt 引导 LLM 优先用专用搜索工具
5. **去掉 find/grep 工具的 resolve_path() 限制** — 搜索是只读操作，路径限制不必要

### 5.4 关于"虚拟 Shell 解释器"方案的分析

有建议提出实现一个"虚拟 Shell 解释器"——LLM 输出 shell 命令，但底层拦截解析转为 Python API 调用。

**不建议采用此方案**，原因：

1. **解析 shell 语法极其复杂** — 管道 `|`、重定向 `>`、子 shell `$()`、引号嵌套、反斜杠转义……完整解析等于重写一个 shell
2. **LLM 不需要这一层** — LLM 已经能理解 JSON tool schema，直接调用结构化 API 更可靠
3. **行业趋势是反方向** — OpenCode / Claude Code 都在强化结构化工具，不是模拟 shell
4. **维护成本极高** — 需要持续跟进各种 shell 语法边界情况
5. **Claude Code 用 tree-sitter 解析 bash** — 但目的是做安全审计（识别危险命令），不是拦截执行

**正确的做法**是让 LLM 直接使用结构化工具（JSON schema），而不是假装在用 shell。

### 5.5 关于"禁用 bash"的建议

完全禁用 bash 不可行——有些场景（git 操作、包管理、进程管理）只有 shell 能做。

**但可以做以下改进**：
- system prompt 明确引导：文件搜索用 grep/find 工具，不要在 bash 里跑 find/grep
- 可选：bash 工具的 cwd 改为 POSIX 路径风格，改善 Git Bash 兼容性
- 可选：对 bash 输出做危险命令检测（rm -rf /、chmod 777 等）

## 六、推荐行动方案

### 优先级 P0（必须做）

1. **安装 rg 并自动下载机制**
   - 从 GitHub Releases 下载 rg 14.1.1 Windows x64 zip
   - 解压到 `~/.ragtag_crew/bin/rg.exe`
   - 修改 `search_tools.py` 中 rg 的查找路径，优先 `~/.ragtag_crew/bin/`
   - 这样现有的 grep 工具立即受益，无需改 tool schema

2. **find 工具改用 rg --files**
   - `_find_files()` 改为优先调用 `rg --files --glob <pattern> <path>`
   - Python `rglob()` 作为回退
   - 性能提升 10-100x（rg 并行遍历 vs Python 单线程）

### 优先级 P1（建议做）

3. **去掉 grep/find 工具的路径限制**
   - 搜索是只读操作，`resolve_path()` 限制导致搜不到 working_dir 外的文件
   - 改为可配置：默认允许 working_dir 内，管理员可通过配置开放全局搜索

4. **新增 fd 作为可选外部工具**
   - 放在 `external/fd_search.py`（与 everything.py 同级）
   - 覆盖 rg 不擅长的场景：按扩展名搜索（`fd -e tex`）、按类型过滤（`fd -t d`）

### 优先级 P2（可选）

5. **bash 路径兼容性改善**
   - cwd 传 POSIX 风格路径改善 Git Bash 兼容性
   - 或者在 system prompt 中引导 LLM 优先使用专用搜索工具

6. **tool 执行日志**
   - agent.py 中 `_execute_tool()` 记录 tool 名称、参数摘要、耗时、结果长度

## 七、工具下载清单

| 工具 | 版本 | 下载地址 | 目标位置 |
|------|------|---------|---------|
| rg (ripgrep) | 14.1.1 | `ripgrep-14.1.1-x86_64-pc-windows-msvc.zip` | `~/.ragtag_crew/bin/rg.exe` |
| fd (fd-find) | 10.4.2 | `fd-v10.4.2-x86_64-pc-windows-msvc.zip` | `C:\Users\thefm\bin\fd.exe` |
| es (Everything) | 1.1.0.30 | `ES-1.1.0.30.x64.zip` | `C:\Users\thefm\bin\es.exe` (已安装) |

rg 下载 URL：
```
https://github.com/BurntSushi/ripgrep/releases/download/14.1.1/ripgrep-14.1.1-x86_64-pc-windows-msvc.zip
```

fd 下载 URL：
```
https://github.com/sharkdp/fd/releases/download/v10.4.2/fd-v10.4.2-x86_64-pc-windows-msvc.zip
```
