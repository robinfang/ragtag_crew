"""Shared path helpers for sandboxed tool access."""

from __future__ import annotations

from pathlib import Path

from ragtag_crew.config import settings


def get_working_dir() -> Path:
    """Return the resolved working directory for all tools."""
    return Path(settings.working_dir).resolve()


def resolve_path(raw: str) -> Path:
    """Resolve a user path and ensure it stays inside the working directory."""
    base = get_working_dir()
    candidate = Path(raw).expanduser()
    resolved = candidate.resolve() if candidate.is_absolute() else (base / candidate).resolve()

    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise PermissionError(f"Access denied: {raw} is outside working directory") from exc

    return resolved


def resolve_read_path(raw: str) -> Path:
    """Resolve a path for read-only operations.

    Relative paths are anchored to the working directory (same as resolve_path).
    Absolute paths are resolved directly without sandbox checks.
    """
    base = get_working_dir()
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (base / candidate).resolve()


def display_path(path: Path) -> str:
    """Render a path relative to the working directory when possible."""
    base = get_working_dir()
    try:
        rel = path.relative_to(base)
    except ValueError:
        return str(path)
    return "." if not rel.parts else rel.as_posix()
