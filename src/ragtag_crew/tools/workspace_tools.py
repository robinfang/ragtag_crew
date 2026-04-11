"""Workspace tools for managed temporary directories and reusable scripts."""

from __future__ import annotations

from pathlib import Path

from ragtag_crew.tools import Tool, register_tool
from ragtag_crew.tools.path_utils import display_path
from ragtag_crew.workspace_manager import (
    create_workspace,
    delete_workspace,
    format_timestamp,
    list_workspaces,
    register_workspace_file,
    resolve_workspace_ref,
    cleanup_workspaces,
)


async def _create_workspace_tool(
    kind: str,
    purpose: str = "",
    name_hint: str = "",
) -> str:
    try:
        record = create_workspace(kind=kind, purpose=purpose, name_hint=name_hint)
    except ValueError as exc:
        return f"ERROR: {exc}"

    purpose_text = record.purpose or "(none)"
    return (
        f"OK: created {record.kind} workspace {record.id}\n"
        f"path: {display_path(record.path)}\n"
        f"purpose: {purpose_text}"
    )


async def _list_workspaces_tool(
    kind: str = "",
    query: str = "",
    limit: int = 20,
) -> str:
    try:
        actual_kind = kind or None
        records = list_workspaces(kind=actual_kind, query=query, limit=limit)
    except ValueError as exc:
        return f"ERROR: {exc}"

    label = actual_kind or "all"
    if not records:
        if query.strip():
            return f"No {label} workspaces matched query: {query.strip()}"
        return f"No {label} workspaces found."

    lines = [f"{label.capitalize()} workspaces ({len(records)}):"]
    for record in records:
        primary = ", ".join(record.primary_files[:3]) or "(none)"
        if len(record.primary_files) > 3:
            primary += ", ..."
        lines.append(
            " | ".join(
                [
                    record.id,
                    record.kind,
                    display_path(record.path),
                    f"last_used={format_timestamp(record.last_used_at)}",
                    f"purpose={record.purpose or '(none)'}",
                    f"files={primary}",
                ]
            )
        )
    return "\n".join(lines)


async def _delete_workspace_tool(workspace: str, recursive: bool = True) -> str:
    try:
        record = delete_workspace(workspace, recursive=recursive)
    except (FileNotFoundError, OSError, ValueError) as exc:
        return f"ERROR: {exc}"

    return f"OK: deleted workspace {record.id} at {display_path(record.path)}"


async def _cleanup_workspaces_tool(
    older_than_hours: int = 0,
    dry_run: bool = True,
) -> str:
    try:
        records = cleanup_workspaces(
            kind="tmp",
            older_than_hours=older_than_hours or None,
            dry_run=dry_run,
        )
    except ValueError as exc:
        return f"ERROR: {exc}"

    if not records:
        return "No tmp workspaces matched cleanup criteria."

    prefix = "Would delete" if dry_run else "Deleted"
    lines = [f"{prefix} {len(records)} tmp workspace(s):"]
    for record in records:
        lines.append(
            f"- {record.id} | {display_path(record.path)} | last_used={format_timestamp(record.last_used_at)}"
        )
    return "\n".join(lines)


async def _write_script_tool(
    filename: str,
    content: str,
    purpose: str = "",
    workspace: str = "",
) -> str:
    try:
        record = (
            resolve_workspace_ref(workspace, kind="script")
            if workspace.strip()
            else create_workspace(
                kind="script",
                purpose=purpose,
                name_hint=Path(filename).stem,
            )
        )
    except (FileNotFoundError, ValueError) as exc:
        return f"ERROR: {exc}"

    target = (record.path / Path(filename)).resolve()
    try:
        target.relative_to(record.path.resolve())
    except ValueError:
        return f"ERROR: script path escapes workspace: {filename}"

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        register_workspace_file(target)
    except OSError as exc:
        return f"ERROR: {exc}"

    return (
        f"OK: wrote script to {display_path(target)}\n"
        f"workspace: {record.id}\n"
        f"workspace_path: {display_path(record.path)}"
    )


create_workspace_tool = register_tool(
    Tool(
        name="create_workspace",
        description=(
            "Create a managed tmp/script workspace inside the current working directory."
        ),
        parameters={
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["tmp", "script"],
                    "description": "Workspace kind",
                },
                "purpose": {
                    "type": "string",
                    "description": "Short purpose for later reuse",
                    "default": "",
                },
                "name_hint": {
                    "type": "string",
                    "description": "Optional name hint used in the directory name",
                    "default": "",
                },
            },
            "required": ["kind"],
        },
        execute=_create_workspace_tool,
        enabled_in_presets=("coding",),
    )
)


list_workspaces_tool = register_tool(
    Tool(
        name="list_workspaces",
        description=(
            "List managed workspaces so the agent can reuse temporary outputs and saved scripts."
        ),
        parameters={
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["", "tmp", "script"],
                    "description": "Optional workspace kind filter",
                    "default": "",
                },
                "query": {
                    "type": "string",
                    "description": "Optional substring filter over purpose, id, path, and primary files",
                    "default": "",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of workspaces to return",
                    "default": 20,
                },
            },
        },
        execute=_list_workspaces_tool,
        enabled_in_presets=("coding",),
    )
)


delete_workspace_tool = register_tool(
    Tool(
        name="delete_workspace",
        description="Delete a managed workspace by id or workspace path.",
        parameters={
            "type": "object",
            "properties": {
                "workspace": {
                    "type": "string",
                    "description": "Workspace id or workspace directory path",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Delete non-empty workspaces recursively",
                    "default": True,
                },
            },
            "required": ["workspace"],
        },
        execute=_delete_workspace_tool,
        enabled_in_presets=("coding",),
    )
)


cleanup_workspaces_tool = register_tool(
    Tool(
        name="cleanup_workspaces",
        description="Clean up stale tmp workspaces. Script workspaces are not auto-cleaned.",
        parameters={
            "type": "object",
            "properties": {
                "older_than_hours": {
                    "type": "integer",
                    "description": "Age threshold in hours; defaults to WORKSPACE_TMP_TTL_HOURS",
                    "default": 0,
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Preview cleanup results without deleting anything",
                    "default": True,
                },
            },
        },
        execute=_cleanup_workspaces_tool,
        enabled_in_presets=("coding",),
    )
)


write_script_tool = register_tool(
    Tool(
        name="write_script",
        description=(
            "Write a reusable script into a managed script workspace so it is kept long-term."
        ),
        parameters={
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Script filename relative to the target script workspace",
                },
                "content": {
                    "type": "string",
                    "description": "Full script content",
                },
                "purpose": {
                    "type": "string",
                    "description": "Purpose used when creating a new script workspace",
                    "default": "",
                },
                "workspace": {
                    "type": "string",
                    "description": "Optional existing script workspace id or path",
                    "default": "",
                },
            },
            "required": ["filename", "content"],
        },
        execute=_write_script_tool,
        enabled_in_presets=("coding",),
    )
)
