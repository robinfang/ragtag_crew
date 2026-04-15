# Zhidu

本地制度库检索与附件定位，通过 zhidu 本地 HTTP 服务调用。

## 依赖

- `zhidu` 项目位于 `Z:\agentworkspace\zhidu`
- 默认服务地址：`http://127.0.0.1:8964`
- 调用脚本：`scripts/zhidu_client.py`
- 如服务未启动，提示用户先在 `Z:\agentworkspace\zhidu` 中启动：

```powershell
uv run uvicorn app.main:app --host 0.0.0.0 --port 8964
```

## 适用场景

- 查询制度库中某个主题、关键词或规定
- 比较不同制度对同一问题的表述
- 追溯答案依据到具体制度正文和附件
- 打开本地附件或定位附件所在目录

## 可用命令

优先通过 `bash` 调用以下脚本，不要手写 HTTP 请求：

```powershell
uv run python scripts/zhidu_client.py health
uv run python scripts/zhidu_client.py search keyword --query "绩效" --json
uv run python scripts/zhidu_client.py search hybrid --query "中层 绩效" --category "人力资源" --json
uv run python scripts/zhidu_client.py search chunk --query "奖励性绩效工资" --json
uv run python scripts/zhidu_client.py doc 307772
uv run python scripts/zhidu_client.py attachment open 1
uv run python scripts/zhidu_client.py attachment reveal 1
```

## 推荐工作流

1. 先执行 `health`，确认本地服务在线。
2. 先用 `search keyword` 做制度名和显性关键词召回。
3. 关键词不够时，再用 `search hybrid` 或 `search chunk` 做语义补充。
4. 根据搜索结果中的 `docid` 调 `doc <docid>` 读取正文。
5. 只有在用户明确要求时，才执行 `attachment open` 或 `attachment reveal`。

## 检索策略

### `keyword`

- 用于制度标题、固定术语、机构名、岗位名、附件名等精确检索
- 适合先建立候选清单

### `hybrid`

- 用于“制度上怎么规定某件事”一类综合问题
- 适合在关键词召回基础上补充语义相关文档

### `chunk`

- 用于定位具体条款、条件、限制、例外、流程
- 返回正文命中片段时，优先引用这些片段再下结论

## 输出要求

- 不要直接 dump 原始 JSON 给用户
- 先给结论，再列依据文档
- 引用制度时至少给出：`制度名 + docid + 关键条款或正文片段`
- 如果证据不足，要明确说明“不足以确定”
- 如果只是制度层面的规则，不要推断实际发放金额或真实执行情况

## 附件操作边界

- `attachment open` 会直接打开本地文件
- `attachment reveal` 会打开资源管理器并选中文件
- 这两个动作都会触发本机 UI，只有在用户明确要求时才执行

## 常见问法

- “这个单位中层怎么发绩效？”
- “保密制度里对涉密载体怎么要求？”
- “找出和奖励性绩效工资有关的制度”
- “把这份制度的附件打开”
