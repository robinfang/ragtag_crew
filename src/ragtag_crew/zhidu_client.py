from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

DEFAULT_BASE_URL = "http://127.0.0.1:8964"
CONTENT_PREVIEW_LIMIT = 6000
SEARCH_TYPES = ("keyword", "vector", "hybrid", "chunk")


class ZhiduClientError(RuntimeError):
    """Raised when the local zhidu service cannot satisfy a request."""


@dataclass(frozen=True)
class ZhiduClient:
    base_url: str = DEFAULT_BASE_URL
    timeout: float = 20.0

    def request_json(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        url = self.base_url.rstrip("/") + path
        if query:
            query_string = urllib.parse.urlencode(
                {key: value for key, value in query.items() if value is not None}
            )
            if query_string:
                url = f"{url}?{query_string}"

        headers = {"Accept": "application/json"}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        request = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method=method.upper(),
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise ZhiduClientError(self._format_http_error(exc)) from exc
        except urllib.error.URLError as exc:
            raise ZhiduClientError(
                f"无法连接 zhidu 服务：{self.base_url}。请先启动 `Z:/agentworkspace/zhidu` 中的 Web 服务。"
            ) from exc

        if not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ZhiduClientError(
                f"zhidu 服务返回了非 JSON 响应：{raw[:200]}"
            ) from exc

    def _format_http_error(self, exc: urllib.error.HTTPError) -> str:
        body = exc.read().decode("utf-8", errors="replace")
        detail = body.strip()
        if body:
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(parsed, dict) and parsed.get("detail"):
                    detail = str(parsed["detail"])
        if not detail:
            detail = exc.reason or "unknown error"
        return f"zhidu 服务请求失败：HTTP {exc.code} - {detail}"

    def health(self) -> dict[str, Any]:
        docs = self.list_docs(limit=1, offset=0)
        sample = docs.get("items", [])[:1]
        return {
            "ok": True,
            "base_url": self.base_url,
            "total": docs.get("total", 0),
            "sample": sample[0] if sample else None,
        }

    def list_docs(
        self,
        *,
        limit: int = 1,
        offset: int = 0,
        category: str | None = None,
    ) -> dict[str, Any]:
        return self.request_json(
            "GET",
            "/api/docs",
            query={"limit": limit, "offset": offset, "category": category},
        )

    def search(
        self,
        search_type: str,
        *,
        query: str,
        category: str | None = None,
        limit: int = 10,
        offset: int = 0,
        threshold: float | None = None,
        keyword_weight: float | None = None,
        vector_weight: float | None = None,
    ) -> dict[str, Any]:
        if search_type not in SEARCH_TYPES:
            raise ZhiduClientError(f"不支持的检索类型：{search_type}")
        payload = {
            "query": query,
            "category": category,
            "limit": limit,
            "offset": offset,
        }
        if threshold is not None:
            payload["threshold"] = threshold
        if keyword_weight is not None:
            payload["keyword_weight"] = keyword_weight
        if vector_weight is not None:
            payload["vector_weight"] = vector_weight
        return self.request_json("POST", f"/api/search/{search_type}", payload=payload)

    def get_doc(self, docid: str) -> dict[str, Any]:
        return self.request_json("GET", f"/api/docs/{docid}")

    def open_attachment(self, attachment_id: int) -> dict[str, Any]:
        return self.request_json(
            "POST", f"/api/docs/attachments/{attachment_id}/open", payload={}
        )

    def reveal_attachment(self, attachment_id: int) -> dict[str, Any]:
        return self.request_json(
            "POST", f"/api/docs/attachments/{attachment_id}/reveal", payload={}
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="zhidu 本地制度库客户端")
    parser.add_argument(
        "--base-url",
        default=os.getenv("ZHIDU_BASE_URL", DEFAULT_BASE_URL),
        help="zhidu Web 服务地址，默认 http://127.0.0.1:8964",
    )
    parser.add_argument("--timeout", type=float, default=20.0, help="请求超时秒数")
    parser.add_argument("--json", action="store_true", dest="as_json", help="输出 JSON")

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("health", help="检查本地 zhidu 服务是否可用")

    search_parser = subparsers.add_parser("search", help="执行制度检索")
    search_parser.add_argument("search_type", choices=SEARCH_TYPES)
    search_parser.add_argument("--query", required=True, help="检索词")
    search_parser.add_argument("--category", help="分类过滤")
    search_parser.add_argument("--limit", type=int, default=10)
    search_parser.add_argument("--offset", type=int, default=0)
    search_parser.add_argument("--threshold", type=float)
    search_parser.add_argument("--keyword-weight", type=float)
    search_parser.add_argument("--vector-weight", type=float)

    doc_parser = subparsers.add_parser("doc", help="读取制度详情")
    doc_parser.add_argument("docid", help="制度文档 ID")

    attachment_parser = subparsers.add_parser("attachment", help="附件操作")
    attachment_subparsers = attachment_parser.add_subparsers(
        dest="attachment_action", required=True
    )
    open_parser = attachment_subparsers.add_parser("open", help="打开本地附件")
    open_parser.add_argument("attachment_id", type=int)
    reveal_parser = attachment_subparsers.add_parser("reveal", help="打开所在目录")
    reveal_parser.add_argument("attachment_id", type=int)

    return parser


def _print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _render_search_result(search_type: str, data: dict[str, Any]) -> str:
    items = data.get("items", [])
    lines = [
        f"检索方式: {search_type}",
        f"查询词: {data.get('query', '')}",
        f"结果总数: {data.get('total', 0)}",
    ]
    if not items:
        lines.append("未命中文档。")
        return "\n".join(lines)

    for idx, item in enumerate(items, start=1):
        lines.append(
            f"{idx}. [{item.get('category', '未知分类')}] {item.get('title', '无标题')}"
        )
        meta = [f"DocID: {item.get('docid', '-')}"]
        if item.get("score") is not None:
            meta.append(f"Score: {item['score']}")
        if item.get("owner"):
            meta.append(f"Owner: {item['owner']}")
        if item.get("createdate"):
            meta.append(f"Created: {item['createdate']}")
        if item.get("accessorycount") is not None:
            meta.append(f"附件: {item['accessorycount']}")
        lines.append("   " + " | ".join(meta))
        if item.get("snippet"):
            lines.append(f"   摘要: {item['snippet']}")
        if item.get("matched_chunk_count") is not None:
            lines.append(
                "   分块命中: "
                f"{item.get('matched_chunk_count', 0)}，正文命中: {item.get('body_hit_count', 0)}"
            )
        for chunk in item.get("chunk_matches", [])[:3]:
            chunk_line = chunk.get("text_preview", "")
            score = chunk.get("score")
            prefix = f"   - chunk#{chunk.get('chunk_index', '-')}"
            if score is not None:
                prefix += f" ({score})"
            lines.append(f"{prefix}: {chunk_line}")
    return "\n".join(lines)


def _render_doc_result(doc: dict[str, Any]) -> str:
    lines = [
        f"[{doc.get('category', '未知分类')}] {doc.get('title', '无标题')}",
        f"DocID: {doc.get('docid', '-')}",
    ]
    meta = []
    if doc.get("owner"):
        meta.append(f"所有者: {doc['owner']}")
    if doc.get("createdate"):
        meta.append(f"创建: {doc['createdate']}")
    if doc.get("lastmoddate"):
        meta.append(f"修改: {doc['lastmoddate']}")
    if doc.get("catalog"):
        meta.append(f"目录: {doc['catalog']}")
    if meta:
        lines.append(" | ".join(meta))

    content = (doc.get("content") or "").strip()
    if content:
        if len(content) > CONTENT_PREVIEW_LIMIT:
            content = content[:CONTENT_PREVIEW_LIMIT].rstrip() + "\n...[正文已截断]"
        lines.append("")
        lines.append(content)

    attachments = doc.get("attachments", [])
    if attachments:
        lines.append("")
        lines.append("附件:")
        for att in attachments:
            extra = []
            if att.get("filetype"):
                extra.append(str(att["filetype"]))
            if att.get("filesize"):
                extra.append(f"{att['filesize']} bytes")
            if att.get("local_path"):
                extra.append(f"本地: {att['local_path']}")
            suffix = f" [{' | '.join(extra)}]" if extra else ""
            lines.append(
                f"- #{att.get('id', '-')} {att.get('filename', '未命名附件')}{suffix}"
            )
    return "\n".join(lines)


def _render_health_result(result: dict[str, Any]) -> str:
    lines = [
        f"zhidu 服务可用: {result.get('base_url', DEFAULT_BASE_URL)}",
        f"文档总数: {result.get('total', 0)}",
    ]
    sample = result.get("sample")
    if sample:
        lines.append(
            f"示例文档: [{sample.get('category', '未知分类')}] {sample.get('title', '无标题')} ({sample.get('docid', '-')})"
        )
    return "\n".join(lines)


def _render_attachment_result(action: str, result: dict[str, Any]) -> str:
    lines = [f"附件操作成功: {action}"]
    if result.get("path"):
        lines.append(f"路径: {result['path']}")
    if result.get("directory"):
        lines.append(f"目录: {result['directory']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    client = ZhiduClient(base_url=args.base_url, timeout=args.timeout)

    try:
        if args.command == "health":
            result = client.health()
            if args.as_json:
                _print_json(result)
            else:
                print(_render_health_result(result))
            return 0

        if args.command == "search":
            result = client.search(
                args.search_type,
                query=args.query,
                category=args.category,
                limit=args.limit,
                offset=args.offset,
                threshold=args.threshold,
                keyword_weight=args.keyword_weight,
                vector_weight=args.vector_weight,
            )
            if args.as_json:
                _print_json(result)
            else:
                print(_render_search_result(args.search_type, result))
            return 0

        if args.command == "doc":
            result = client.get_doc(args.docid)
            if args.as_json:
                _print_json(result)
            else:
                print(_render_doc_result(result))
            return 0

        if args.command == "attachment":
            if args.attachment_action == "open":
                result = client.open_attachment(args.attachment_id)
            else:
                result = client.reveal_attachment(args.attachment_id)
            if args.as_json:
                _print_json(result)
            else:
                print(_render_attachment_result(args.attachment_action, result))
            return 0
    except ZhiduClientError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    parser.error("unknown command")
    return 2
