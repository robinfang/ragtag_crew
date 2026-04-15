"""Persistent peer -> session routing for chat frontends."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ragtag_crew.config import settings
from ragtag_crew.session_store import SessionKey


@dataclass(frozen=True)
class SessionRoute:
    peer_key: str
    current_session_key: SessionKey
    default_session_key: SessionKey
    is_overridden: bool


def _routes_file() -> Path:
    path = Path(settings.session_routes_file).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_routes() -> dict[str, str]:
    path = _routes_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(peer_key): str(session_key)
        for peer_key, session_key in data.items()
        if str(session_key).strip()
    }


def _save_routes(routes: dict[str, str]) -> None:
    target = _routes_file()
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
            tmp_file.write(json.dumps(routes, ensure_ascii=False, indent=2))
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_name, target)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def build_peer_key(frontend: str, peer_id: int | str) -> str:
    return f"{frontend}:{peer_id}"


def get_session_route(
    *,
    frontend: str,
    peer_id: int | str,
    default_session_key: SessionKey,
) -> SessionRoute:
    peer_key = build_peer_key(frontend, peer_id)
    override = _load_routes().get(peer_key)
    if override is None or override == str(default_session_key):
        return SessionRoute(
            peer_key=peer_key,
            current_session_key=default_session_key,
            default_session_key=default_session_key,
            is_overridden=False,
        )
    return SessionRoute(
        peer_key=peer_key,
        current_session_key=override,
        default_session_key=default_session_key,
        is_overridden=True,
    )


def set_session_route(
    *,
    frontend: str,
    peer_id: int | str,
    default_session_key: SessionKey,
    session_key: SessionKey,
) -> SessionRoute:
    peer_key = build_peer_key(frontend, peer_id)
    routes = _load_routes()
    normalized = str(session_key)
    if normalized == str(default_session_key):
        routes.pop(peer_key, None)
    else:
        routes[peer_key] = normalized
    _save_routes(routes)
    return get_session_route(
        frontend=frontend,
        peer_id=peer_id,
        default_session_key=default_session_key,
    )


def reset_session_route(
    *,
    frontend: str,
    peer_id: int | str,
    default_session_key: SessionKey,
) -> SessionRoute:
    return set_session_route(
        frontend=frontend,
        peer_id=peer_id,
        default_session_key=default_session_key,
        session_key=default_session_key,
    )


def detect_session_source(session_key: SessionKey) -> str:
    key = str(session_key)
    if key.startswith("weixin:"):
        return "weixin"
    if key == "0":
        return "repl"
    return "telegram"
