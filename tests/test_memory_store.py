from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from ragtag_crew.config import settings
from ragtag_crew.memory_store import (
    MemoryHit,
    append_memory_note,
    append_memory_note_if_missing,
    list_memory_files,
    promote_inbox,
    read_memory_file,
    read_memory_index,
    search_memory,
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

    def test_append_memory_note_if_missing_deduplicates_inbox(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with memory_paths(root):
                first_path, first_added = append_memory_note_if_missing("same note")
                second_path, second_added = append_memory_note_if_missing("same note")
                inbox = (root / "memory" / "inbox.md").read_text(encoding="utf-8")

        self.assertEqual(first_path.name, "inbox.md")
        self.assertEqual(second_path.name, "inbox.md")
        self.assertTrue(first_added)
        self.assertFalse(second_added)
        self.assertEqual(inbox.count("same note"), 1)

    def test_append_memory_note_if_missing_deduplicates_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "MEMORY.md").write_text(
                "# Memory\n\nexisting note", encoding="utf-8"
            )
            with memory_paths(root):
                path, added = append_memory_note_if_missing("existing note")

        self.assertEqual(path.name, "MEMORY.md")
        self.assertFalse(added)

    def test_append_memory_note_if_missing_matches_exact_note_not_substring(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with memory_paths(root):
                append_memory_note("python packaging refs: https://example.com/a")
                path, added = append_memory_note_if_missing("python packaging")
                inbox = (root / "memory" / "inbox.md").read_text(encoding="utf-8")

        self.assertEqual(path.name, "inbox.md")
        self.assertTrue(added)
        self.assertEqual(inbox.count("python packaging"), 2)

    def test_append_memory_note_if_missing_checks_named_memory_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_root = root / "memory"
            memory_root.mkdir()
            (memory_root / "project-decisions.md").write_text(
                "# Project Decisions\n\n- 2026-04-09 12:00:00 saved external note\n",
                encoding="utf-8",
            )
            with memory_paths(root):
                path, added = append_memory_note_if_missing("saved external note")

        self.assertEqual(path.name, "project-decisions.md")
        self.assertFalse(added)

    def test_read_memory_file_reads_named_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_root = root / "memory"
            memory_root.mkdir()
            (memory_root / "preferences.md").write_text(
                "saved preferences", encoding="utf-8"
            )
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
                inbox_content = (root / "memory" / "inbox.md").read_text(
                    encoding="utf-8"
                )

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

    def test_search_memory_finds_index_and_named_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_root = root / "memory"
            memory_root.mkdir()
            (root / "MEMORY.md").write_text(
                "# Memory\n\npython packaging", encoding="utf-8"
            )
            (memory_root / "preferences.md").write_text(
                "python style note", encoding="utf-8"
            )
            with memory_paths(root):
                hits = search_memory("python")

        self.assertGreaterEqual(len(hits), 2)
        self.assertTrue(all(isinstance(hit, MemoryHit) for hit in hits))
        self.assertEqual(hits[0].file_name, "MEMORY.md")

    def test_search_memory_limit_applies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_root = root / "memory"
            memory_root.mkdir()
            (root / "MEMORY.md").write_text("alpha\nbeta\ngamma", encoding="utf-8")
            (memory_root / "preferences.md").write_text(
                "alpha\nalpha", encoding="utf-8"
            )
            with memory_paths(root):
                hits = search_memory("a", limit=2)

        self.assertEqual(len(hits), 2)

    def test_search_memory_raises_for_empty_query(self) -> None:
        with self.assertRaises(ValueError):
            search_memory("   ")

    def test_search_memory_returns_empty_when_no_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "MEMORY.md").write_text("short memory index", encoding="utf-8")
            with memory_paths(root):
                hits = search_memory("missing")

        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
