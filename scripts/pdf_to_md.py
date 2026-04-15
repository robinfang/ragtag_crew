"""单文件 PDF 转 Markdown 脚本，使用 MinerU 在线 API。

用法: python scripts/pdf_to_md.py <pdf_path> [output_path]

示例: python scripts/pdf_to_md.py book.pdf book.md
"""

import hashlib
import io
import sys
import time
import zipfile
from pathlib import Path

import requests

TOKEN = "eyJ0eXBlIjoiSldUIiwiYWxnIjoiSFM1MTIifQ.eyJqdGkiOiIxNTMwMDEyOSIsInJvbCI6IlJPTEVfUkVHSVNURVIiLCJpc3MiOiJPcGVuWExhYiIsImlhdCI6MTc3MzExMjUyNSwiY2xpZW50SWQiOiJsa3pkeDU3bnZ5MjJqa3BxOXgydyIsInBob25lIjoiMTMwNDEwODE2ODYiLCJvcGVuSWQiOm51bGwsInV1aWQiOiI0NzY4NzIwMy0yYmFmLTQ5YzItYTA5Mi1lZGZlZTM5ODFiMDciLCJlbWFpbCI6IiIsImV4cCI6MTc4MDg4ODUyNX0.leHcr38dzFboN0jGLLVxCFmVoHrYmYqYPz-6q8b6KazZfqw8j7NCnaxXjaFz5uMttVsAOOBystJBlK7Rc8hBsA"

API_BASE = "https://mineru.net/api/v4"
BATCH_UPLOAD_URL = f"{API_BASE}/file-urls/batch"
BATCH_RESULT_URL = f"{API_BASE}/extract-results/batch"

POLL_INTERVAL = 30
MAX_POLL_WAIT = 3600


def auth_headers() -> dict:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {TOKEN}",
    }


def calc_md5(file_path: Path) -> str:
    h = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def submit_and_upload(pdf_path: Path) -> str:
    pdf_name = pdf_path.name
    pdf_md5 = calc_md5(pdf_path)

    print(f"PDF: {pdf_name} ({pdf_path.stat().st_size / 1024 / 1024:.1f} MB, md5={pdf_md5[:16]}...)")

    print("[1/3] 申请上传链接...")
    payload = {
        "files": [{"name": pdf_name, "data_id": pdf_md5}],
        "model_version": "vlm",
        "language": "en",
        "enable_formula": True,
        "enable_table": True,
    }

    retry_count = 0
    while retry_count < 10:
        resp = requests.post(BATCH_UPLOAD_URL, headers=auth_headers(), json=payload, timeout=30)
        data = resp.json()
        if data["code"] == 0:
            break
        if data["code"] == -60009:
            retry_count += 1
            print(f"  队列已满，{60}s 后重试 ({retry_count}/10)...")
            time.sleep(60)
            continue
        if data["code"] == -60018:
            print("  每日解析额度已达上限，请明天再试")
            sys.exit(1)
        print(f"  提交失败: code={data['code']}, msg={data}")
        sys.exit(1)

    batch_id = data["data"]["batch_id"]
    upload_url = data["data"]["file_urls"][0]
    print(f"  batch_id: {batch_id}")

    print("[2/3] 上传 PDF...")
    with open(pdf_path, "rb") as f:
        r = requests.put(upload_url, data=f, timeout=300)
    if r.status_code != 200:
        print(f"  上传失败: HTTP {r.status_code}")
        sys.exit(1)
    print("  上传成功")

    return batch_id


def poll_result(batch_id: str) -> dict:
    print("[3/3] 等待 MinerU 处理...")
    url = f"{BATCH_RESULT_URL}/{batch_id}"
    start = time.time()

    while True:
        elapsed = time.time() - start
        if elapsed > MAX_POLL_WAIT:
            print(f"  超时 ({MAX_POLL_WAIT}s)")
            sys.exit(1)

        resp = requests.get(url, headers=auth_headers(), timeout=30)
        data = resp.json()

        if data["code"] != 0:
            print(f"  查询失败: {data}")
            time.sleep(POLL_INTERVAL)
            continue

        results = data["data"].get("extract_result", [])
        if not results:
            time.sleep(POLL_INTERVAL)
            continue

        item = results[0]
        state = item["state"]

        if state == "done":
            print(f"  处理完成 ({elapsed:.0f}s)")
            return item
        if state == "failed":
            print(f"  处理失败: {item.get('err_msg', 'unknown')}")
            sys.exit(1)

        print(f"  状态: {state} ({elapsed:.0f}s elapsed)")
        time.sleep(POLL_INTERVAL)


def download_and_extract(result_item: dict, output_path: Path) -> None:
    zip_url = result_item["full_zip_url"]
    print(f"  下载结果 ZIP...")

    resp = requests.get(zip_url, timeout=120)
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        md_files = [n for n in zf.namelist() if n.endswith(".md")]
        if not md_files:
            print("  ZIP 中未找到 .md 文件")
            sys.exit(1)

        main_md = sorted(md_files, key=lambda n: (len(n), n))[0]
        print(f"  提取: {main_md}")
        content = zf.read(main_md).decode("utf-8", errors="replace")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    print(f"  已保存: {output_path} ({len(content):,} chars)")


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python pdf_to_md.py <pdf_path> [output_path]")
        sys.exit(1)

    pdf_path = Path(sys.argv[1]).resolve()
    if not pdf_path.exists():
        print(f"文件不存在: {pdf_path}")
        sys.exit(1)

    if len(sys.argv) >= 3:
        output_path = Path(sys.argv[2]).resolve()
    else:
        output_path = pdf_path.with_suffix(".md")

    batch_id = submit_and_upload(pdf_path)
    result_item = poll_result(batch_id)
    download_and_extract(result_item, output_path)

    print("\n完成!")


if __name__ == "__main__":
    main()
