from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from ragtag_crew.zhidu_client import ZhiduClient, ZhiduClientError, main


class _FakeResponse:
    def __init__(self, payload: object):
        self._payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class ZhiduClientRequestTests(unittest.TestCase):
    def test_search_posts_expected_payload(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout=0):  # type: ignore[no-untyped-def]
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            captured["timeout"] = timeout
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse({"total": 1, "items": []})

        with patch("ragtag_crew.zhidu_client.urllib.request.urlopen", fake_urlopen):
            result = ZhiduClient(base_url="http://127.0.0.1:8964", timeout=9).search(
                "hybrid",
                query="中层 绩效",
                category="人力资源",
                limit=5,
                offset=10,
                threshold=0.25,
                keyword_weight=1.2,
                vector_weight=0.8,
            )

        self.assertEqual(result["total"], 1)
        self.assertEqual(captured["url"], "http://127.0.0.1:8964/api/search/hybrid")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["timeout"], 9)
        self.assertEqual(
            captured["body"],
            {
                "query": "中层 绩效",
                "category": "人力资源",
                "limit": 5,
                "offset": 10,
                "threshold": 0.25,
                "keyword_weight": 1.2,
                "vector_weight": 0.8,
            },
        )

    def test_doc_get_uses_expected_endpoint(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout=0):  # type: ignore[no-untyped-def]
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            return _FakeResponse({"docid": "307772", "title": "收入分配管理办法"})

        with patch("ragtag_crew.zhidu_client.urllib.request.urlopen", fake_urlopen):
            result = ZhiduClient().get_doc("307772")

        self.assertEqual(result["docid"], "307772")
        self.assertEqual(captured["url"], "http://127.0.0.1:8964/api/docs/307772")
        self.assertEqual(captured["method"], "GET")


class ZhiduClientCliTests(unittest.TestCase):
    def test_main_health_prints_summary(self) -> None:
        stdout = io.StringIO()
        with (
            patch.object(
                ZhiduClient,
                "health",
                return_value={
                    "ok": True,
                    "base_url": "http://127.0.0.1:8964",
                    "total": 107,
                    "sample": {
                        "docid": "307772",
                        "title": "中国标准化研究院收入分配管理办法（试行）",
                        "category": "人力资源",
                    },
                },
            ),
            redirect_stdout(stdout),
        ):
            code = main(["health"])

        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertIn("zhidu 服务可用", output)
        self.assertIn("107", output)
        self.assertIn("收入分配管理办法", output)

    def test_main_search_text_renders_chunk_hits(self) -> None:
        stdout = io.StringIO()
        with (
            patch.object(
                ZhiduClient,
                "search",
                return_value={
                    "query": "奖励性绩效工资",
                    "total": 1,
                    "items": [
                        {
                            "docid": "307772",
                            "title": "中国标准化研究院收入分配管理办法（试行）",
                            "category": "人力资源",
                            "score": 0.91,
                            "snippet": "奖励性绩效工资包括月绩效和年终绩效。",
                            "matched_chunk_count": 4,
                            "body_hit_count": 3,
                            "chunk_matches": [
                                {
                                    "chunk_index": 2,
                                    "score": 0.88,
                                    "text_preview": "第二十九条 院领导、职能部门的奖励性绩效工资...",
                                }
                            ],
                        }
                    ],
                },
            ),
            redirect_stdout(stdout),
        ):
            code = main(["search", "chunk", "--query", "奖励性绩效工资"])

        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertIn("检索方式: chunk", output)
        self.assertIn("分块命中: 4，正文命中: 3", output)
        self.assertIn("chunk#2", output)

    def test_main_attachment_reveal_prints_directory(self) -> None:
        stdout = io.StringIO()
        with (
            patch.object(
                ZhiduClient,
                "reveal_attachment",
                return_value={
                    "ok": True,
                    "path": r"Z:\agentworkspace\zhidu\downloads\人力资源\307772\附件1.docx",
                    "directory": r"Z:\agentworkspace\zhidu\downloads\人力资源\307772",
                },
            ),
            redirect_stdout(stdout),
        ):
            code = main(["attachment", "reveal", "1"])

        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertIn("附件操作成功: reveal", output)
        self.assertIn(r"Z:\agentworkspace\zhidu\downloads\人力资源\307772", output)

    def test_main_reports_client_error(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(
                ZhiduClient,
                "health",
                side_effect=ZhiduClientError("服务未启动"),
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = main(["health"])

        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("ERROR: 服务未启动", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
