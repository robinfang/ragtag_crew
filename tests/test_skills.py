from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from ragtag_crew.agent import AgentSession
from ragtag_crew.config import settings
from ragtag_crew.skill_loader import get_skill, list_skills
from ragtag_crew.tools import Tool


async def _noop_tool(**_: str) -> str:
    return "ok"


@contextmanager
def skills_dir(path: Path):
    original = settings.skills_dir
    settings.skills_dir = str(path)
    try:
        yield
    finally:
        settings.skills_dir = original


class SkillLoaderTests(unittest.TestCase):
    def test_list_skills_ignores_readme_and_reads_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("# docs\n", encoding="utf-8")
            (root / "review.md").write_text(
                "# Review\n\nFocus on risks first.\nMore details.",
                encoding="utf-8",
            )

            with skills_dir(root):
                skills = list_skills()

        self.assertEqual([skill.name for skill in skills], ["review"])
        self.assertEqual(skills[0].summary, "Focus on risks first.")

    def test_agent_build_messages_includes_active_skill_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "review.md").write_text(
                "# Review\n\nFocus on bugs first.",
                encoding="utf-8",
            )

            with skills_dir(root):
                session = AgentSession(
                    model="openai/GLM-5.1",
                    tools=[Tool("noop", "noop", {"type": "object"}, _noop_tool)],
                    system_prompt="base prompt",
                    enabled_skills=["review"],
                )
                messages = session._build_messages()

        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("base prompt", messages[0]["content"])
        self.assertIn("## Active Skills", messages[0]["content"])
        self.assertIn("Focus on bugs first.", messages[0]["content"])
        self.assertIn("review.md", messages[0]["content"])
        self.assertIn("Use the read tool", messages[0]["content"])
        self.assertNotIn("# Review", messages[0]["content"])

    def test_get_skill_raises_for_unknown_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with skills_dir(root):
                with self.assertRaises(KeyError):
                    get_skill("missing")


if __name__ == "__main__":
    unittest.main()
