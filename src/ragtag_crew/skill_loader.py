"""Local Markdown skill discovery and prompt rendering."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ragtag_crew.config import settings


@dataclass(frozen=True)
class SkillDefinition:
    """A local Markdown skill file."""

    name: str
    path: Path
    content: str
    summary: str


def _skills_dir() -> Path:
    return Path(settings.skills_dir).resolve()


def _normalize_skill_name(name: str) -> str:
    return name.strip().lower()


def list_skills() -> list[SkillDefinition]:
    """List all local Markdown skills under ``skills_dir``."""
    root = _skills_dir()
    if not root.exists():
        return []

    skills: list[SkillDefinition] = []
    for path in sorted(root.glob("*.md"), key=lambda item: item.name.lower()):
        if path.name.lower() == "readme.md":
            continue
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            continue

        summary = ""
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            summary = stripped
            break

        skills.append(
            SkillDefinition(
                name=path.stem,
                path=path,
                content=content,
                summary=summary,
            )
        )

    return skills


def get_skill(name: str) -> SkillDefinition:
    """Look up one skill by file stem."""
    target = _normalize_skill_name(name)
    for skill in list_skills():
        if _normalize_skill_name(skill.name) == target:
            return skill
    raise KeyError(f"Unknown skill: {name}")


def render_skill_prompt(skill_names: list[str]) -> str:
    """Render active skills into one prompt block."""
    parts: list[str] = []
    for name in skill_names:
        skill = get_skill(name)
        parts.append(f"## Skill: {skill.name}\n{skill.content}")
    return "\n\n".join(parts)
