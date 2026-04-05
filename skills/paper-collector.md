# Paper Collector

论文集合管理与版本关联，通过 paper-collector OpenAPI tools 调用。

## 依赖

- paper-collector 服务运行在 `http://127.0.0.1:10001`
- 在 openapi_tools.local.json 中 enabled，需手动启用
- 如果 paper_collection_list 返回连接错误，提示用户启动服务

## 适用场景

- 为某个研究主题建立论文集合
- 导入已搜集的论文清单
- 判断预印本是否已正式发表
- 审核版本关系证据

## 可用 tools

### 集合管理

| Tool | 用途 |
|------|------|
| `paper_collection_list` | 列出所有集合 |
| `paper_collection_create` | 创建集合（name 必填） |
| `paper_collection_get` | 获取集合详情 |

### 导入与发现

| Tool | 用途 |
|------|------|
| `paper_collection_import` | 导入论文条目列表（同步） |
| `paper_collection_discover` | 按主题检索并写入集合（同步阻塞） |

### 工作流操作

| Tool | 用途 |
|------|------|
| `paper_collection_resolve` | 解析版本关系（**同步**，直接返回结果） |
| `paper_collection_enrich` | 补充 CCF/中科院/IF 元数据（**异步**，需轮询） |
| `paper_collection_enrich_progress` | 查询 enrich 进度 |
| `paper_collection_enrich_cancel` | 取消 enrich |
| `paper_collection_dedup` | 去重 |

### 浏览

| Tool | 用途 |
|------|------|
| `paper_collection_items` | 分页浏览导入记录（DataTables 协议） |
| `paper_collection_items_all` | 获取全部导入记录（**推荐，无分页**） |
| `paper_collection_works` | 分页浏览论文主表 |
| `paper_collection_reviews` | 查看待审核关系队列 |

### 关系审核

| Tool | 用途 |
|------|------|
| `paper_relation_get` | 获取关系详情 |
| `paper_relation_evidence` | 获取关系证据列表 |
| `paper_relation_confirm` | 确认关系 |
| `paper_relation_reject` | 拒绝关系 |

### 导出与 Job

| Tool | 用途 |
|------|------|
| `paper_collection_export` | 导出集合 JSON（display_works / priority_works / works） |
| `paper_job_list` | 列出所有 job |
| `paper_job_get` | 获取 job 状态（running / success / failed） |
| `paper_job_logs` | 获取 job 日志 |

### 补充

| Tool | 用途 |
|------|------|
| `paper_work_get` | work 详情（含版本和关系） |
| `paper_work_versions` | 列出 work 所有版本 |
| `paper_version_get` | 版本详情 |
| `paper_collection_consistency` | 检查集合一致性 |

## 推荐工作流

```
1. paper_collection_create（建立集合）
2. paper_collection_import 或 paper_collection_discover（导入论文）
   → import 是同步的，直接读取 result.imported_count
3. paper_collection_resolve（解析版本关系）
   → 同步完成，读取 result.resolved / result.review_needed
4. paper_collection_enrich（补充元数据）
   → 异步！轮询 paper_collection_enrich_progress 或 paper_job_get
   → 等待 status = "success"（失败则查 error_message）
5. paper_collection_reviews（查看待审核关系）
   → paper_relation_evidence 查看证据
   → paper_relation_confirm / paper_relation_reject
6. paper_collection_export（导出）
   → 优先读 display_works 和 priority_works
```

## import 请求示例

```json
{
  "collection_id": 1,
  "items": [
    {
      "title": "Paper Title",
      "authors": ["Author A", "Author B"],
      "year": 2024,
      "doi": "10.1234/example",
      "url": "https://arxiv.org/abs/2401.01234",
      "abstract": "Abstract text...",
      "source": "paper-search",
      "notes": "optional notes"
    }
  ]
}
```

尽量附带 abstract，有助于后续 resolve 和 enrich。

## 结果解读

- `items`：导入来源记录和追溯
- `works`：研究成果层面的归并结果（文献调研优先消费）
- `display_works`：保守浏览视图，区分待确认正式版与候选排名
- `priority_works`：当前最值得优先阅读的论文
- `preferred_version_id`：在 works[].versions 中定位当前最优版本
- `publication_verification_status = review_needed_published`：candidate_* 是待审核线索，非已确认事实

## 审核决策规则

### 可以自动确认

- evidence_summary 包含"DOI 一致"或"arXiv ID 一致"
- 同时存在正式 DOI 与可靠来源快照

### 建议人工复核

- evidence_summary 包含"年份冲突"
- 只有标题相似，无强标识符
- 单一来源，evidence 条数很少

### 正式发表的谨慎规则

- `10.48550/arXiv...` DOI 不等于正式发表
- 结合 venue、journal、version_type 和 relation evidence 综合判断
- 只有预印本来源 → 保留为"预印本/待追踪"状态

## 错误处理

- `404`：资源不存在，停止当前对象处理
- `400`：修正请求后重试
- enrich 失败：查 `paper_job_get` 的 error_message，可能需重试
- reviews 非空：流程未闭环，不应直接结束
