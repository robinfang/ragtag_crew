from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from ragtag_crew.llm import ToolCall
from ragtag_crew.runtime_events import (
    AgentStartEvent,
    ErrorEvent,
    MessageEndEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from ragtag_crew.trace import (
    JsonlTraceSink,
    TraceCollector,
    TraceRecordBuilder,
    _clip,
    _summarize_args,
)


class ClipTests(unittest.TestCase):
    def test_short_text_unchanged(self) -> None:
        self.assertEqual(_clip("hello", 10), "hello")

    def test_long_text_truncated(self) -> None:
        self.assertEqual(_clip("a" * 100, 10), "aaaaaaa...")

    def test_default_limit(self) -> None:
        self.assertTrue(len(_clip("x" * 1000)) <= 500)


class SummarizeArgsTests(unittest.TestCase):
    def test_empty_args(self) -> None:
        self.assertEqual(_summarize_args({}), "")

    def test_small_args(self) -> None:
        self.assertEqual(_summarize_args({"a": 1}), '{"a": 1}')

    def test_large_args_truncated(self) -> None:
        big = {"content": "x" * 2000}
        result = _summarize_args(big, limit=100)
        self.assertLessEqual(len(result), 100)
        self.assertTrue(result.endswith("..."))


class TraceCollectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_builder_builds_record_without_filesystem(self) -> None:
        builder = TraceRecordBuilder(trace_id="trace-1", session_key="42")
        builder.set_context(
            model="openai/GLM-5.1",
            user_input="read the file",
            tool_preset="coding",
            enabled_skills=["review"],
            planning_enabled=True,
        )

        await builder.on_event(
            AgentStartEvent(
                prompt_phase="execution",
                awaiting_plan_confirmation_at_start=False,
            )
        )
        await builder.on_event(TurnStartEvent(turn=1, tools_enabled=True))
        await builder.on_event(MessageEndEvent(content="done"))
        await builder.on_event(TurnEndEvent(turn=1))

        record = builder.build_record()
        self.assertEqual(record["trace_id"], "trace-1")
        self.assertEqual(record["session_key"], "42")
        self.assertEqual(record["model"], "openai/GLM-5.1")
        self.assertEqual(record["turns"][0]["turn"], 1)
        self.assertTrue(record["turns"][0]["has_content"])

    def test_jsonl_sink_appends_records(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            sink = JsonlTraceSink(trace_dir=tmpdir)
            first = sink.write({"trace_id": "t1", "value": 1})
            second = sink.write({"trace_id": "t2", "value": 2})

            self.assertEqual(first, second)
            assert first is not None
            lines = Path(first).read_text(encoding="utf-8").strip().split("\n")
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[0])["trace_id"], "t1")
            self.assertEqual(json.loads(lines[1])["trace_id"], "t2")

    async def test_full_trace_produces_valid_jsonl(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch("ragtag_crew.trace.settings.trace_enabled", True),
                patch("ragtag_crew.trace.settings.trace_dir", tmpdir),
            ):
                c = TraceCollector(session_key=42)
                c.set_context(
                    model="openai/GLM-5.1",
                    user_input="read the file",
                    tool_preset="coding",
                    enabled_skills=["review"],
                    planning_enabled=True,
                )

                await c.on_event(
                    AgentStartEvent(
                        prompt_phase="execution",
                        awaiting_plan_confirmation_at_start=True,
                    )
                )
                await c.on_event(TurnStartEvent(turn=1, tools_enabled=True))
                await c.on_event(MessageEndEvent(content="let me read that"))
                await c.on_event(
                    ToolExecutionStartEvent(
                        tool_call=ToolCall(
                            id="c1", name="read", arguments={"file_path": "foo.py"}
                        )
                    )
                )
                await c.on_event(
                    ToolExecutionEndEvent(
                        tool_call=ToolCall(
                            id="c1", name="read", arguments={"file_path": "foo.py"}
                        ),
                        result="line 1: hello",
                    )
                )
                await c.on_event(TurnEndEvent(turn=1))

                path = c.finalize()
                self.assertIsNotNone(path)

                lines = Path(path).read_text(encoding="utf-8").strip().split("\n")
                self.assertEqual(len(lines), 1)
                record = json.loads(lines[0])

                self.assertEqual(record["trace_id"], c.trace_id)
                self.assertEqual(record["session_key"], "42")
                self.assertEqual(record["model"], "openai/GLM-5.1")
                self.assertEqual(record["user_input"], "read the file")
                self.assertEqual(record["tool_preset"], "coding")
                self.assertEqual(record["enabled_skills"], ["review"])
                self.assertTrue(record["planning_enabled"])
                self.assertTrue(record["awaiting_plan_confirmation_at_start"])
                self.assertEqual(record["prompt_phase"], "execution")
                self.assertEqual(record["total_turns"], 1)
                self.assertEqual(record["status"], "success")
                self.assertEqual(len(record["turns"]), 1)

                turn = record["turns"][0]
                self.assertEqual(turn["turn"], 1)
                self.assertTrue(turn["tools_enabled"])
                self.assertTrue(turn["has_content"])
                self.assertEqual(turn["tool_calls"], ["read"])
                self.assertEqual(len(turn["tools"]), 1)

                tool = turn["tools"][0]
                self.assertEqual(tool["name"], "read")
                self.assertEqual(tool["status"], "success")
                self.assertIn("duration_ms", tool)

    async def test_error_status_on_error_event(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch("ragtag_crew.trace.settings.trace_enabled", True),
                patch("ragtag_crew.trace.settings.trace_dir", tmpdir),
            ):
                c = TraceCollector()
                await c.on_event(ErrorEvent(error=RuntimeError("boom")))
                path = c.finalize()

                record = json.loads(Path(path).read_text(encoding="utf-8").strip())
                self.assertEqual(record["status"], "error")
                self.assertIn("RuntimeError", record["error_info"])

    async def test_finalize_disabled_returns_none(self) -> None:
        with patch("ragtag_crew.trace.settings.trace_enabled", False):
            c = TraceCollector()
            self.assertIsNone(c.finalize())

    async def test_multiple_traces_append_to_same_file(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch("ragtag_crew.trace.settings.trace_enabled", True),
                patch("ragtag_crew.trace.settings.trace_dir", tmpdir),
            ):
                c1 = TraceCollector(session_key=1)
                c1.set_context(model="m1", user_input="first")
                c1.finalize()

                c2 = TraceCollector(session_key=2)
                c2.set_context(model="m2", user_input="second")
                c2.finalize()

                traces_dir = Path(tmpdir)
                jsonl_files = list(traces_dir.glob("*.jsonl"))
                self.assertEqual(len(jsonl_files), 1)

                lines = jsonl_files[0].read_text(encoding="utf-8").strip().split("\n")
                self.assertEqual(len(lines), 2)

    async def test_tool_error_status(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch("ragtag_crew.trace.settings.trace_enabled", True),
                patch("ragtag_crew.trace.settings.trace_dir", tmpdir),
            ):
                c = TraceCollector()
                await c.on_event(TurnStartEvent(turn=1, tools_enabled=False))
                await c.on_event(MessageEndEvent(content=""))
                await c.on_event(
                    ToolExecutionStartEvent(
                        tool_call=ToolCall(
                            id="c1", name="bash", arguments={"command": "rm -rf /"}
                        )
                    )
                )
                await c.on_event(
                    ToolExecutionEndEvent(
                        tool_call=ToolCall(
                            id="c1", name="bash", arguments={"command": "rm -rf /"}
                        ),
                        result="ERROR: delete commands are blocked",
                    )
                )
                await c.on_event(TurnEndEvent(turn=1))

                path = c.finalize()
                record = json.loads(Path(path).read_text(encoding="utf-8").strip())
                tool = record["turns"][0]["tools"][0]
                self.assertEqual(tool["status"], "error")


if __name__ == "__main__":
    unittest.main()
