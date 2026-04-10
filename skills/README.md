# Local Skills

这个目录只放仓库内本地 skill。每个 skill 对应一个 `*.md` 文件，文件名（不含扩展名）就是 skill 名称。

示例：

- `review.md`
- `refactor.md`

## 当前约定

- “部署”一个本地 skill，等价于在 `skills/` 下新建或更新一个 Markdown 文件
- 当前没有 `/skill install`、注册表或额外安装步骤
- `/skills` 会列出当前会话已启用 skill，以及 `skills/` 下可发现的本地 skill
- `/skill use <name>` 只负责把某个已存在的 skill 加入当前会话
- `/skill drop <name>` 和 `/skill clear` 只影响当前会话，不会删除文件

## Prompt 行为

- 启用 skill 后，system prompt 只注入 skill 的名称、摘要和文件路径
- skill 全文不会直接注入；模型需要完整说明时，应按需读取对应的 `skills/<name>.md`
- 摘要来自 Markdown 文件中第一条非空、非标题行，所以建议把这一行写成一句清晰的用途说明

## 推荐写法

- 文件开头先写标题，再用一行短摘要说明 skill 用途
- 后续正文再写触发条件、操作步骤、边界和注意事项
- 尽量让 skill 自描述，避免依赖仓库外的隐含约定

## Telegram 命令

- `/skills` 查看可用 skill 与当前启用状态
- `/skill use review` 启用某个 skill
- `/skill drop review` 停用某个 skill
- `/skill clear` 清空当前会话 skill
