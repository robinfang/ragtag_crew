"""Generate a lightweight workspace snapshot for system prompt injection."""

from __future__ import annotations

import os
import time
from pathlib import Path

from ragtag_crew.config import settings

_cache_timestamp: float = 0.0
_cache_result: str = ""
_CACHE_TTL = 60

_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "dist",
        "build",
        "target",
        "htmlcov",
        ".eggs",
        ".next",
        ".nuxt",
        "coverage",
    }
)

_TECH_FILES: list[tuple[str, str]] = [
    ("pyproject.toml", "Python"),
    ("setup.py", "Python"),
    ("requirements.txt", "Python"),
    ("package.json", "Node.js"),
    ("Cargo.toml", "Rust"),
    ("go.mod", "Go"),
    ("Makefile", "Make"),
    ("Dockerfile", "Docker"),
    ("docker-compose.yml", "Docker Compose"),
    ("docker-compose.yaml", "Docker Compose"),
]


def load_workspace_snapshot() -> str:
    global _cache_timestamp, _cache_result

    if not settings.env_bootstrap_enabled:
        return ""

    root = Path(settings.working_dir).resolve()
    if not root.is_dir():
        return ""

    now = time.monotonic()
    if now - _cache_timestamp < _CACHE_TTL and _cache_result:
        return _cache_result

    _cache_result = _build_snapshot(root)
    _cache_timestamp = now
    return _cache_result


def _build_snapshot(root: Path) -> str:
    max_chars = settings.env_bootstrap_max_tokens * 4
    skip_dirs = {
        d.strip() for d in settings.env_bootstrap_skip_dirs.split(",") if d.strip()
    }

    parts: list[str] = []

    tree = _scan_tree(root, settings.env_bootstrap_max_depth, skip_dirs)
    if tree:
        parts.append(tree)

    tech = _detect_tech_stack(root)
    if tech:
        parts.append(tech)

    result = "\n\n".join(parts)
    if len(result) > max_chars:
        result = result[: max_chars - 3].rstrip() + "..."
    return result


def _scan_tree(root: Path, max_depth: int, skip_dirs: set[str]) -> str:
    lines: list[str] = []

    def _walk(current: Path, depth: int, prefix: str) -> None:
        if depth > max_depth:
            return
        try:
            entries = sorted(os.scandir(current), key=lambda e: e.name.lower())
        except (PermissionError, OSError):
            return

        dirs: list[os.DirEntry] = []
        files: list[os.DirEntry] = []
        for entry in entries:
            if entry.name.startswith(".") and entry.name != ".env":
                continue
            if entry.is_dir(follow_symlinks=False):
                if entry.name not in skip_dirs:
                    dirs.append(entry)
            else:
                files.append(entry)

        all_entries = dirs + files
        for i, entry in enumerate(all_entries):
            is_last = i == len(all_entries) - 1
            connector = "└── " if is_last else "├── "
            if entry.is_dir(follow_symlinks=False):
                lines.append(f"{prefix}{connector}{entry.name}/")
                extension = "    " if is_last else "│   "
                _walk(Path(entry.path), depth + 1, prefix + extension)
            else:
                lines.append(f"{prefix}{connector}{entry.name}")

    lines.append(f"{root.name}/")
    _walk(root, 1, "")
    return "\n".join(lines)


def _detect_tech_stack(root: Path) -> str:
    hints: list[str] = []

    for filename, label in _TECH_FILES:
        path = root / filename
        if not path.is_file():
            continue
        snippet = ""
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")[:200]
            snippet = " ".join(raw.split())
        except (PermissionError, OSError):
            snippet = "(unreadable)"
        hints.append(f"- **{label}** (`{filename}`): {snippet}")

    if not hints:
        return ""

    return "Detected tech stack:\n" + "\n".join(hints)
