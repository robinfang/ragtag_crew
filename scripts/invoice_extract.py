"""发票 PDF / 付款截图结构化提取脚本。

通过 vision LLM（默认 GLM-4V-Flash）识别发票或付款截图，
输出结构化 JSON。

用法:
    python scripts/invoice_extract.py <file_path> [options]

示例:
    python scripts/invoice_extract.py 发票.pdf
    python scripts/invoice_extract.py 付款截图.jpg --type payment
    python scripts/invoice_extract.py 发票.pdf --type invoice --output result.json
    python scripts/invoice_extract.py 多页.pdf --output results.json

环境变量:
    GLM_API_KEY       - 智谱 API 密钥
    GLM_API_BASE      - 智谱 API 地址（默认 https://open.bigmodel.cn/api/coding/paas/v4）
    OPENAI_API_KEY    - OpenAI 兼容 API 密钥（GLM_API_KEY 未设置时使用）
    OPENAI_API_BASE   - OpenAI 兼容 API 地址
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
from pathlib import Path

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"}
PDF_EXTENSIONS = {".pdf"}

RENDER_DPI = 200

DEFAULT_MODEL = "openai/glm-4v-flash"
DEFAULT_GLM_BASE = "https://open.bigmodel.cn/api/coding/paas/v4"

INVOICE_PROMPT = """请识别这张图片中的发票信息，按以下JSON格式输出：

{
  "type": "invoice",
  "invoice_type": "发票类型（如增值税电子普通发票、增值税专用发票、电子发票等）",
  "invoice_code": "发票代码",
  "invoice_number": "发票号码",
  "invoice_date": "开票日期，格式YYYY-MM-DD",
  "check_code": "校验码（如有）",
  "buyer": {"name": "购买方名称", "tax_id": "纳税人识别号"},
  "seller": {"name": "销售方名称", "tax_id": "纳税人识别号"},
  "items": [
    {
      "name": "项目名称",
      "spec": "规格型号",
      "unit": "单位",
      "quantity": 数量,
      "unit_price": 单价,
      "amount": 金额,
      "tax_rate": "税率，如6%",
      "tax": 税额
    }
  ],
  "total_amount": 价税合计金额（数字）,
  "total_tax": 合计税额（数字）,
  "amount_in_words": "价税合计大写"
}

规则：
1. 只输出纯JSON，不要包含markdown代码块标记或其他文字
2. 无法识别的字段设为null
3. 金额、数量等数值使用数字类型
4. 如果图片不是发票，请输出 {"type": "unknown", "description": "简要说明图片内容"}"""

PAYMENT_PROMPT = """请识别这张图片中的付款/转账信息，按以下JSON格式输出：

{
  "type": "payment",
  "payment_channel": "付款渠道（银行APP/支付宝/微信支付/网银/其他）",
  "amount": 金额（数字）,
  "currency": "货币，如CNY",
  "payer": {"name": "付款方名称", "account": "付款账号/卡号"},
  "payee": {"name": "收款方名称", "account": "收款账号/卡号"},
  "transaction_time": "交易时间，格式YYYY-MM-DD HH:mm:ss",
  "transaction_id": "交易单号/凭证号",
  "status": "交易状态（成功/处理中/失败）",
  "remark": "备注/摘要/用途"
}

规则：
1. 只输出纯JSON，不要包含markdown代码块标记或其他文字
2. 无法识别的字段设为null
3. 金额使用数字类型
4. 如果图片不是付款/转账截图，请输出 {"type": "unknown", "description": "简要说明图片内容"}"""

AUTO_PROMPT = """请识别这张图片中的内容类型并提取结构化信息。

如果图片是发票（增值税发票、电子发票等），输出：
{
  "type": "invoice",
  "invoice_type": "发票类型",
  "invoice_code": "发票代码",
  "invoice_number": "发票号码",
  "invoice_date": "YYYY-MM-DD",
  "check_code": "校验码",
  "buyer": {"name": "购买方名称", "tax_id": "纳税人识别号"},
  "seller": {"name": "销售方名称", "tax_id": "纳税人识别号"},
  "items": [{"name": "项目名称", "spec": "规格型号", "unit": "单位", "quantity": 数量, "unit_price": 单价, "amount": 金额, "tax_rate": "税率", "tax": 税额}],
  "total_amount": 价税合计,
  "total_tax": 合计税额,
  "amount_in_words": "大写金额"
}

如果图片是付款/转账截图，输出：
{
  "type": "payment",
  "payment_channel": "银行APP/支付宝/微信支付/网银/其他",
  "amount": 金额,
  "currency": "CNY",
  "payer": {"name": "付款方", "account": "付款账号"},
  "payee": {"name": "收款方", "account": "收款账号"},
  "transaction_time": "YYYY-MM-DD HH:mm:ss",
  "transaction_id": "交易单号",
  "status": "成功/处理中/失败",
  "remark": "备注"
}

规则：
1. 只输出纯JSON，不要包含markdown代码块标记或其他文字
2. 无法识别的字段设为null，数值字段使用数字类型
3. 如果都不是，输出 {"type": "unknown", "description": "简要说明"}"""


def _get_api_credentials() -> tuple[str, str]:
    """返回 (api_key, api_base)，优先使用 GLM 环境变量。"""
    api_key = os.getenv("GLM_API_KEY", "").strip()
    api_base = os.getenv("GLM_API_BASE", DEFAULT_GLM_BASE).strip()

    if not api_key:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        api_base = os.getenv("OPENAI_API_BASE", "").strip()

    if not api_key:
        print("错误：未设置 GLM_API_KEY 或 OPENAI_API_KEY 环境变量", file=sys.stderr)
        sys.exit(1)

    return api_key, api_base


def _load_env() -> None:
    """从项目根目录加载 .env 文件（如果存在）。"""
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent
    env_file = project_root / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("\"'")
            if key not in os.environ:
                os.environ[key] = value


def _render_pdf_to_images(pdf_path: Path, dpi: int = RENDER_DPI) -> list[bytes]:
    """将 PDF 每页渲染为 PNG 字节。"""
    import fitz

    doc = fitz.open(str(pdf_path))
    images: list[bytes] = []
    for page_num, page in enumerate(doc, 1):
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat)
        img_data = pix.tobytes("png")
        images.append(img_data)
        print(f"  第 {page_num}/{len(doc)} 页 ({pix.width}x{pix.height})", file=sys.stderr)
    doc.close()
    return images


def _encode_image(img_bytes: bytes, ext: str = ".png") -> str:
    """将图片字节编码为 base64 data URL。"""
    mime = "image/png"
    if ext in (".jpg", ".jpeg"):
        mime = "image/jpeg"
    elif ext in (".bmp",):
        mime = "image/bmp"
    elif ext in (".webp",):
        mime = "image/webp"
    b64 = base64.b64encode(img_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _call_vision_llm(
    image_data_url: str,
    prompt: str,
    model: str,
    api_key: str,
    api_base: str,
) -> str:
    """调用 vision LLM 返回文本响应。"""
    import litellm

    print(f"  调用 {model} ...", file=sys.stderr, flush=True)
    response = litellm.completion(
        model=model,
        api_key=api_key,
        api_base=api_base,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            }
        ],
        temperature=0,
    )
    return response.choices[0].message.content or ""


def _parse_json_response(text: str) -> dict:
    """从 LLM 响应中提取 JSON，容忍 markdown 代码块包裹。"""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    return json.loads(text)


def extract_single(
    image_bytes: bytes,
    ext: str,
    doc_type: str,
    model: str,
    api_key: str,
    api_base: str,
) -> dict:
    """对单张图片执行提取。"""
    data_url = _encode_image(image_bytes, ext)
    if doc_type == "invoice":
        prompt = INVOICE_PROMPT
    elif doc_type == "payment":
        prompt = PAYMENT_PROMPT
    else:
        prompt = AUTO_PROMPT

    raw = _call_vision_llm(data_url, prompt, model, api_key, api_base)
    try:
        result = _parse_json_response(raw)
    except json.JSONDecodeError:
        print(f"  JSON 解析失败，原始响应：{raw[:500]}", file=sys.stderr)
        result = {"type": "error", "raw_response": raw, "description": "JSON解析失败"}

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="发票/付款截图结构化提取")
    parser.add_argument("file", help="PDF 或图片文件路径")
    parser.add_argument(
        "--type",
        choices=["auto", "invoice", "payment"],
        default="auto",
        help="文档类型，默认自动判断",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="vision 模型名称")
    parser.add_argument("--output", "-o", default=None, help="输出 JSON 文件路径（默认输出到 stdout）")
    parser.add_argument("--dpi", type=int, default=RENDER_DPI, help="PDF 渲染 DPI（默认 200）")
    args = parser.parse_args()

    _load_env()

    file_path = Path(args.file).resolve()
    if not file_path.exists():
        print(f"文件不存在: {file_path}", file=sys.stderr)
        sys.exit(1)

    ext = file_path.suffix.lower()
    is_pdf = ext in PDF_EXTENSIONS
    is_image = ext in IMAGE_EXTENSIONS

    if not is_pdf and not is_image:
        print(f"不支持的文件格式: {ext}", file=sys.stderr)
        sys.exit(1)

    api_key, api_base = _get_api_credentials()
    print(f"文件: {file_path.name} ({file_path.stat().st_size / 1024:.1f} KB)", file=sys.stderr)

    if is_pdf:
        images = _render_pdf_to_images(file_path, args.dpi)
        results: list[dict] = []
        for i, img_bytes in enumerate(images):
            print(f"识别第 {i + 1}/{len(images)} 页 ...", file=sys.stderr)
            result = extract_single(img_bytes, ".png", args.type, args.model, api_key, api_base)
            result["page"] = i + 1
            results.append(result)

        if len(results) == 1:
            output = results[0]
        else:
            output = {"pages": results, "page_count": len(results)}
    else:
        img_bytes = file_path.read_bytes()
        output = extract_single(img_bytes, ext, args.type, args.model, api_key, api_base)

    json_str = json.dumps(output, ensure_ascii=False, indent=2)

    if args.output:
        out_path = Path(args.output).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json_str, encoding="utf-8")
        print(f"结果已保存: {out_path}", file=sys.stderr)
    else:
        print(json_str)


if __name__ == "__main__":
    main()
