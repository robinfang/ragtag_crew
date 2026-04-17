# `ragtag_crew` Web 对话界面接入方案

## 1. 目标

在不推翻现有 `Telegram / 微信 / REPL` 三前端结构的前提下，为 `ragtag_crew` 增加一个可长期维护的 Web 对话界面。

目标不是引入一个“大而全 AI 平台”，而是新增一个 **单用户、自托管、可流式输出、可切 session 的第四前端**。

约束如下：

- 不使用 GPL / AGPL / 其他强 copyleft 方案
- 尽量复用现有 `AgentSession`、`session_store.py`、`session_routes.py`、`trace.py`
- 不提前把 Telegram / 微信 / Web 重构成统一大前端框架
- 第一版优先解决“聊天体验比 Telegram 更舒服”，而不是做文件中心、用户系统或复杂控制台

---

## 2. 当前项目是否适合接 Web

适合，原因有三：

1. **核心会话层已经前端无关**
- `AgentSession` 负责对话、工具调用、busy 状态、取消、planning、progress snapshot
- 这部分并不依赖 Telegram 或微信 API

2. **会话持久化和路由层已经抽出来了**
- `session_store.py` 已支持 `session_key`
- `session_routes.py` 已支持 `frontend peer -> current_session_key`
- Web 可以作为新的 `frontend=web`

3. **已有两种不同前端接入范式**
- Telegram：流式消息编辑型
- 微信：typing + 最终文本回复型
- 说明项目已经接受“前端能力不同，但共享同一 agent 内核”的做法

因此，Web 最合理的定位是：

> **新增一个薄前端，而不是重新定义产品架构。**

---

## 3. 开源候选与许可证筛选

以下候选只保留已核对到宽松协议或明确不推荐的方案。

### 3.1 推荐候选

| 方案 | 许可证 | 适合程度 | 说明 |
|------|--------|---------|------|
| `assistant-ui` | MIT | 高 | React 聊天 UI 组件库，适合保留 Python 后端，只新增 Web 壳 |
| `Chainlit` | Apache-2.0 | 中高 | Python 原生聊天 UI，MVP 很快，但会让 Web 前端更像一套独立应用 |
| `Vercel ai-chatbot` | Apache-2.0 | 中 | 更像完整 Next.js 模板，适合参考或局部裁剪，不适合硬嫁接 |
| `Gradio` | Apache-2.0 | 中 | 适合快速验证，但产品感和会话控制面有限 |
| `Streamlit` | Apache-2.0 | 中 | 快速试验型可用，但长期做聊天前端不如专门的 chat UI |
| `LibreChat` | MIT | 低 | 协议可用，但工程太重，多用户/平台化能力远超当前需求 |

### 3.2 不推荐候选

| 方案 | 原因 |
|------|------|
| `LobeChat` | 当前为 `LobeHub Community License`，对衍生分发有限制，不适合“拿来改改” |
| `Open WebUI` | 当前许可证带品牌保留等附加条件，不属于干净宽松协议 |

---

## 4. 推荐路线

### 4.1 长期推荐：`assistant-ui`

适合当前项目的原因：

- 它是 **UI 组件库**，不是强绑定后端的平台
- 可以保留 `ragtag_crew` 的 Python 后端、会话模型和工具链
- 对流式输出、消息列表、输入框、附件、线程 UI 都有成熟支持
- 后续如果要补“文件上传 / 产物回传 / trace 面板”，扩展空间大

代价：

- 需要接受 React / Node 前端栈
- 需要自己提供 Web API 或 SSE / WebSocket 协议

适用结论：

> 如果目标是做一个长期保留的 Web 前端，`assistant-ui` 是首选。

### 4.2 最快 MVP：`Chainlit`

适合当前项目的原因：

- Python 原生，集成速度快
- 对流式聊天 UI、上传、消息历史等已经有很多现成功能

问题：

- 它会把 Web 前端变成更像一个“Chainlit 应用”
- 不如 `assistant-ui` 那样容易保持当前后端边界
- 后续若要把 Web 做成与 Telegram / 微信并列的薄前端，回收成本更高

适用结论：

> 如果目标是尽快做一个能用的 Web 原型，可以先上 `Chainlit`。

### 4.3 不建议的方向

- 直接拿 `LibreChat` 这类完整 AI 平台大改
- 先搞统一多前端抽象，再接 Web
- 一上来就做多用户、鉴权、文件中心、模型管理后台

这些方向都会让项目偏离“单用户 coding agent”的主线。

---

## 5. 建议的产品定位

Web 前端第一版应定位为：

> **比 Telegram / 微信 更适合长回复、代码块、表格、历史切换和会话控制的本地聊天界面。**

它不是：

- 新的多用户 SaaS 面板
- IDE 替代品
- Open WebUI / LibreChat 式的一体化 AI 平台

第一版最值得做的能力：

1. 聊天输入框
2. 流式输出
3. 代码块与表格友好展示
4. busy 时进度显示
5. `New / Cancel / Plan` 控制
6. Session 列表与切换
7. 当前 session / 默认 session / override 状态展示

---

## 6. Web 前端架构建议

### 6.1 继续沿用“薄前端”模式

建议新增：

- `src/ragtag_crew/web/`
  - `app.py`：Web 路由入口
  - `service.py`：封装 Web 会话、session route、命令操作
  - `stream.py`：把 `AgentSession` 事件转成 SSE / WebSocket

复用现有：

- `AgentSession`
- `session_store.py`
- `session_routes.py`
- `trace.py`
- `tools` / `external` / `context_builder`

### 6.2 Session key 策略

建议分两步：

#### 第一步：MVP 先用单会话

- 默认 `session_key = web:default`
- peer 也可直接固定为 `web:default`

优点：

- 最简单
- 最贴合当前单用户定位
- 不需要先处理浏览器标签页隔离

#### 第二步：如果需要多标签页独立

- `peer_key = web:<client_id>`
- `default_session_key = web:<client_id>`

再继续复用 `session_routes.py` 做 override。

### 6.3 流式协议建议

第一版优先：

- **SSE（Server-Sent Events）**

原因：

- 当前需求主要是服务端单向流式输出
- 比 WebSocket 更轻、更容易调试
- 与 `AgentSession` 的事件流模型天然匹配

后续若需要：

- 真正双向实时交互
- 更复杂工具状态推送
- 断线恢复

再评估 WebSocket。

### 6.4 事件关联模型

第一版不建议采用“`POST /api/chat/send` 发消息，再临时连一次 `GET /api/chat/stream`”这种弱关联方案，因为它很容易出现以下问题：

- 首批 token 在前端订阅前已经丢失
- 多次发送时不同轮次输出串流
- 单会话下多标签页难以区分是谁触发的本轮执行

更稳妥的 MVP 设计是：

1. 页面加载后先建立一个**长连接 SSE**，按 session 维度订阅事件。
2. `POST /api/chat/send` 只负责创建本次执行请求，并立即返回 `request_id`。
3. 后续所有流式事件都带上 `request_id`、`session_key`、`sequence`、`event_type`。
4. 前端只渲染自己刚刚提交的 `request_id`，旧请求或其他标签页的事件可以忽略或只做只读展示。

建议事件字段至少包含：

- `request_id`
- `session_key`
- `event_type`
- `sequence`
- `timestamp`
- `payload`

如果后续需要断线恢复，可再利用 SSE 的 `Last-Event-ID` 或服务端短期 ring buffer 做补发；这不必放进第一版，但 `sequence` / `event_id` 字段最好一开始就预留。

---

## 7. 第一版功能范围

建议严格限制在下面这些功能：

### 7.1 聊天主链路

- 发送文本消息
- 流式显示回复
- 展示工具执行中状态
- 展示最终回复

### 7.2 会话控制

- 查看当前 session
- 列出最近 session
- 切换 session
- reset 到默认 session
- 清空当前 session

### 7.3 运行控制

- cancel 当前任务
- plan on/off
- 展示 busy 进度快照

### 7.4 暂不做

- 文件上传 / 下载
- 多用户登录系统
- 权限模型
- 浏览器能力控制台
- MCP / OpenAPI 后台管理页
- trace 查询 UI

---

## 8. 后端接口建议

若选择 `assistant-ui` 或自建 Web 页面，建议后端提供最小 API：

### 8.1 页面与状态

- `GET /`
  - 返回主页面

- `GET /api/state`
  - 返回当前 route、当前 session、busy 状态、planning 状态

### 8.2 Session 操作

- `GET /api/sessions`
  - 返回 `list_sessions()` 结果

- `POST /api/session/use`
  - 参数：`session_key` 或 `index`

- `POST /api/session/reset`

- `POST /api/session/new`

### 8.3 运行控制

- `POST /api/session/cancel`

- `POST /api/session/plan`
  - 参数：`on/off`

### 8.4 聊天与流

- `POST /api/chat/send`
  - 参数：`text`、可选 `client_request_id`
  - 返回：`request_id`、`session_key`、`accepted=true`

- `GET /api/chat/stream`
  - 参数：`session_key`
  - 建立长连接 SSE，持续输出带 `request_id` 的 agent events

建议 Web 侧事件采用统一 envelope，例如：

```json
{
  "request_id": "req_xxx",
  "session_key": "web:default",
  "event_type": "message_update",
  "sequence": 12,
  "timestamp": 1713333333.0,
  "payload": {}
}
```

---

## 9. 与现有 Telegram / 微信的关系

建议 Web 与 Telegram / 微信 **并存**，而不是替代。

长期启动模式当然可以扩展为：

- Telegram only
- Weixin only
- Web only
- Telegram + Web
- Weixin + Web
- Telegram + Weixin + Web

但从当前实现出发，MVP 不应直接承诺“同进程下三前端同时稳定运行”。当前 `main.py` 只实现了：

- Telegram 单独运行
- 微信单独运行
- Telegram 主链路 + 微信后台线程

还没有一个通用的 frontend supervisor 去统一编排 Web server、Telegram polling 和微信前端。

### 9.1 启动编排建议

第一阶段更务实的选择有两种：

1. **Web only**
   - 先把 Web 当成单独入口跑通。
   - 这是改动最小、风险最低的方案。

2. **Web 与 Telegram / 微信 分进程运行**
   - 共享同一工作目录、session 存储和 trace 文件。
   - 前端层并存，但不强求第一版就在同一进程里统一调度。

只有在 Web 前端被确认要长期保留后，再考虑第三阶段：

- 在 `main.py` 里补一个最小 frontend supervisor
- 明确谁占主线程、谁在后台线程或独立事件循环里运行
- 统一启动/关闭、健康检查和日志语义

关键原则：

- Web 只是新增一个 frontend，不是重新定义 agent 内核
- 不要求 Telegram / 微信 / Web 使用同一种展示协议
- 共享的是 session、route、trace 和 agent 内核

---

## 10. 安全边界建议

由于当前 agent 能调用本地工具、bash、浏览器、外部能力，所以 Web 不能默认按“公网服务”思路开放。

第一版建议：

1. 默认只监听 `127.0.0.1`
2. 默认不开公网访问
3. 如允许远程访问，至少加简单 token
4. attached browser 相关风险边界保持与当前实现一致

建议新增的最小配置：

- `WEB_ENABLED=false`
- `WEB_HOST=127.0.0.1`
- `WEB_PORT=7860`
- `WEB_SESSION_KEY=web:default`
- `WEB_AUTH_TOKEN=`

不建议第一版就做：

- OAuth
- 用户系统
- 多角色权限
- 数据库存储用户资料

---

## 11. 两条可执行实施路线

### 11.1 路线 A：`assistant-ui` 正式方案（推荐）

#### 目标

- 做一个长期保留的 Web 前端
- 保持 Python 后端与现有前端边界清晰

#### 实施步骤

1. 新增 `web/` 后端入口，先跑通 session 级 SSE 长连接
2. 让 `POST /api/chat/send` 返回 `request_id`，并定义统一事件 envelope
3. 补 `web:default` 单会话
4. 接 `new/cancel/plan`
5. 接 `sessions/session use/reset`
6. 前端接入 `assistant-ui` 基础消息列表与输入框
7. 再补 sidebar、tool step、代码块优化

#### 优点

- 架构最干净
- 可长期维护
- 最符合“Web 是第四前端”的定位

#### 缺点

- 首次接入成本高于 Chainlit
- 需要引入前端构建链

### 11.2 路线 B：`Chainlit` MVP 方案（快速试验）

#### 目标

- 尽快验证 Web 体验与核心交互

#### 实施步骤

1. 用 Chainlit 跑通基本聊天界面
2. 接 `AgentSession`
3. 最小复用 `session_store.py` 与 `session_routes.py`
4. 验证是否满足真实使用场景
5. 若后续需要长期保留，再评估是否切回自有 Web frontend

#### 优点

- 开发速度快
- Python 团队心智负担小

#### 缺点

- 与当前前端层边界不够一致
- 后续正式产品化时可能需要二次迁移

---

## 12. 推荐决策

### 如果目标是“长期保留的 Web 前端”

推荐：

- **`assistant-ui` + 自有 Python Web API**

### 如果目标是“尽快试一个能用的 Web 原型”

推荐：

- **`Chainlit`**

### 当前阶段我更推荐的实际策略

> **先写一版基于 `assistant-ui` 的接入方案并实现最小后端 API，优先跑通 session 级 SSE、request_id 关联和 Web-only 启动。**

原因：

- 这条路线和当前项目结构最一致
- 未来如果补文件上传、trace、artifact、工具侧栏，扩展路径更稳
- 不会把 Web 做成和 Telegram / 微信平行但风格完全不同的一套应用

---

## 13. 建议的下一步

1. 明确选择路线：`assistant-ui` 或 `Chainlit`
2. 如果选 `assistant-ui`：先补一份后端 API、事件 envelope 与 SSE 详细设计
3. 如果选 `Chainlit`：先做最小原型，不急着追求与现有前端完全统一
4. 第一版都只做单用户、本机访问、文本聊天与 session 控制
5. Web 与 Telegram / 微信 同进程并存，放到 Web-only 跑通之后再评估

---

## 14. 最终结论

`ragtag_crew` 现在的代码结构已经具备接入 Web 前端的良好前提。

最合理的做法不是引入一个大平台，而是：

- 把 Web 当成第四前端
- 继续复用 `AgentSession + session_store + session_routes + trace`
- 用宽松协议的现成 UI 方案加速，而不是把产品逻辑迁移到第三方平台里

在当前许可证筛选结果下：

- **长期首选：`assistant-ui`（MIT）**
- **MVP 首选：`Chainlit`（Apache-2.0）**
- **不建议：`LobeChat`、`Open WebUI`**
