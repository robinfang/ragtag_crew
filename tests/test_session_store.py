from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from ragtag_crew.agent import AgentSession
from ragtag_crew.config import settings
from ragtag_crew.session_store import (
    cleanup_expired_sessions,
    list_sessions,
    load_session,
    read_session_payload,
    save_session,
)
from ragtag_crew.tools import Tool

import ragtag_crew.tools.file_tools  # noqa: F401
import ragtag_crew.tools.search_tools  # noqa: F401
import ragtag_crew.tools.shell_tools  # noqa: F401


async def _noop_tool(**_: str) -> str:
    return "ok"


@contextmanager
def session_storage(path: Path):
    original_dir = settings.session_storage_dir
    original_ttl = settings.session_ttl_hours
    settings.session_storage_dir = str(path)
    settings.session_ttl_hours = 1
    try:
        yield
    finally:
        settings.session_storage_dir = original_dir
        settings.session_ttl_hours = original_ttl


class SessionStoreTests(unittest.TestCase):
    def test_save_and_load_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with session_storage(root):
                session = AgentSession(
                    model="openai/GLM-5.1",
                    tools=[Tool("noop", "noop", {"type": "object"}, _noop_tool)],
                    system_prompt="system",
                    tool_preset="readonly",
                    enabled_skills=["review"],
                    session_prompt="prefer concise answers",
                    protected_content="always preserve this rule",
                    compression_blocks=[
                        {
                            "block_id": "b1",
                            "created_at": 1.0,
                            "message_count": 2,
                            "summary": "older stuff",
                        }
                    ],
                    session_summary="Discussed repository layout.",
                    summary_updated_at=1234.5,
                    recent_message_count=6,
                    browser_mode="attached",
                    browser_attached_confirmed=True,
                )
                session.messages = [{"role": "user", "content": "hi"}]
                save_session(123, session)

                restored = load_session(123, default_system_prompt="fallback")

        self.assertIsNotNone(restored)
        self.assertEqual(restored.model, "openai/GLM-5.1")
        self.assertEqual(restored.tool_preset, "readonly")
        self.assertEqual(restored.enabled_skills, ["review"])
        self.assertEqual(restored.session_prompt, "prefer concise answers")
        self.assertEqual(restored.protected_content, "always preserve this rule")
        self.assertEqual(len(restored.compression_blocks), 1)
        self.assertEqual(restored.compression_blocks[0]["block_id"], "b1")
        self.assertEqual(restored.session_summary, "Discussed repository layout.")
        self.assertEqual(restored.summary_updated_at, 1234.5)
        self.assertEqual(restored.recent_message_count, 6)
        self.assertEqual(restored.browser_mode, "attached")
        self.assertTrue(restored.browser_attached_confirmed)
        self.assertEqual(restored.messages[0]["content"], "hi")

    def test_save_uses_atomic_replace_without_tmp_leftovers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with session_storage(root):
                session = AgentSession(
                    model="openai/GLM-5.1",
                    tools=[Tool("noop", "noop", {"type": "object"}, _noop_tool)],
                )
                save_session(123, session)

                json_files = sorted(root.glob("*.json"))
                tmp_files = sorted(root.glob("*.tmp"))

        self.assertEqual([path.name for path in json_files], ["123.json"])
        self.assertEqual(tmp_files, [])

    def test_expired_sessions_are_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with session_storage(root):
                path = root / "123.json"
                path.write_text(
                    json.dumps({"last_active_at": 1}),
                    encoding="utf-8",
                )
                with patch("ragtag_crew.session_store.time.time", return_value=10_000):
                    cleanup_expired_sessions()

                self.assertFalse(path.exists())

    def test_corrupt_file_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with session_storage(root):
                path = root / "123.json"
                path.write_text("{not json", encoding="utf-8")

                restored = load_session(123, default_system_prompt="fallback")

        self.assertIsNone(restored)

    def test_list_sessions_returns_sorted_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with session_storage(root):
                (root / "1.json").write_text(
                    json.dumps(
                        {
                            "chat_id": 1,
                            "last_active_at": 10,
                            "model": "m1",
                            "tool_preset": "coding",
                        }
                    ),
                    encoding="utf-8",
                )
                (root / "2.json").write_text(
                    json.dumps(
                        {
                            "chat_id": 2,
                            "last_active_at": 20,
                            "model": "m2",
                            "tool_preset": "readonly",
                        }
                    ),
                    encoding="utf-8",
                )

                records = list_sessions()

        self.assertEqual([r.chat_id for r in records], [2, 1])
        self.assertEqual(records[0].model, "m2")
        self.assertEqual(records[1].tool_preset, "coding")

    def test_read_session_payload_returns_raw_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with session_storage(root):
                (root / "123.json").write_text(
                    json.dumps({"chat_id": 123, "session_summary": "hello"}),
                    encoding="utf-8",
                )
                payload = read_session_payload(123)

        self.assertEqual(payload["chat_id"], 123)
        self.assertEqual(payload["session_summary"], "hello")


if __name__ == "__main__":
    unittest.main()
