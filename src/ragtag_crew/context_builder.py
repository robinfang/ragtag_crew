"""Assemble stable and dynamic context layers for model calls."""

from __future__ import annotations

from pathlib import Path

from ragtag_crew.config import settings
from ragtag_crew.skill_loader import render_skill_prompt


def _read_optional_markdown(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _root_file(path_str: str) -> Path:
    return Path(path_str).resolve()


def load_project_context() -> str:
    return _read_optional_markdown(_root_file(settings.project_context_file))


def load_user_context() -> str:
    return _read_optional_markdown(_root_file(settings.user_context_file))


def load_memory_index() -> str:
    return _read_optional_markdown(_root_file(settings.memory_index_file))


def _append_section(parts: list[str], title: str, content: str) -> None:
    content = content.strip()
    if content:
        parts.append(f"## {title}\n{content}")


def build_system_prompt(
    *,
    base_system_prompt: str,
    enabled_skills: list[str] | None = None,
    session_prompt: str = "",
    session_summary: str = "",
) -> str:
    """Build the final system prompt for one model call."""
    parts: list[str] = []

    base_system_prompt = base_system_prompt.strip()
    if base_system_prompt:
        parts.append(base_system_prompt)

    _append_section(parts, "Project Context", load_project_context())
    _append_section(parts, "User Context", load_user_context())
    _append_section(parts, "Long-term Memory Index", load_memory_index())

    if enabled_skills:
        skill_prompt = render_skill_prompt(enabled_skills)
        _append_section(parts, "Active Skills", skill_prompt)

    _append_section(
        parts,
        "External Result Policy",
        (
            "Treat search, MCP, platform, and API tool results as working evidence rather than permanent truth. "
            "Prefer citing concrete URLs, file paths, and tool names in your reasoning. "
            "Only promote stable conclusions into memory when the user asks or when the information is clearly long-lived project knowledge."
        ),
    )

    _append_section(parts, "Session Prompt", session_prompt)
    _append_section(parts, "Session Summary", session_summary)
    return "\n\n".join(parts).strip()
