from __future__ import annotations

import pytest

from ragtag_crew.session_summary import (
    _clip,
    _clip_text,
    _merge_summary,
    _summarize_message,
    _tool_call_label,
    clear_stale_tool_results,
    compact_history,
)


class TestClip:
    def test_within_limit(self):
        assert _clip("hello", 10) == "hello"

    def test_exceeds_limit(self):
        result = _clip("a" * 20, 10)
        assert result.endswith("...")
        assert len(result) == 10

    def test_exact_limit(self):
        assert _clip("hello", 5) == "hello"

    def test_zero_limit(self):
        assert _clip("hello", 0) == ""

    def test_negative_limit(self):
        assert _clip("hello", -1) == ""


class TestClipText:
    def test_normal_string(self):
        assert _clip_text("hello world", 20) == "hello world"

    def test_collapse_whitespace(self):
        assert _clip_text("hello   world\n\nfoo", 50) == "hello world foo"

    def test_non_string(self):
        assert _clip_text(None, 50) == ""

    def test_default_limit_500(self):
        short = "x" * 400
        assert _clip_text(short) == short
        long_text = "x" * 600
        result = _clip_text(long_text)
        assert len(result) == 500


class TestToolCallLabel:
    def _tc(self, name: str, args: dict | str | None = None) -> dict:
        if args is None:
            args = {}
        if isinstance(args, str):
            args_str = args
        else:
            import json

            args_str = json.dumps(args)
        return {"function": {"name": name, "arguments": args_str}}

    def test_name_only(self):
        assert _tool_call_label(self._tc("read_file")) == "read_file"

    def test_with_path_arg(self):
        result = _tool_call_label(self._tc("read_file", {"path": "src/agent.py"}))
        assert "read_file" in result
        assert "path=src/agent.py" in result

    def test_with_query_arg(self):
        result = _tool_call_label(
            self._tc("grep", {"query": "TODO", "pattern": "fixme"})
        )
        assert "query=TODO" in result
        assert "pattern=fixme" in result

    def test_with_url_arg(self):
        result = _tool_call_label(
            self._tc("web_search", {"url": "https://example.com"})
        )
        assert "url=https://example.com" in result

    def test_multiple_key_args(self):
        result = _tool_call_label(
            self._tc("edit_file", {"path": "foo.py", "content": "new text"})
        )
        assert "path=foo.py" in result

    def test_non_relevant_args_not_shown(self):
        result = _tool_call_label(self._tc("bash", {"command": "ls -la"}))
        assert result == "bash"

    def test_invalid_json_args(self):
        result = _tool_call_label(self._tc("read_file", "not json"))
        assert result == "read_file"

    def test_no_function_key(self):
        result = _tool_call_label({"name": "old_style"})
        assert result == "old_style"

    def test_long_arg_value_truncated(self):
        result = _tool_call_label(self._tc("edit_file", {"path": "x" * 100}))
        assert "path=" in result
        assert len(result) < 150


class TestSummarizeMessage:
    def test_user_message(self):
        msg = {"role": "user", "content": "fix the bug in agent.py"}
        result = _summarize_message(msg)
        assert "User request" in result
        assert "fix the bug" in result

    def test_user_empty_content(self):
        msg = {"role": "user", "content": ""}
        assert _summarize_message(msg) == ""

    def test_assistant_text_only(self):
        msg = {"role": "assistant", "content": "I'll fix that now."}
        result = _summarize_message(msg)
        assert "Assistant response" in result
        assert "I'll fix that now" in result

    def test_assistant_with_single_tool(self):
        msg = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "read_file", "arguments": '{"path": "agent.py"}'}}
            ],
        }
        result = _summarize_message(msg)
        assert "read_file" in result
        assert "path=agent.py" in result

    def test_assistant_tool_order_preserved(self):
        msg = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "read_file", "arguments": '{"path": "a.py"}'}},
                {
                    "function": {
                        "name": "edit_file",
                        "arguments": '{"path": "b.py", "content": "x"}',
                    }
                },
                {"function": {"name": "bash", "arguments": '{"command": "pytest"}'}},
            ],
        }
        result = _summarize_message(msg)
        assert "read_file" in result
        assert "edit_file" in result
        assert "bash" in result
        parts = result.split(" → ")
        assert len(parts) == 3
        assert "read_file" in parts[0]
        assert "edit_file" in parts[1]
        assert "bash" in parts[2]

    def test_tool_result_normal(self):
        msg = {
            "role": "tool",
            "content": "file content here",
            "tool_name": "read_file",
            "tool_call_id": "abc123",
        }
        result = _summarize_message(msg)
        assert "Tool result" in result
        assert "abc123" in result
        assert "read_file" in result

    def test_tool_result_error(self):
        msg = {
            "role": "tool",
            "content": "ERROR: file not found",
            "tool_name": "read_file",
        }
        result = _summarize_message(msg)
        assert "Tool error" in result
        assert "ERROR:" in result

    def test_tool_result_with_external_refs(self):
        msg = {
            "role": "tool",
            "content": "see https://example.com/paper for details",
            "tool_call_id": "ref1",
        }
        result = _summarize_message(msg)
        assert "External refs" in result
        assert "https://example.com/paper" in result

    def test_tool_empty_content(self):
        msg = {"role": "tool", "content": "", "tool_name": "bash"}
        assert _summarize_message(msg) == ""

    def test_unknown_role(self):
        msg = {"role": "system", "content": "be helpful"}
        assert _summarize_message(msg) == ""


class TestMergeSummary:
    def test_empty_previous_and_messages(self):
        assert _merge_summary("", [], max_chars=1000) == ""

    def test_with_new_messages_only(self):
        messages = [{"role": "user", "content": "hello"}]
        result = _merge_summary("", messages, max_chars=1000)
        assert "Recently compacted history" in result
        assert "hello" in result

    def test_with_previous_summary(self):
        prev = "Earlier: did some work"
        messages = [{"role": "user", "content": "new task"}]
        result = _merge_summary(prev, messages, max_chars=1000)
        assert "Earlier summarized context" in result
        assert "Earlier: did some work" in result
        assert "new task" in result

    def test_overflow_truncates_old_keeps_new(self):
        prev = "A" * 500
        messages = [{"role": "user", "content": "IMPORTANT NEW TASK"}]
        result = _merge_summary(prev, messages, max_chars=300)
        assert "IMPORTANT NEW TASK" in result

    def test_overflow_with_large_old_and_new(self):
        prev = "X" * 1000
        messages = [{"role": "user", "content": "Y" * 500}]
        result = _merge_summary(prev, messages, max_chars=600)
        assert "Y" in result
        assert len(result) <= 600

    def test_no_entries_no_crash(self):
        messages = [{"role": "tool", "content": ""}]
        result = _merge_summary("old context", messages, max_chars=200)
        assert "old context" in result


class TestCompactHistory:
    def test_no_compaction_needed(self):
        messages = [{"role": "user", "content": "hi"}]
        summary, kept = compact_history(
            messages=messages,
            previous_summary="old",
            recent_message_count=5,
            max_chars=4000,
        )
        assert summary == "old"
        assert kept == messages

    def test_compaction_splits_correctly(self):
        messages = [{"role": "user", "content": f"msg{i}"} for i in range(10)]
        summary, kept = compact_history(
            messages=messages,
            previous_summary="prev",
            recent_message_count=3,
            max_chars=4000,
        )
        assert len(kept) == 3
        assert kept[0]["content"] == "msg7"
        assert "prev" in summary
        assert "Recently compacted" in summary

    def test_compaction_preserves_tool_args_in_summary(self):
        messages = [
            {"role": "user", "content": "fix agent.py"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "src/agent.py"}',
                        }
                    }
                ],
            },
            {
                "role": "tool",
                "content": "file contents...",
                "tool_name": "read_file",
                "tool_call_id": "t1",
            },
        ]
        summary, kept = compact_history(
            messages=messages,
            previous_summary="",
            recent_message_count=1,
            max_chars=4000,
        )
        assert "path=src/agent.py" in summary
        assert "read_file" in summary

    def test_multiple_compaction_rounds_preserve_latest(self):
        messages_round1 = [
            {"role": "user", "content": "first task"},
            {"role": "assistant", "content": "done"},
        ]
        summary1, _ = compact_history(
            messages=messages_round1,
            previous_summary="",
            recent_message_count=1,
            max_chars=4000,
        )

        messages_round2 = [
            {"role": "user", "content": "second task"},
            {"role": "assistant", "content": "done too"},
        ]
        summary2, _ = compact_history(
            messages=messages_round2,
            previous_summary=summary1,
            recent_message_count=1,
            max_chars=4000,
        )
        assert "second task" in summary2

    def test_multiple_compaction_rounds_latest_not_truncated_by_old(self):
        old_line = "very old context that takes space " * 50
        summary = ""
        for i in range(5):
            messages = [
                {"role": "user", "content": f"round {i} important info: {old_line}"},
                {"role": "assistant", "content": f"done round {i}"},
            ]
            summary, _ = compact_history(
                messages=messages,
                previous_summary=summary,
                recent_message_count=1,
                max_chars=800,
            )
        assert "round 4" in summary


class TestClearStaleToolResults:
    def _tool_msg(self, content: str, i: int) -> dict:
        return {
            "role": "tool",
            "content": content,
            "tool_call_id": f"tc{i}",
            "tool_name": f"tool_{i}",
        }

    def _user_msg(self, content: str) -> dict:
        return {"role": "user", "content": content}

    def _assistant_msg(self, content: str = "") -> dict:
        return {"role": "assistant", "content": content}

    def test_no_truncation_when_within_limit(self):
        messages = [
            self._user_msg("hi"),
            self._assistant_msg(),
            self._tool_msg("x" * 100, 1),
        ]
        result = clear_stale_tool_results(messages, keep_recent=8)
        assert result[2]["content"] == "x" * 100

    def test_stale_tool_results_truncated(self):
        long_content = "A" * 500
        messages = [
            self._tool_msg(long_content, 1),
            self._tool_msg(long_content, 2),
            self._tool_msg(long_content, 3),
            self._tool_msg(long_content, 4),
        ]
        result = clear_stale_tool_results(messages, keep_recent=2, truncate_to=50)
        assert result[0]["content"].startswith("[Result truncated: 500 chars]")
        assert result[1]["content"].startswith("[Result truncated: 500 chars]")
        assert result[2]["content"] == long_content
        assert result[3]["content"] == long_content

    def test_short_tool_results_untouched(self):
        messages = [
            self._tool_msg("short", 1),
            self._tool_msg("tiny", 2),
        ]
        result = clear_stale_tool_results(messages, keep_recent=1, truncate_to=50)
        assert result[0]["content"] == "short"
        assert result[1]["content"] == "tiny"

    def test_keep_recent_boundary(self):
        long = "B" * 500
        messages = [self._tool_msg(long, i) for i in range(5)]
        result = clear_stale_tool_results(messages, keep_recent=3)
        assert result[0]["content"].startswith("[Result truncated:")
        assert result[1]["content"].startswith("[Result truncated:")
        assert result[2]["content"] == long
        assert result[3]["content"] == long
        assert result[4]["content"] == long

    def test_empty_messages(self):
        result = clear_stale_tool_results([])
        assert result == []

    def test_no_tool_messages(self):
        messages = [self._user_msg("hi"), self._assistant_msg("ok")]
        result = clear_stale_tool_results(messages, keep_recent=2)
        assert result == messages

    def test_non_string_content_ignored(self):
        messages = [
            {"role": "tool", "content": None, "tool_call_id": "t1"},
            {"role": "tool", "content": 123, "tool_call_id": "t2"},
        ]
        result = clear_stale_tool_results(messages, keep_recent=0, truncate_to=10)
        assert result[0]["content"] is None
        assert result[1]["content"] == 123

    def test_truncated_placeholder_includes_length(self):
        long = "C" * 1000
        messages = [self._tool_msg(long, 1)]
        result = clear_stale_tool_results(messages, keep_recent=0, truncate_to=50)
        assert "[Result truncated: 1000 chars]" in result[0]["content"]

    def test_other_roles_untouched(self):
        messages = [
            self._user_msg("D" * 500),
            self._assistant_msg("E" * 500),
            self._tool_msg("F" * 500, 1),
        ]
        result = clear_stale_tool_results(messages, keep_recent=0, truncate_to=50)
        assert result[0]["content"] == "D" * 500
        assert result[1]["content"] == "E" * 500
