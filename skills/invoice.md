# Invoice Extract

发票 PDF 和付款截图的结构化识别提取技能，通过 vision LLM（默认 GLM-4V-Flash）将发票/付款截图转换为结构化 JSON。

## 适用场景

- 用户发送发票 PDF（增值税专用发票、增值税普通发票、电子发票等），需要提取结构化信息
- 用户发送付款/转账截图（银行APP、支付宝、微信支付），需要提取交易信息
- 用户需要批量处理发票或付款凭证，并汇总金额、收款方等

## 依赖

- 主脚本：`scripts/invoice_extract.py`
- Python 依赖：`pymupdf`（PDF 转图片）、`litellm`（已内置）
- 环境变量：`GLM_API_KEY`（必填，智谱 API 密钥）；`GLM_API_BASE`（可选）
- 默认模型：`openai/glm-4v-flash`（免费）

## 使用约定

- 调用脚本时使用 `uv run python scripts/invoice_extract.py`，确保依赖可用
- 不要假设脚本在当前工作目录，使用绝对路径引用文件
- 原始文件不会被修改，脚本只读取
- 多页 PDF 会逐页识别，输出 JSON 数组；单页 PDF 输出单个对象
- 如果用户未指定 `--type`，脚本默认自动判断（invoice / payment）
- 提取结果输出到 stdout，用户可通过 `--output` 指定输出文件

## 推荐工作流

1. 确认用户提供的文件路径。
2. 调用脚本提取结构化信息。
3. 解析 JSON 结果，向用户展示关键信息（金额、收款方/销售方、日期等）。
4. 如有 `--type` 需求（已知是发票或付款截图），显式指定以提高准确率。
5. 批量处理时，可循环调用脚本并汇总结果。

## 常用命令

```text
uv run python scripts/invoice_extract.py "<发票.pdf>"
uv run python scripts/invoice_extract.py "<付款截图.jpg>"
uv run python scripts/invoice_extract.py "<文件>" --type invoice
uv run python scripts/invoice_extract.py "<文件>" --type payment
uv run python scripts/invoice_extract.py "<多页.pdf>" --output result.json
uv run python scripts/invoice_extract.py "<文件>" --model openai/glm-4v-flash
```

## 输出 JSON 字段

### 发票 (type=invoice)

| 字段 | 类型 | 说明 |
|------|------|------|
| type | string | 固定 "invoice" |
| invoice_type | string | 发票类型 |
| invoice_code | string | 发票代码 |
| invoice_number | string | 发票号码 |
| invoice_date | string | 开票日期 YYYY-MM-DD |
| buyer.name | string | 购买方名称 |
| buyer.tax_id | string | 购买方纳税人识别号 |
| seller.name | string | 销售方名称 |
| seller.tax_id | string | 销售方纳税人识别号 |
| items[] | array | 商品明细 |
| total_amount | number | 价税合计 |
| total_tax | number | 合计税额 |
| amount_in_words | string | 大写金额 |

### 付款截图 (type=payment)

| 字段 | 类型 | 说明 |
|------|------|------|
| type | string | 固定 "payment" |
| payment_channel | string | 付款渠道 |
| amount | number | 金额 |
| payer.name | string | 付款方 |
| payee.name | string | 收款方 |
| transaction_time | string | 交易时间 |
| transaction_id | string | 交易单号 |
| status | string | 交易状态 |
| remark | string | 备注 |

## 输出要求

- 向用户展示提取结果时，优先展示金额、收款方/销售方、日期等核心字段
- 如果识别结果中 `type` 为 `unknown`，说明图片内容无法识别，将原始描述告知用户
- 如果 JSON 解析失败（`type` 为 `error`），告知用户识别失败，建议检查图片质量或手动提供信息
- 多页 PDF 结果中，汇总各页金额并展示

## 风险边界

- 识别依赖 vision LLM，准确率受图片质量、模糊程度、截取范围影响
- 发票 OCR 可能遗漏部分字段（如规格型号、校验码），返回 null 是正常行为
- 金额等关键字段识别准确率通常较高，但不应作为财务审计依据
- 如果用户需要发票真伪验证，本技能不提供此功能，需另调用税务接口
