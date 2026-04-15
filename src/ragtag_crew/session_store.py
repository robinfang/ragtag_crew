"""JSON-backed session persistence for Telegram、微信和 REPL 会话。"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from ragtag_crew.agent import AgentSession
from ragtag_crew.config import settings
from ragtag_crew.tools import get_tools_for_preset

log = logging.getLogger(__name__)

SessionKey = int | str


@dataclass(frozen=True)
class SessionRecord:
    session_key: str
    path: Path
    last_active_at: float
    model: str
    tool_preset: str


def _storage_dir() -> Path:
    path = Path(settings.session_storage_dir).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _normalize_session_key(session_key: SessionKey) -> str:
    return str(session_key)


def _session_path(session_key: SessionKey) -> Path:
    normalized = _normalize_session_key(session_key)
    return _storage_dir() / f"{quote(normalized, safe='')}.json"


def _read_payload(path: Path, *, on_error: Callable[[Path], None]) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        on_error(path)
        return None


def _payload_session_key(payload: dict, path: Path) -> str:
    if "session_key" in payload:
        return str(payload["session_key"])
    if "chat_id" in payload:
        return str(payload["chat_id"])
    return path.stem


def cleanup_expired_sessions() -> None:
    ttl_seconds = max(settings.session_ttl_hours, 0) * 3600
    root = _storage_dir()
    if ttl_seconds > 0:
        cutoff = time.time() - ttl_seconds
        for path in root.glob("*.json"):
            payload = _read_payload(
                path,
                on_error=lambda unreadable_path: log.warning(
                    "Skipping unreadable session file: %s", unreadable_path
                ),
            )
            if payload is None:
                continue

            if payload.get("last_active_at", 0) < cutoff:
                path.unlink(missing_ok=True)

    for tmp_path in root.glob("*.tmp"):
        tmp_path.unlink(missing_ok=True)


def list_sessions() -> list[SessionRecord]:
    records: list[SessionRecord] = []
    for path in sorted(_storage_dir().glob("*.json"), key=lambda item: item.name):
        payload = _read_payload(
            path,
            on_error=lambda unreadable_path: log.warning(
                "Skipping unreadable session file: %s", unreadable_path
            ),
        )
        if payload is None:
            continue
        records.append(
            SessionRecord(
                session_key=_payload_session_key(payload, path),
                path=path,
                last_active_at=float(payload.get("last_active_at", 0)),
                model=str(payload.get("model", "")),
                tool_preset=str(payload.get("tool_preset", "")),
            )
        )
    records.sort(key=lambda item: (-item.last_active_at, item.session_key))
    return records


def read_session_payload(session_key: SessionKey) -> dict:
    path = _session_path(session_key)
    if not path.exists():
        raise FileNotFoundError(f"Session not found: {session_key}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_session(
    session_key: SessionKey, *, default_system_prompt: str
) -> AgentSession | None:
    path = _session_path(session_key)
    if not path.exists():
        return None

    payload = _read_payload(
        path,
        on_error=lambda corrupt_path: log.warning(
            "Skipping corrupt session file: %s", corrupt_path
        ),
    )
    if payload is None:
        return None

    ttl_seconds = max(settings.session_ttl_hours, 0) * 3600
    if ttl_seconds > 0 and payload.get("last_active_at", 0) < time.time() - ttl_seconds:
        path.unlink(missing_ok=True)
        return None

    tool_preset = payload.get("tool_preset", settings.default_tool_preset)
    try:
        tools = get_tools_for_preset(tool_preset)
    except KeyError:
        tool_preset = settings.default_tool_preset
        tools = get_tools_for_preset(tool_preset)

    session = AgentSession(
        model=payload.get("model", settings.default_model),
        tools=tools,
        system_prompt=payload.get("system_prompt", default_system_prompt),
        tool_preset=tool_preset,
        enabled_skills=payload.get("enabled_skills", []),
        session_prompt=payload.get("session_prompt", ""),
        protected_content=payload.get("protected_content", ""),
        compression_blocks=payload.get("compression_blocks", []),
        session_summary=payload.get("session_summary", ""),
        summary_updated_at=payload.get("summary_updated_at"),
        recent_message_count=payload.get("recent_message_count", 0),
        browser_mode=payload.get("browser_mode", settings.browser_mode_default),
        browser_attached_confirmed=payload.get("browser_attached_confirmed", False),
        planning_enabled=payload.get("planning_enabled", settings.planning_enabled),
    )
    session.messages = payload.get("messages", [])
    return session


def save_session(session_key: SessionKey, session: AgentSession) -> None:
    normalized_key = _normalize_session_key(session_key)
    payload = {
        "version": 1,
        "session_key": normalized_key,
        "model": session.model,
        "tool_preset": session.tool_preset,
        "enabled_skills": session.enabled_skills,
        "system_prompt": session.system_prompt,
        "session_prompt": session.session_prompt,
        "protected_content": session.protected_content,
        "compression_blocks": session.compression_blocks,
        "session_summary": session.session_summary,
        "summary_updated_at": session.summary_updated_at,
        "recent_message_count": session.recent_message_count,
        "browser_mode": session.browser_mode,
        "browser_attached_confirmed": session.browser_attached_confirmed,
        "planning_enabled": session.planning_enabled,
        "messages": session.messages,
        "last_active_at": time.time(),
    }
    target = _session_path(normalized_key)
    root = target.parent
    root.mkdir(parents=True, exist_ok=True)

    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=root,
        prefix=f"{target.stem}.",
        suffix=".tmp",
        text=True,
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(json.dumps(payload, ensure_ascii=False, indent=2))
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_name, target)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def delete_session(session_key: SessionKey) -> None:
    _session_path(session_key).unlink(missing_ok=True)
