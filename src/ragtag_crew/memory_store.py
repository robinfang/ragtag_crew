"""Lightweight local memory file helpers."""

from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from ragtag_crew.config import settings
from ragtag_crew.context_builder import load_memory_index

_TIMESTAMPED_NOTE_RE = re.compile(r"^-\s+\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\s+(.*)$")


def _memory_dir() -> Path:
    return Path(settings.memory_dir).resolve()


def _memory_inbox_path() -> Path:
    return _memory_dir() / "inbox.md"


def _memory_index_path() -> Path:
    return Path(settings.memory_index_file).resolve()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f"{path.stem}.",
        suffix=".tmp",
        text=True,
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_name, path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _title_from_stem(path: Path) -> str:
    return path.stem.replace("-", " ").replace("_", " ").title()


def _normalize_target_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValueError("Memory target is empty")
    if normalized.lower() in {"index", "memory", "memory.md"}:
        return "MEMORY.md"
    if "/" in normalized or "\\" in normalized:
        raise ValueError("Memory target must be a simple file name")
    if normalized.lower().endswith(".md"):
        return normalized
    return f"{normalized}.md"


def _resolve_memory_target(name: str) -> Path:
    normalized = _normalize_target_name(name)
    if normalized == "MEMORY.md":
        return _memory_index_path()

    path = (_memory_dir() / normalized).resolve()
    path.relative_to(_memory_dir())
    return path


def _append_entries(
    existing: str, entries: list[str], *, title: str | None = None
) -> str:
    existing = existing.strip()
    parts: list[str] = []
    if existing:
        parts.append(existing)
    elif title:
        parts.append(f"# {title}")

    parts.extend(entries)
    return "\n\n".join(parts).rstrip() + "\n"


def _read_inbox_entries() -> list[str]:
    path = _memory_inbox_path()
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("- ")
    ]


def _extract_normalized_notes(content: str) -> set[str]:
    notes: set[str] = set()
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if not line.startswith("#"):
            notes.add(line)
        match = _TIMESTAMPED_NOTE_RE.match(line)
        if match:
            notes.add(match.group(1).strip())
            continue
        if line.startswith("- "):
            notes.add(line[2:].strip())
    return notes


def _memory_note_exists(note: str) -> Path | None:
    candidates: list[Path] = [_memory_index_path()]
    memory_root = _memory_dir()
    if memory_root.exists():
        candidates.extend(
            path
            for path in sorted(
                memory_root.glob("*.md"), key=lambda item: item.name.lower()
            )
            if path.name.lower() != "readme.md"
        )

    for path in candidates:
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8")
        if note in _extract_normalized_notes(content):
            return path
    return None


def list_memory_files() -> list[str]:
    root = _memory_dir()
    if not root.exists():
        return []
    return [
        path.name
        for path in sorted(root.glob("*.md"), key=lambda item: item.name.lower())
        if path.name.lower() != "readme.md"
    ]


def read_memory_index() -> str:
    return load_memory_index()


def read_memory_file(name: str) -> str:
    path = _resolve_memory_target(name)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Memory file not found: {path.name}")
    return path.read_text(encoding="utf-8").strip()


def append_memory_note(note: str) -> Path:
    note = note.strip()
    if not note:
        raise ValueError("Memory note is empty")

    root = _memory_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = _memory_inbox_path()
    prefix = "# Inbox\n\n"
    existing = path.read_text(encoding="utf-8") if path.exists() else prefix
    if existing and not existing.endswith("\n"):
        existing += "\n"

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    content = f"{existing}- {timestamp} {note}\n"

    _atomic_write(path, content)

    return path


def append_memory_note_if_missing(note: str) -> tuple[Path, bool]:
    note = note.strip()
    if not note:
        raise ValueError("Memory note is empty")

    existing_path = _memory_note_exists(note)
    if existing_path is not None:
        return existing_path, False

    return append_memory_note(note), True


def promote_inbox(target_name: str = "MEMORY.md") -> tuple[Path, int]:
    entries = _read_inbox_entries()
    if not entries:
        raise ValueError("Memory inbox is empty")

    target = _resolve_memory_target(target_name)
    existing_target = target.read_text(encoding="utf-8") if target.exists() else ""
    content = _append_entries(existing_target, entries, title=_title_from_stem(target))
    _atomic_write(target, content)

    inbox = _memory_inbox_path()
    _atomic_write(inbox, "# Inbox\n\n")
    return target, len(entries)
