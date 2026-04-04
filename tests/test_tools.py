from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from ragtag_crew.config import settings
from ragtag_crew.tools import Tool, _ALL_TOOLS, get_tools_for_preset, register_tool
from ragtag_crew.tools.path_utils import resolve_path

import ragtag_crew.tools.file_tools  # noqa: F401
import ragtag_crew.tools.search_tools as search_tools
import ragtag_crew.tools.shell_tools  # noqa: F401


@contextmanager
def working_dir(path: Path):
    original = settings.working_dir
    settings.working_dir = str(path)
    try:
        yield
    finally:
        settings.working_dir = original


class PathSandboxTests(unittest.TestCase):
    def test_resolve_path_allows_paths_inside_working_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inner = root / "nested" / "file.txt"

            with working_dir(root):
                resolved = resolve_path("nested/file.txt")

            self.assertEqual(resolved, inner)

    def test_resolve_path_blocks_parent_escape_even_with_shared_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "work"
            sibling = Path(tmp) / "work-evil"
            base.mkdir()
            sibling.mkdir()

            with working_dir(base):
                with self.assertRaises(PermissionError):
                    resolve_path("../work-evil/secrets.txt")


class ToolPresetTests(unittest.TestCase):
    def tearDown(self) -> None:
        _ALL_TOOLS.pop("external_demo", None)

    def test_unknown_preset_raises_key_error(self) -> None:
        with self.assertRaises(KeyError):
            get_tools_for_preset("unknown")

    def test_readonly_preset_contains_only_safe_tools(self) -> None:
        tools = get_tools_for_preset("readonly")
        self.assertEqual([tool.name for tool in tools], ["read", "grep", "find", "ls"])

    def test_dynamic_tool_can_join_preset_via_metadata(self) -> None:
        async def _execute() -> str:
            return "ok"

        register_tool(
            Tool(
                name="external_demo",
                description="Demo external tool",
                parameters={"type": "object", "properties": {}},
                execute=_execute,
                source_type="mcp",
                source_name="demo",
                enabled_in_presets=("coding",),
            )
        )

        tools = get_tools_for_preset("coding")
        self.assertIn("external_demo", [tool.name for tool in tools])


class SearchToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_find_and_ls_skip_internal_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "visible").mkdir()
            (root / "visible" / "main.py").write_text("print('ok')\n", encoding="utf-8")
            (root / ".venv").mkdir()
            (root / ".venv" / "hidden.py").write_text("print('hidden')\n", encoding="utf-8")
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "cached.pyc").write_bytes(b"x")

            with working_dir(root):
                listed = await search_tools._list_dir(".")
                found = await search_tools._find_files("*.py", ".")

            self.assertIn("visible/", listed)
            self.assertNotIn(".venv/", listed)
            self.assertNotIn("__pycache__/", listed)
            self.assertIn("visible/main.py", found)
            self.assertNotIn("hidden.py", found)

    async def test_grep_python_fallback_respects_include_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
            (root / "notes.txt").write_text("def main in text\n", encoding="utf-8")

            with working_dir(root):
                with patch("ragtag_crew.tools.search_tools._grep_with_rg", return_value=None):
                    result = await search_tools._grep_search("def main", ".", "*.py")

            self.assertIn("app.py:1: def main():", result)
            self.assertNotIn("notes.txt", result)


if __name__ == "__main__":
    unittest.main()
