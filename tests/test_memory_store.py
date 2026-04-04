from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from ragtag_crew.config import settings
from ragtag_crew.memory_store import (
    append_memory_note,
    list_memory_files,
    promote_inbox,
    read_memory_file,
    read_memory_index,
)


@contextmanager
def memory_paths(root: Path):
    original_index = settings.memory_index_file
    original_dir = settings.memory_dir
    settings.memory_index_file = str(root / "MEMORY.md")
    settings.memory_dir = str(root / "memory")
    try:
        yield
    finally:
        settings.memory_index_file = original_index
        settings.memory_dir = original_dir


class MemoryStoreTests(unittest.TestCase):
    def test_read_memory_index_returns_file_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "MEMORY.md").write_text("short memory index", encoding="utf-8")
            with memory_paths(root):
                content = read_memory_index()

        self.assertEqual(content, "short memory index")

    def test_list_memory_files_ignores_readme(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_root = root / "memory"
            memory_root.mkdir()
            (memory_root / "README.md").write_text("docs", encoding="utf-8")
            (memory_root / "preferences.md").write_text("prefs", encoding="utf-8")
            with memory_paths(root):
                files = list_memory_files()

        self.assertEqual(files, ["preferences.md"])

    def test_append_memory_note_writes_inbox_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with memory_paths(root):
                path = append_memory_note("remember this detail")
                content = path.read_text(encoding="utf-8")

        self.assertEqual(path.name, "inbox.md")
        self.assertIn("remember this detail", content)
        self.assertTrue(content.startswith("# Inbox"))

    def test_read_memory_file_reads_named_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_root = root / "memory"
            memory_root.mkdir()
            (memory_root / "preferences.md").write_text("saved preferences", encoding="utf-8")
            with memory_paths(root):
                content = read_memory_file("preferences")

        self.assertEqual(content, "saved preferences")

    def test_promote_inbox_to_index_moves_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "MEMORY.md").write_text("# Memory Index\n", encoding="utf-8")
            with memory_paths(root):
                append_memory_note("first note")
                append_memory_note("second note")
                path, count = promote_inbox()
                index_content = (root / "MEMORY.md").read_text(encoding="utf-8")
                inbox_content = (root / "memory" / "inbox.md").read_text(encoding="utf-8")

        self.assertEqual(path.name, "MEMORY.md")
        self.assertEqual(count, 2)
        self.assertIn("first note", index_content)
        self.assertIn("second note", index_content)
        self.assertEqual(inbox_content, "# Inbox\n\n")

    def test_promote_inbox_to_named_file_creates_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with memory_paths(root):
                append_memory_note("decision note")
                path, count = promote_inbox("project-decisions")
                target_content = path.read_text(encoding="utf-8")

        self.assertEqual(path.name, "project-decisions.md")
        self.assertEqual(count, 1)
        self.assertIn("# Project Decisions", target_content)
        self.assertIn("decision note", target_content)


if __name__ == "__main__":
    unittest.main()
