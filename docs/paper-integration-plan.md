# Paper Search & Paper Collector 集成方案

将 paper-search 和 paper-collector 两个服务集成到 ragtag_crew，采用 **OpenAPI tools + 项目级 skill** 方案。

## 服务状态（已验证）

- **paper-search** `http://127.0.0.1:19001`，版本 0.1.0，状态 ok
- **paper-collector** `http://127.0.0.1:10001`，版本 0.1.0，状态 ok

两个 provider 在 `openapi_tools.local.json` 中默认 `enabled: false`，依赖本地服务启动后手动启用。

---

## 技术前置条件：路径参数替换

~~已解决~~：`openapi_provider.py:126-132` 已实现路径参数替换。按 key 长度降序遍历，防止短 key 误匹配长 key 前缀；路径值经 `urllib.parse.quote` 编码。

---

## paper-search API（已验证）

base_url: `http://127.0.0.1:19001`

| 工具名 | 方法 | 路径 | 说明 |
|--------|------|------|------|
| `paper_health_check` | GET | `/` | 健康检查，返回 `{service, version, status}` |
| `paper_list_sources` | GET | `/api/sources` | 列出可用数据源及优先级 |
| `paper_search_create_job` | POST | `/api/search/jobs` | 创建异步搜索任务，返回 `job_id` |
| `paper_search_poll_job` | GET | `/api/search/jobs/$job_id` | 轮询任务状态与结果 |

> paper-search 还有同步 `POST /api/search` 和 `GET /api/search/jobs/$job_id/result`，但 skill 约定走 job 异步接口。

### create_job 请求体（SearchRequestBody）

| 字段 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| `query` | string | 是 | 搜索关键词 |
| `sources` | string[] | 否 | 数据源列表，不填用默认四源 |
| `limit` | integer | 否 | 最大结果数，默认 20，最大 200 |
| `year_from` | integer | 否 | 起始年份（含） |
| `year_to` | integer | 否 | 截止年份（含） |

### job 轮询约定

轮询间隔 1-2s，`status` 值：
- `running`：继续轮询
- `completed`：全部源完成，读取 `items`
- `partial`：部分源失败，`items` 仍有效，输出时注明 `sources_failed`
- `failed`：全部源失败

poll_job 响应为扁平结构（非 wrapper），直接包含 `items` 数组：

| 字段 | 类型 | 说明 |
|------|------|------|
| `job_id` | string | 任务 ID |
| `status` | string | 任务状态（见上） |
| `query` | string | 原始查询 |
| `sources_queried` | string[] | 参与查询的数据源 |
| `sources_succeeded` | string[] | 成功的数据源 |
| `sources_failed` | string[] | 失败的数据源 |
| `total` | integer | 去重后结果数 |
| `items` | object[] | 搜索结果列表，每项含 `title`/`authors`/`year`/`doi`/`abstract`/`venue`/`citation_count`/`fields_of_study`/`primary_source`/`found_in_sources` 等 |
| `errors` | object | 按源名索引的错误信息 |

### 可用数据源（已验证）

| 源名 | 优先级 | 默认启用 | 适用场景 |
|------|:------:|:--------:|---------|
| openalex | 100 | 是 | 全学科综合 |
| semantic_scholar | 90 | 是 | AI 增强，提供 TLDR |
| dblp | 85 | 是 | 计算机科学专项 |
| arxiv | 80 | 是 | 预印本 |
| crossref | 70 | 否 | 全球 DOI 元数据 |
| pubmed | 65 | 否 | 生物医学 |

---

## paper-collector API（已验证）

base_url: `http://127.0.0.1:10001`

所有 API 端点在 `/api/` 前缀下，`/collections/` 无前缀的路径是 Web UI 表单接口，不用。

### 集合管理

| 工具名 | 方法 | 路径 | 说明 |
|--------|------|------|------|
| `paper_collection_list` | GET | `/api/collections` | 列出所有集合 |
| `paper_collection_create` | POST | `/api/collections` | 创建集合 |
| `paper_collection_get` | GET | `/api/collections/$collection_id` | 获取集合详情 |

create 请求体（CollectionCreateRequest）：`name`（必填）、`description`（可选）

返回（CollectionCreateResponse）：`collection_id`（整数）、`slug`、`name`

### 导入与发现

| 工具名 | 方法 | 路径 | 说明 |
|--------|------|------|------|
| `paper_collection_import` | POST | `/api/collections/$collection_id/import` | 导入论文条目列表 |
| `paper_collection_discover` | POST | `/api/collections/$collection_id/discover` | 按主题检索并写入集合 |

import 请求体（ImportItemsRequest）：`items: [CollectionItemInput]`

CollectionItemInput 字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| `title` | string | 是 | 论文标题 |
| `authors` | string[] | 否 | 作者列表 |
| `year` | integer | 否 | 年份 |
| `doi` | string | 否 | DOI |
| `url` | string | 否 | 链接 |
| `abstract` | string | 否 | 摘要 |
| `source` | string | 否 | 来源标注 |
| `notes` | string | 否 | 备注 |

discover 请求体（DiscoverySearchRequest）：`query`（必填）、`sources`、`limit`、`year_from`、`year_to`

discover 响应（同步，wrapper 结构）：`{"job": {...}, "result": {...}}`

result 字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `job_id` | integer | 关联的 job ID |
| `query` | string | 原始查询 |
| `sources` | string[] | 使用的数据源 |
| `discovered_count` | integer | 发现的论文数 |
| `imported_count` | integer | 导入到集合的论文数（去重后） |
| `skipped_existing` | integer | 因已存在而跳过的数量 |
| `items` | object[] | 导入的论文列表，每项比 CollectionItemInput 多出 `venue`/`journal`/`arxiv_id`/`citation_count`/`fields_of_study`/`publication_status`/`tldr`/`source_names` 等 |

### 工作流操作

> 异步性说明：`resolve`、`import`、`discover` 均为**同步**操作，HTTP 响应在操作完成/失败后返回，无需轮询。仅 `enrich` 为**异步**操作，返回 `status: "running"` 后需主动轮询 `enrich/progress` 或 `paper_job_get`。
>
> wrapper 结构说明：`resolve`/`import`/`discover`/`enrich` 均返回 `{"job": JobResponse, "result": {...}}` 包装结构。`GET /api/jobs/$job_id` 返回扁平 JobResponse（无 wrapper）。`dedup` 不走 job 系统，直接返回结果对象。
>
> 响应风格差异：paper-search 的 poll_job 响应为扁平结构（items 直接在响应体内）；paper-collector 的 action 端点统一用 wrapper 结构。

| 工具名 | 方法 | 路径 | 同步/异步 | 说明 |
|--------|------|------|:---------:|------|
| `paper_collection_resolve` | POST | `/api/collections/$collection_id/resolve` | 同步 | 解析版本关系 |
| `paper_collection_enrich` | POST | `/api/collections/$collection_id/enrich` | 异步 | 补充元数据（CCF、中科院分区、IF 等） |
| `paper_collection_enrich_progress` | GET | `/api/collections/$collection_id/enrich/progress` | — | 查询 enrich 进度（**非** `/enrich/{job_id}`） |
| `paper_collection_enrich_cancel` | POST | `/api/collections/$collection_id/enrich/cancel` | — | 取消 enrich |
| `paper_collection_dedup` | POST | `/api/collections/$collection_id/dedup` | 同步 | 去重，不走 job 系统，返回 `{"total_duplicates", "removed_items"}` |

enrich 异步响应的 result 字段：`resumed`（bool，是否续跑）、`processed`（已处理数）、`total`（总数）、`skipped`（跳过数），可用于计算进度百分比。

### Job 系统（统一任务追踪）

| 工具名 | 方法 | 路径 | 说明 |
|--------|------|------|------|
| `paper_job_list` | GET | `/api/jobs` | 列出所有任务 |
| `paper_job_get` | GET | `/api/jobs/$job_id` | 获取任务状态 |
| `paper_job_logs` | GET | `/api/jobs/$job_id/logs` | 获取任务日志 |

JobResponse 字段：`id`、`job_type`、`target_type`、`target_id`、`status`、`error_message`、`started_at`、`completed_at`

job status 枚举：`running`（进行中）、`success`（成功）、`failed`（失败，查 `error_message`）

### 浏览与查询

| 工具名 | 方法 | 路径 | 说明 |
|--------|------|------|------|
| `paper_collection_items` | GET | `/api/collections/$collection_id/items` | 分页浏览导入记录（DataTables 协议） |
| `paper_collection_items_all` | GET | `/api/collections/$collection_id/items/all` | 获取全部导入记录（无分页） |
| `paper_collection_works` | GET | `/api/collections/$collection_id/works` | 分页浏览论文主表（DataTables 协议） |
| `paper_collection_reviews` | GET | `/api/collections/$collection_id/reviews` | 查看待审核关系队列 |

works 支持的 query 筛选参数：`draw`、`start`、`length`、`search`、`order_column`、`order_dir`、`publication_verification_status`、`ccf_rank`、`cas_rank`、`priority_band`

> DataTables 协议的 `draw`/`start`/`length` 对 LLM 不友好，建议直接使用 `paper_collection_items_all` 而非分页接口。

### 关系审核

| 工具名 | 方法 | 路径 | 说明 |
|--------|------|------|------|
| `paper_relation_get` | GET | `/api/relations/$relation_id` | 获取关系详情 |
| `paper_relation_evidence` | GET | `/api/relations/$relation_id/evidence` | 获取关系证据列表 |
| `paper_relation_confirm` | POST | `/api/relations/$relation_id/confirm` | 确认关系 |
| `paper_relation_reject` | POST | `/api/relations/$relation_id/reject` | 拒绝关系 |

> confirm/reject 请求体为空 JSON `{}`，路径在 `/api/relations/` 下（不是 `/relations/`）

### 导出

| 工具名 | 方法 | 路径 | 说明 |
|--------|------|------|------|
| `paper_collection_export` | GET | `/api/collections/$collection_id/export` | 导出集合（query: `format=json`） |

### 补充端点（可选注册）

| 工具名 | 方法 | 路径 | 说明 |
|--------|------|------|------|
| `paper_work_get` | GET | `/api/works/$work_id` | 获取 work 详情（含版本和关系） |
| `paper_work_versions` | GET | `/api/works/$work_id/versions` | 列出 work 的所有版本 |
| `paper_version_get` | GET | `/api/versions/$version_id` | 获取版本详情 |
| `paper_collection_consistency` | GET | `/api/collections/consistency` | 检查集合一致性 |

---

## 推荐工作流

```
1. paper_search_create_job（搜索候选论文）
   → 轮询 paper_search_poll_job 直到 completed/partial
   → 筛选 relevant papers

2. paper_collection_create（建立集合）
3. paper_collection_import（导入筛选结果，尽量附 abstract）
   → 同步完成，直接读取响应中的 result.imported_count
4. paper_collection_resolve（解析版本关系）
   → 同步完成，直接读取响应中的 result.resolved / result.review_needed
5. paper_collection_enrich（补充 CCF/中科院/IF 元数据）
   → 异步操作，轮询 paper_collection_enrich_progress 或 paper_job_get
   → 等待 job.status = "success"（失败则 "failed"，查 error_message）
6. paper_collection_reviews（查看待审核关系）
   → paper_relation_evidence 查看证据
   → paper_relation_confirm / paper_relation_reject
7. paper_collection_export（导出 JSON）
   → 读取 display_works / priority_works / works
```

---

## 项目级 skill 文件

- `skills/paper-search.md` — paper-search 使用指南
- `skills/paper-collector.md` — paper-collector 使用指南

---

## 实现步骤

1. ~~修改 `openapi_provider.py`~~：~~在 `_build_url` 中增加路径参数替换~~ — **已完成**
2. ~~新建 `openapi_tools.local.json`~~：~~注册上述两个 provider，`enabled: false`~~ — **已完成**
3. ~~新建 `skills/paper-search.md`~~ — **已完成**
4. ~~新建 `skills/paper-collector.md`~~ — **已完成**

---

## 相关文件

- 全局 skill（保留）：`~/.agents/skills/paper-search/SKILL.md`、`~/.agents/skills/paper-collector-agent/SKILL.md`
- OpenAPI provider 实现：`src/ragtag_crew/external/openapi_provider.py`
- OpenAPI 配置示例：`openapi_tools.example.json`
- 配置项：`settings.openapi_tools_file`
