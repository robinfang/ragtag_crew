from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from ragtag_crew.config import settings
from ragtag_crew.context_builder import build_system_prompt


@contextmanager
def context_files(root: Path):
    original_project = settings.project_context_file
    original_user = settings.user_context_file
    original_memory = settings.memory_index_file
    original_skills = settings.skills_dir
    settings.project_context_file = str(root / "PROJECT.md")
    settings.user_context_file = str(root / "USER.local.md")
    settings.memory_index_file = str(root / "MEMORY.md")
    settings.skills_dir = str(root / "skills")
    try:
        yield
    finally:
        settings.project_context_file = original_project
        settings.user_context_file = original_user
        settings.memory_index_file = original_memory
        settings.skills_dir = original_skills


class ContextBuilderTests(unittest.TestCase):
    def test_build_system_prompt_includes_layers_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "PROJECT.md").write_text("project rules", encoding="utf-8")
            (root / "USER.local.md").write_text("user preferences", encoding="utf-8")
            (root / "MEMORY.md").write_text("memory index", encoding="utf-8")
            skills_root = root / "skills"
            skills_root.mkdir()
            (skills_root / "review.md").write_text("# Review\n\nfind bugs first", encoding="utf-8")

            with context_files(root):
                prompt = build_system_prompt(
                    base_system_prompt="base system",
                    enabled_skills=["review"],
                    session_prompt="answer concisely",
                    session_summary="worked on context layering",
                )

        self.assertLess(prompt.index("base system"), prompt.index("## Planning Protocol"))
        self.assertLess(prompt.index("## Planning Protocol"), prompt.index("## Project Context"))
        self.assertLess(prompt.index("## Project Context"), prompt.index("## User Context"))
        self.assertLess(prompt.index("## User Context"), prompt.index("## Long-term Memory Index"))
        self.assertLess(prompt.index("## Long-term Memory Index"), prompt.index("## Active Skills"))
        self.assertLess(prompt.index("## Active Skills"), prompt.index("## External Result Policy"))
        self.assertLess(prompt.index("## External Result Policy"), prompt.index("## Session Prompt"))
        self.assertLess(prompt.index("## Session Prompt"), prompt.index("## Session Summary"))

    def test_build_system_prompt_skips_missing_optional_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with context_files(root):
                prompt = build_system_prompt(base_system_prompt="base system")

        self.assertTrue(prompt.startswith("base system"))
        self.assertIn("## External Result Policy", prompt)

    def test_planning_protocol_included_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with context_files(root):
                prompt = build_system_prompt(base_system_prompt="base", planning_enabled=True)

        self.assertIn("## Planning Protocol", prompt)
        self.assertIn("numbered plan", prompt)

    def test_planning_protocol_skipped_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with context_files(root):
                prompt = build_system_prompt(base_system_prompt="base", planning_enabled=False)

        self.assertNotIn("## Planning Protocol", prompt)


if __name__ == "__main__":
    unittest.main()
