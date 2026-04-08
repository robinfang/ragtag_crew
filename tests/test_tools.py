from __future__ import annotations

import asyncio
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from ragtag_crew.config import settings
from ragtag_crew.tools import Tool, _ALL_TOOLS, get_tools_for_preset, register_tool
from ragtag_crew.tools.path_utils import resolve_path, resolve_read_path

import ragtag_crew.tools.file_tools  # noqa: F401
import ragtag_crew.tools.search_tools as search_tools
import ragtag_crew.tools.shell_tools as shell_tools  # noqa: F401


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

    def test_resolve_read_path_allows_absolute_outside_working_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "work"
            outside = Path(tmp) / "other"
            base.mkdir()
            outside.mkdir()

            with working_dir(base):
                resolved = resolve_read_path(str(outside / "file.txt"))
                self.assertEqual(resolved, outside / "file.txt")

    def test_resolve_read_path_anchors_relative_to_working_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inner = root / "nested" / "file.txt"

            with working_dir(root):
                resolved = resolve_read_path("nested/file.txt")

            self.assertEqual(resolved, inner)

    def test_resolve_path_still_blocks_absolute_outside(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "work"
            outside = Path(tmp) / "other"
            base.mkdir()
            outside.mkdir()

            with working_dir(base):
                with self.assertRaises(PermissionError):
                    resolve_path(str(outside / "secrets.txt"))


class ToolPresetTests(unittest.TestCase):
    def tearDown(self) -> None:
        _ALL_TOOLS.pop("external_demo", None)

    def test_unknown_preset_raises_key_error(self) -> None:
        with self.assertRaises(KeyError):
            get_tools_for_preset("unknown")

    def test_readonly_preset_contains_only_safe_tools(self) -> None:
        tools = get_tools_for_preset("readonly")
        self.assertEqual(
            [tool.name for tool in tools],
            ["read", "grep", "find", "ls", "memory_search"],
        )

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
            (root / ".venv" / "hidden.py").write_text(
                "print('hidden')\n", encoding="utf-8"
            )
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
            (root / "app.py").write_text(
                "def main():\n    return 1\n", encoding="utf-8"
            )
            (root / "notes.txt").write_text("def main in text\n", encoding="utf-8")

            with working_dir(root):
                with patch(
                    "ragtag_crew.tools.search_tools._get_rg_path", return_value=None
                ):
                    result = await search_tools._grep_search("def main", ".", "*.py")

            self.assertIn("app.py:1: def main():", result)
            self.assertNotIn("notes.txt", result)

    async def test_find_with_rg_uses_system_rg(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "hello.py").write_text("# test\n", encoding="utf-8")

            with working_dir(root):
                with patch(
                    "ragtag_crew.tools.search_tools._get_rg_path",
                    return_value="/usr/bin/rg",
                ):
                    mock_instance = await self._create_mock_proc(b"hello.py\n", b"")
                    with patch(
                        "ragtag_crew.tools.search_tools.asyncio.create_subprocess_exec",
                        return_value=mock_instance,
                    ):
                        await search_tools._find_files("*.py", ".")

    async def test_grep_with_rg_falls_back_on_file_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("import os\n", encoding="utf-8")

            with working_dir(root):
                with patch(
                    "ragtag_crew.tools.search_tools._get_rg_path",
                    return_value="/nonexistent/rg",
                ):
                    with patch(
                        "ragtag_crew.tools.search_tools.asyncio.create_subprocess_exec",
                        side_effect=FileNotFoundError,
                    ):
                        result = await search_tools._grep_search("import", ".", "*.py")

            self.assertIn("app.py:1:", result)

    async def test_find_with_rg_falls_back_when_rg_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "test.py").write_text("# test\n", encoding="utf-8")

            with working_dir(root):
                with patch(
                    "ragtag_crew.tools.search_tools._get_rg_path", return_value=None
                ):
                    result = await search_tools._find_files("*.py", ".")

            self.assertIn("test.py", result)

    @staticmethod
    async def _create_mock_proc(stdout: bytes, stderr: bytes):
        mock = unittest.mock.AsyncMock()
        mock.communicate.return_value = (stdout, stderr)
        mock.returncode = 0
        return mock


if __name__ == "__main__":
    unittest.main()


import ragtag_crew.tools.file_tools as file_tools  # noqa: E402


class ShellDeleteBlockTests(unittest.TestCase):
    def test_bash_blocks_rm_command(self) -> None:
        self.assertIn("ERROR", shell_tools._check_delete_attempt("rm file.txt"))

    def test_bash_blocks_del_command(self) -> None:
        self.assertIn("ERROR", shell_tools._check_delete_attempt("del file.txt"))

    def test_bash_blocks_rmdir_command(self) -> None:
        self.assertIn("ERROR", shell_tools._check_delete_attempt("rmdir empty_dir"))

    def test_bash_blocks_remove_item(self) -> None:
        self.assertIn(
            "ERROR", shell_tools._check_delete_attempt("Remove-Item file.txt")
        )

    def test_bash_blocks_rm_rf(self) -> None:
        self.assertIn("ERROR", shell_tools._check_delete_attempt("rm -rf /tmp/stuff"))

    def test_bash_allows_non_delete_commands(self) -> None:
        self.assertIsNone(shell_tools._check_delete_attempt("echo hello"))
        self.assertIsNone(shell_tools._check_delete_attempt("git status"))
        self.assertIsNone(shell_tools._check_delete_attempt("pip install requests"))
        self.assertIsNone(shell_tools._check_delete_attempt("python main.py"))

    def test_bash_allows_program_with_rm_in_name(self) -> None:
        self.assertIsNone(shell_tools._check_delete_attempt("farm --help"))
        self.assertIsNone(shell_tools._check_delete_attempt("python alarm.py"))


class DeleteFileTests(unittest.IsolatedAsyncioTestCase):
    async def test_delete_file_removes_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "to_delete.txt"
            target.write_text("bye", encoding="utf-8")

            with working_dir(root):
                result = await file_tools._delete_file("to_delete.txt")

            self.assertIn("OK", result)
            self.assertFalse(target.exists())

    async def test_delete_file_returns_error_for_nonexistent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with working_dir(Path(tmp)):
                result = await file_tools._delete_file("nope.txt")

            self.assertIn("ERROR", result)
            self.assertIn("not found", result)

    async def test_delete_file_blocks_path_outside_working_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "work"
            outside = Path(tmp) / "other"
            base.mkdir()
            outside.mkdir()
            (outside / "secret.txt").write_text("secret", encoding="utf-8")

            with working_dir(base):
                result = await file_tools._delete_file(str(outside / "secret.txt"))

            self.assertIn("ERROR", result)
            self.assertTrue((outside / "secret.txt").exists())

    async def test_delete_file_removes_empty_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "empty_dir"
            target.mkdir()

            with working_dir(root):
                result = await file_tools._delete_file("empty_dir")

            self.assertIn("OK", result)
            self.assertFalse(target.exists())

    async def test_delete_file_refuses_nonempty_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "full_dir"
            target.mkdir()
            (target / "child.txt").write_text("x", encoding="utf-8")

            with working_dir(root):
                result = await file_tools._delete_file("full_dir")

            self.assertIn("ERROR", result)
            self.assertIn("not empty", result)
            self.assertTrue(target.exists())
