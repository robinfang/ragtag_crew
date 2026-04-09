"""JSON-backed session persistence for Telegram chats."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from ragtag_crew.agent import AgentSession
from ragtag_crew.config import settings
from ragtag_crew.tools import get_tools_for_preset

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionRecord:
    chat_id: int
    path: Path
    last_active_at: float
    model: str
    tool_preset: str


def _storage_dir() -> Path:
    path = Path(settings.session_storage_dir).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _session_path(chat_id: int) -> Path:
    return _storage_dir() / f"{chat_id}.json"


def cleanup_expired_sessions() -> None:
    ttl_seconds = max(settings.session_ttl_hours, 0) * 3600
    root = _storage_dir()
    if ttl_seconds > 0:
        cutoff = time.time() - ttl_seconds
        for path in root.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                log.warning("Skipping unreadable session file: %s", path)
                continue

            if payload.get("last_active_at", 0) < cutoff:
                path.unlink(missing_ok=True)

    for tmp_path in root.glob("*.tmp"):
        tmp_path.unlink(missing_ok=True)


def list_sessions() -> list[SessionRecord]:
    records: list[SessionRecord] = []
    for path in sorted(_storage_dir().glob("*.json"), key=lambda item: item.name):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            chat_id = int(payload.get("chat_id", path.stem))
        except Exception:
            log.warning("Skipping unreadable session file: %s", path)
            continue
        records.append(
            SessionRecord(
                chat_id=chat_id,
                path=path,
                last_active_at=float(payload.get("last_active_at", 0)),
                model=str(payload.get("model", "")),
                tool_preset=str(payload.get("tool_preset", "")),
            )
        )
    records.sort(key=lambda item: (-item.last_active_at, item.chat_id))
    return records


def read_session_payload(chat_id: int) -> dict:
    path = _session_path(chat_id)
    if not path.exists():
        raise FileNotFoundError(f"Session not found: {chat_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_session(chat_id: int, *, default_system_prompt: str) -> AgentSession | None:
    path = _session_path(chat_id)
    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        log.warning("Skipping corrupt session file: %s", path)
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
        session_summary=payload.get("session_summary", ""),
        summary_updated_at=payload.get("summary_updated_at"),
        recent_message_count=payload.get("recent_message_count", 0),
        browser_mode=payload.get("browser_mode", settings.browser_mode_default),
        browser_attached_confirmed=payload.get("browser_attached_confirmed", False),
        planning_enabled=payload.get("planning_enabled", settings.planning_enabled),
    )
    session.messages = payload.get("messages", [])
    return session


def save_session(chat_id: int, session: AgentSession) -> None:
    payload = {
        "version": 1,
        "chat_id": chat_id,
        "model": session.model,
        "tool_preset": session.tool_preset,
        "enabled_skills": session.enabled_skills,
        "system_prompt": session.system_prompt,
        "session_prompt": session.session_prompt,
        "protected_content": session.protected_content,
        "session_summary": session.session_summary,
        "summary_updated_at": session.summary_updated_at,
        "recent_message_count": session.recent_message_count,
        "browser_mode": session.browser_mode,
        "browser_attached_confirmed": session.browser_attached_confirmed,
        "planning_enabled": session.planning_enabled,
        "messages": session.messages,
        "last_active_at": time.time(),
    }
    target = _session_path(chat_id)
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


def delete_session(chat_id: int) -> None:
    _session_path(chat_id).unlink(missing_ok=True)
