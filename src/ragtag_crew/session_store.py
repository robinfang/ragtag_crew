"""JSON-backed session persistence for Telegram、微信和 REPL 会话。"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import quote

from ragtag_crew.agent import AgentSession
from ragtag_crew.config import settings
from ragtag_crew.tools import get_tools_for_preset

log = logging.getLogger(__name__)

_SESSION_STORE_LOCK = threading.RLock()

SessionKey = int | str


@dataclass(frozen=True)
class SessionRecord:
    session_key: str
    path: Path
    last_active_at: float
    model: str
    tool_preset: str


@dataclass(frozen=True)
class SessionState:
    session_key: str
    model: str
    tool_preset: str
    enabled_skills: list[str]
    system_prompt: str
    session_prompt: str
    protected_content: str
    compression_blocks: list[dict]
    session_summary: str
    summary_updated_at: float | None
    recent_message_count: int
    browser_mode: str
    browser_attached_confirmed: bool
    planning_enabled: bool
    awaiting_plan_confirmation: bool
    pending_plan_text: str
    pending_plan_request_text: str
    plan_generated_at: float | None
    messages: list[dict]
    last_active_at: float

    def to_payload(self) -> dict:
        payload = asdict(self)
        payload["version"] = 1
        return payload


def _state_from_payload(
    payload: dict, *, session_key: SessionKey, default_system_prompt: str
) -> SessionState:
    normalized_key = _normalize_session_key(session_key)
    return SessionState(
        session_key=str(payload.get("session_key", normalized_key)),
        model=str(payload.get("model", settings.default_model)),
        tool_preset=str(payload.get("tool_preset", settings.default_tool_preset)),
        enabled_skills=list(payload.get("enabled_skills", [])),
        system_prompt=str(payload.get("system_prompt", default_system_prompt)),
        session_prompt=str(payload.get("session_prompt", "")),
        protected_content=str(payload.get("protected_content", "")),
        compression_blocks=list(payload.get("compression_blocks", [])),
        session_summary=str(payload.get("session_summary", "")),
        summary_updated_at=_coerce_optional_float(payload.get("summary_updated_at")),
        recent_message_count=int(payload.get("recent_message_count", 0)),
        browser_mode=str(
            payload.get("browser_mode", settings.browser_mode_default)
        ),
        browser_attached_confirmed=bool(
            payload.get("browser_attached_confirmed", False)
        ),
        planning_enabled=bool(
            payload.get("planning_enabled", settings.planning_enabled)
        ),
        awaiting_plan_confirmation=bool(
            payload.get("awaiting_plan_confirmation", False)
        ),
        pending_plan_text=str(payload.get("pending_plan_text", "")),
        pending_plan_request_text=str(payload.get("pending_plan_request_text", "")),
        plan_generated_at=_coerce_optional_float(payload.get("plan_generated_at")),
        messages=list(payload.get("messages", [])),
        last_active_at=_coerce_float(payload.get("last_active_at", 0)),
    )


def _state_to_session(
    state: SessionState, *, default_system_prompt: str
) -> AgentSession:
    del default_system_prompt
    tool_preset = state.tool_preset
    try:
        tools = get_tools_for_preset(tool_preset)
    except KeyError:
        tool_preset = settings.default_tool_preset
        tools = get_tools_for_preset(tool_preset)

    session = AgentSession(
        model=state.model,
        tools=tools,
        system_prompt=state.system_prompt,
        tool_preset=tool_preset,
        enabled_skills=state.enabled_skills,
        session_prompt=state.session_prompt,
        protected_content=state.protected_content,
        compression_blocks=state.compression_blocks,
        session_summary=state.session_summary,
        summary_updated_at=state.summary_updated_at,
        recent_message_count=state.recent_message_count,
        browser_mode=state.browser_mode,
        browser_attached_confirmed=state.browser_attached_confirmed,
        planning_enabled=state.planning_enabled,
        awaiting_plan_confirmation=state.awaiting_plan_confirmation,
        pending_plan_text=state.pending_plan_text,
        pending_plan_request_text=state.pending_plan_request_text,
        plan_generated_at=state.plan_generated_at,
    )
    session.messages = state.messages
    return session


def _session_to_state(session_key: SessionKey, session: AgentSession) -> SessionState:
    return SessionState(
        session_key=_normalize_session_key(session_key),
        model=session.model,
        tool_preset=session.tool_preset,
        enabled_skills=list(session.enabled_skills),
        system_prompt=session.system_prompt,
        session_prompt=session.session_prompt,
        protected_content=session.protected_content,
        compression_blocks=list(session.compression_blocks),
        session_summary=session.session_summary,
        summary_updated_at=session.summary_updated_at,
        recent_message_count=session.recent_message_count,
        browser_mode=session.browser_mode,
        browser_attached_confirmed=session.browser_attached_confirmed,
        planning_enabled=session.planning_enabled,
        awaiting_plan_confirmation=session.awaiting_plan_confirmation,
        pending_plan_text=session.pending_plan_text,
        pending_plan_request_text=session.pending_plan_request_text,
        plan_generated_at=session.plan_generated_at,
        messages=list(session.messages),
        last_active_at=time.time(),
    )


def _coerce_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_optional_float(value: object) -> float | None:
    if value is None:
        return None
    return _coerce_float(value)


def _storage_dir() -> Path:
    path = Path(settings.session_storage_dir).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _normalize_session_key(session_key: SessionKey) -> str:
    return str(session_key)


def _session_path(session_key: SessionKey) -> Path:
    normalized = _normalize_session_key(session_key)
    return _storage_dir() / f"{quote(normalized, safe='')}.json"


def _try_unlink(path: Path, *, context: str = "") -> bool:
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError:
        log.warning("Failed to delete %s%s", path, f" ({context})" if context else "")
        return False


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
    with _SESSION_STORE_LOCK:
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
                    _try_unlink(path, context="cleanup expired")

        for tmp_path in root.glob("*.tmp"):
            _try_unlink(tmp_path, context="cleanup temp")


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
        _try_unlink(path, context="load expired")
        return None

    state = _state_from_payload(
        payload,
        session_key=session_key,
        default_system_prompt=default_system_prompt,
    )
    return _state_to_session(state, default_system_prompt=default_system_prompt)


def save_session(session_key: SessionKey, session: AgentSession) -> None:
    normalized_key = _normalize_session_key(session_key)
    payload = _session_to_state(normalized_key, session).to_payload()
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
        _try_unlink(Path(tmp_name), context="save cleanup")
        raise


def delete_session(session_key: SessionKey) -> None:
    _try_unlink(_session_path(session_key), context="delete")
