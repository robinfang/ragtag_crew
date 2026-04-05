# Paper Search

多源并发学术论文检索，通过 paper-search OpenAPI tools 调用。

## 依赖

- paper-search 服务运行在 `http://127.0.0.1:19001`
- 如果 paper_health_check 返回错误，提示用户启动服务

## 可用 tools

| Tool | 用途 |
|------|------|
| `paper_health_check` | 检查服务是否在线 |
| `paper_list_sources` | 列出数据源及优先级 |
| `paper_search_create_job` | 创建搜索任务，返回 job_id |
| `paper_search_poll_job` | 轮询任务状态与结果 |

## 搜索流程

1. `paper_search_create_job`：传入 query、可选 sources/limit/year_from/year_to
2. `paper_search_poll_job`：传入 job_id，每 1-2 秒轮询一次
3. status 枚举：
   - `running`：继续轮询
   - `completed`：全部源完成，展示结果
   - `partial`：部分源失败，结果仍有效，注明失败源
   - `failed`：全部源失败，无可用结果

## 输出格式

编号列表，不要 dump 原始 JSON：

```
Found <total> papers  [sources: <sources_succeeded>]
1. <title> (<year>)
   Authors  : <前3位作者, et al. if more>
   Venue    : <venue or journal, or "—">
   DOI      : <doi or "—">
   Citations: <citation_count or "—">
   Sources  : <found_in_sources>

   <abstract 截取 ~200 字符>
```

最多展示前 20 条。

## 源选择

| 场景 | 推荐源 |
|------|--------|
| CS / AI / ML | 默认四源（openalex, semantic_scholar, dblp, arxiv） |
| 生物医学 | openalex, pubmed, semantic_scholar |
| 全学科综合 | 默认四源 + crossref |
| 仅预印本 | arxiv |

## 去重规则

服务端三级 canonical key 去重：
1. DOI 归一化匹配
2. arXiv/DBLP/PubMed 强标识符匹配
3. 标题+年份兜底

合并规则：abstract 取最长、citation_count 取最大、ID 互补填充、authors/fields 去重累加。

排序：有摘要优先 → 来源优先级降序 → 引用数降序 → 年份降序。

## publication_status 解读

- `published`：有正式发表记录
- `accepted`：有被接收线索
- `preprint`：预印本
- `null`：无法判断

不要因为 `10.48550/arXiv...` DOI 就认定为正式发表，要结合 venue/journal/status 综合判断。

## 与 paper-collector 协作

搜索后入库：

```
paper_search_create_job → paper_search_poll_job
  → 筛选 relevant papers
  → paper_collection_create
  → paper_collection_import（带 title/authors/year/doi/url/abstract）
  → 后续 resolve → enrich → review → export
```

## 错误处理

- 连接失败 → 提示用户启动 paper-search 服务
- 保存 job_id，网络中断可重试同一 job_id
- `sources_failed` 非空 → 仍返回结果，注明失败源
- `422` → 参数错误（空 query、未知源名、年份反转）
