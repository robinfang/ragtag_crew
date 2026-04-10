from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

from ragtag_crew.repl_streamer import ReplStreamer, _summarize_args


class SummarizeArgsTests(unittest.TestCase):
    def test_short_values(self) -> None:
        result = _summarize_args({"path": "foo.py", "line": 10})
        self.assertEqual(result, "path=foo.py, line=10")

    def test_truncates_long_values(self) -> None:
        result = _summarize_args({"content": "x" * 100})
        self.assertTrue(result.endswith("..."))
        self.assertEqual(len(result), 37 + len("content=") + 3)

    def test_empty_args(self) -> None:
        self.assertEqual(_summarize_args({}), "")


class ReplStreamerTests(unittest.IsolatedAsyncioTestCase):
    def _make_streamer(self) -> tuple[ReplStreamer, io.StringIO]:
        streamer = ReplStreamer()
        buf = io.StringIO()
        return streamer, buf

    async def test_message_update_prints_delta(self) -> None:
        streamer, buf = self._make_streamer()
        with redirect_stdout(buf):
            await streamer.on_event("message_update", delta="hello ")
            await streamer.on_event("message_update", delta="world")
        self.assertEqual(buf.getvalue(), "hello world")
        self.assertEqual(streamer.buffer, "hello world")

    async def test_tool_execution_start(self) -> None:
        streamer, buf = self._make_streamer()
        tc = SimpleNamespace(name="read", arguments={"path": "README.md"})
        with redirect_stdout(buf):
            await streamer.on_event("tool_execution_start", tool_call=tc)
        self.assertIn("⏳ read(path=README.md)", buf.getvalue())

    async def test_tool_execution_end_short_result(self) -> None:
        streamer, buf = self._make_streamer()
        with redirect_stdout(buf):
            await streamer.on_event(
                "tool_execution_end",
                tool_call=SimpleNamespace(name="read", arguments={}),
                result="short result",
            )
        out = buf.getvalue()
        self.assertIn("✅", out)
        self.assertIn("short result", out)
        self.assertNotIn("...", out)

    async def test_tool_execution_end_long_result_truncates(self) -> None:
        streamer, buf = self._make_streamer()
        long_result = "x" * 300
        with redirect_stdout(buf):
            await streamer.on_event(
                "tool_execution_end",
                tool_call=SimpleNamespace(name="read", arguments={}),
                result=long_result,
            )
        out = buf.getvalue()
        self.assertIn("✅", out)
        self.assertIn("...", out)

    async def test_tool_execution_end_empty_result(self) -> None:
        streamer, buf = self._make_streamer()
        with redirect_stdout(buf):
            await streamer.on_event(
                "tool_execution_end",
                tool_call=SimpleNamespace(name="bash", arguments={}),
                result="",
            )
        self.assertEqual(buf.getvalue(), "")

    async def test_agent_end_prints_newline_when_buffer_has_content(self) -> None:
        streamer, buf = self._make_streamer()
        with redirect_stdout(buf):
            await streamer.on_event("message_update", delta="some text")
            await streamer.on_event("agent_end")
        self.assertTrue(buf.getvalue().endswith("\n"))

    async def test_agent_end_no_newline_when_buffer_empty(self) -> None:
        streamer, buf = self._make_streamer()
        with redirect_stdout(buf):
            await streamer.on_event("agent_end")
        self.assertEqual(buf.getvalue(), "")

    async def test_cancelled(self) -> None:
        streamer, buf = self._make_streamer()
        with redirect_stdout(buf):
            await streamer.on_event("cancelled")
        self.assertIn("⚠️ 已取消", buf.getvalue())

    async def test_error(self) -> None:
        streamer, buf = self._make_streamer()
        with redirect_stdout(buf):
            await streamer.on_event("error", error=RuntimeError("boom"))
        self.assertIn("❌", buf.getvalue())
        self.assertIn("boom", buf.getvalue())

    async def test_error_no_error_kwarg(self) -> None:
        streamer, buf = self._make_streamer()
        with redirect_stdout(buf):
            await streamer.on_event("error")
        self.assertIn("❌", buf.getvalue())
        self.assertIn("Unknown error", buf.getvalue())

    async def test_message_end_fallback_when_buffer_empty(self) -> None:
        streamer, buf = self._make_streamer()
        with redirect_stdout(buf):
            await streamer.on_event("message_end", content="final answer")
        self.assertEqual(streamer.buffer, "final answer")
        self.assertIn("final answer", buf.getvalue())

    async def test_message_end_no_print_when_buffer_has_content(self) -> None:
        streamer, buf = self._make_streamer()
        with redirect_stdout(buf):
            await streamer.on_event("message_update", delta="partial")
            await streamer.on_event("message_end", content="partial answer")
        out = buf.getvalue()
        self.assertEqual(out, "partial")

    async def test_full_flow(self) -> None:
        streamer, buf = self._make_streamer()
        tc = SimpleNamespace(name="read", arguments={"path": "main.py"})
        with redirect_stdout(buf):
            await streamer.on_event("message_update", delta="Let me read the file.\n")
            await streamer.on_event("tool_execution_start", tool_call=tc)
            await streamer.on_event(
                "tool_execution_end", tool_call=tc, result="file contents here"
            )
            await streamer.on_event("message_update", delta="The file looks good.")
            await streamer.on_event("agent_end")
        out = buf.getvalue()
        self.assertIn("Let me read the file.", out)
        self.assertIn("⏳ read(path=main.py)", out)
        self.assertIn("✅", out)
        self.assertIn("The file looks good.", out)
        self.assertTrue(out.endswith("\n"))


if __name__ == "__main__":
    unittest.main()
