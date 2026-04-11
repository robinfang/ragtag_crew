"""Managed per-working-directory workspaces for temporary files and scripts."""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from ragtag_crew.config import settings
from ragtag_crew.tools.path_utils import display_path, get_working_dir, resolve_path

WORKSPACE_META_FILENAME = ".workspace.json"
WORKSPACE_KINDS = {"tmp", "script"}


@dataclass(slots=True)
class WorkspaceRecord:
    id: str
    kind: str
    purpose: str
    created_at: float
    last_used_at: float
    keep: bool
    primary_files: list[str]
    path: Path


def get_workspace_state_dir() -> Path:
    return resolve_path(settings.workspace_state_dir_name)


def get_workspace_root_dir() -> Path:
    return get_workspace_state_dir() / "workspaces"


def get_workspace_kind_dir(kind: str) -> Path:
    _validate_workspace_kind(kind)
    name = "scripts" if kind == "script" else "tmp"
    return get_workspace_root_dir() / name


def ensure_workspace_dirs() -> None:
    for kind in WORKSPACE_KINDS:
        get_workspace_kind_dir(kind).mkdir(parents=True, exist_ok=True)


def path_targets_workspace_state(path: Path) -> bool:
    state_dir = get_workspace_state_dir().resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(state_dir)
    except ValueError:
        return False
    return True


def is_workspace_metadata_path(path: Path) -> bool:
    return path.name == WORKSPACE_META_FILENAME and path_targets_workspace_state(path)


def is_script_path(path: str | Path) -> bool:
    ext = Path(path).suffix.lower()
    return bool(ext and ext in settings.get_workspace_script_extensions())


def create_workspace(
    kind: str,
    purpose: str = "",
    name_hint: str = "",
    *,
    now: float | None = None,
) -> WorkspaceRecord:
    ensure_workspace_dirs()
    _validate_workspace_kind(kind)

    timestamp = now or time.time()
    slug = _slugify(name_hint or purpose or kind)
    workspace_id = f"{kind}-{time.strftime('%Y%m%d-%H%M%S', time.localtime(timestamp))}-{uuid4().hex[:6]}"
    workspace_path = (
        get_workspace_kind_dir(kind) / f"{slug}-{workspace_id.split('-', 1)[1]}"
    )
    workspace_path.mkdir(parents=True, exist_ok=False)

    record = WorkspaceRecord(
        id=workspace_id,
        kind=kind,
        purpose=purpose.strip(),
        created_at=timestamp,
        last_used_at=timestamp,
        keep=(kind == "script"),
        primary_files=[],
        path=workspace_path,
    )
    _write_record(record)
    return record


def list_workspaces(
    kind: str | None = None,
    *,
    query: str = "",
    limit: int = 20,
) -> list[WorkspaceRecord]:
    ensure_workspace_dirs()
    if kind is not None:
        _validate_workspace_kind(kind)
        kind_dirs = [get_workspace_kind_dir(kind)]
    else:
        kind_dirs = [get_workspace_kind_dir("script"), get_workspace_kind_dir("tmp")]

    records: list[WorkspaceRecord] = []
    lowered_query = query.strip().lower()
    for kind_dir in kind_dirs:
        if not kind_dir.is_dir():
            continue
        for child in kind_dir.iterdir():
            if not child.is_dir():
                continue
            record = _load_record(child)
            if record is None:
                continue
            if lowered_query and not _record_matches_query(record, lowered_query):
                continue
            records.append(record)

    records.sort(key=lambda item: (item.last_used_at, item.created_at), reverse=True)
    if limit <= 0:
        return records
    return records[:limit]


def resolve_workspace_ref(ref: str, kind: str | None = None) -> WorkspaceRecord:
    if kind is not None:
        _validate_workspace_kind(kind)

    stripped = ref.strip()
    if not stripped:
        raise ValueError("workspace reference is required")

    for record in list_workspaces(kind, limit=0):
        if record.id == stripped:
            return record

    resolved = resolve_path(stripped)
    if resolved.is_file():
        if resolved.name != WORKSPACE_META_FILENAME:
            raise ValueError("workspace path must reference a workspace directory")
        resolved = resolved.parent

    if not resolved.is_dir():
        raise FileNotFoundError(f"workspace not found: {ref}")

    record = _load_record(resolved)
    if record is None:
        raise ValueError(f"not a managed workspace: {display_path(resolved)}")
    if kind is not None and record.kind != kind:
        raise ValueError(f"workspace {record.id} is not a {kind} workspace")
    return record


def delete_workspace(ref: str, *, recursive: bool = True) -> WorkspaceRecord:
    record = resolve_workspace_ref(ref)
    children = [
        child
        for child in record.path.iterdir()
        if child.name != WORKSPACE_META_FILENAME
    ]
    if children and not recursive:
        raise OSError("workspace is not empty")

    if recursive:
        shutil.rmtree(record.path)
    else:
        metadata_path = record.path / WORKSPACE_META_FILENAME
        if metadata_path.exists():
            metadata_path.unlink()
        record.path.rmdir()

    return record


def cleanup_workspaces(
    *,
    kind: str = "tmp",
    older_than_hours: int | None = None,
    dry_run: bool = True,
    now: float | None = None,
) -> list[WorkspaceRecord]:
    _validate_workspace_kind(kind)
    ttl_hours = older_than_hours or settings.workspace_tmp_ttl_hours
    if ttl_hours <= 0:
        raise ValueError("older_than_hours must be positive")

    current_time = now or time.time()
    cutoff = current_time - ttl_hours * 3600
    matched: list[WorkspaceRecord] = []
    for record in list_workspaces(kind, limit=0):
        if record.keep:
            continue
        if record.last_used_at > cutoff:
            continue
        matched.append(record)

    if not dry_run:
        for record in matched:
            shutil.rmtree(record.path)

    return matched


def touch_workspace_for_path(path: Path) -> bool:
    record = _load_record_for_path(path)
    if record is None:
        return False
    record.last_used_at = time.time()
    _write_record(record)
    return True


def register_workspace_file(path: Path) -> bool:
    record = _load_record_for_path(path)
    if record is None:
        return False

    relative = _relative_workspace_file(record.path, path)
    if relative is not None and relative not in record.primary_files:
        record.primary_files.append(relative)
        record.primary_files.sort()
    record.last_used_at = time.time()
    _write_record(record)
    return True


def unregister_workspace_file(path: Path) -> bool:
    record = _load_record_for_path(path)
    if record is None:
        return False

    relative = _relative_workspace_file(record.path, path)
    if relative is not None:
        record.primary_files = [
            item for item in record.primary_files if item != relative
        ]
    record.last_used_at = time.time()
    _write_record(record)
    return True


def format_timestamp(timestamp: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))


def _validate_workspace_kind(kind: str) -> None:
    if kind not in WORKSPACE_KINDS:
        raise ValueError(f"unsupported workspace kind: {kind}")


def _workspace_metadata_path(workspace_path: Path) -> Path:
    return workspace_path / WORKSPACE_META_FILENAME


def _load_record(workspace_path: Path) -> WorkspaceRecord | None:
    metadata_path = _workspace_metadata_path(workspace_path)
    if not metadata_path.is_file():
        return None
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    return WorkspaceRecord(
        id=str(payload.get("id") or workspace_path.name),
        kind=str(payload.get("kind") or "tmp"),
        purpose=str(payload.get("purpose") or ""),
        created_at=float(payload.get("created_at") or 0.0),
        last_used_at=float(
            payload.get("last_used_at") or payload.get("created_at") or 0.0
        ),
        keep=bool(payload.get("keep")),
        primary_files=[
            str(item)
            for item in payload.get("primary_files", [])
            if isinstance(item, str) and item.strip()
        ],
        path=workspace_path,
    )


def _write_record(record: WorkspaceRecord) -> None:
    payload = {
        "id": record.id,
        "kind": record.kind,
        "purpose": record.purpose,
        "created_at": record.created_at,
        "last_used_at": record.last_used_at,
        "keep": record.keep,
        "primary_files": record.primary_files,
    }
    metadata_path = _workspace_metadata_path(record.path)
    metadata_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _load_record_for_path(path: Path) -> WorkspaceRecord | None:
    resolved = path.resolve()
    workspace_root = get_workspace_root_dir().resolve()
    try:
        resolved.relative_to(workspace_root)
    except ValueError:
        return None

    current = resolved if resolved.is_dir() else resolved.parent
    while True:
        record = _load_record(current)
        if record is not None:
            return record
        if current == workspace_root:
            return None
        if current.parent == current:
            return None
        current = current.parent


def _relative_workspace_file(workspace_path: Path, path: Path) -> str | None:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(workspace_path.resolve())
    except ValueError:
        return None
    if not relative.parts or relative.name == WORKSPACE_META_FILENAME:
        return None
    return relative.as_posix()


def _record_matches_query(record: WorkspaceRecord, lowered_query: str) -> bool:
    haystacks = [
        record.id.lower(),
        record.kind.lower(),
        record.purpose.lower(),
        display_path(record.path).lower(),
        *(item.lower() for item in record.primary_files),
    ]
    return any(lowered_query in haystack for haystack in haystacks)


def _slugify(text: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip()).strip("-").lower()
    return normalized[:40] or "workspace"
