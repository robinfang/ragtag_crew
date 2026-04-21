"""Helpers for reusing OpenCode's stored OpenAI OAuth session."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp

from ragtag_crew.config import settings

_OPENAI_PROVIDER_ID = "openai"
_OPENAI_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
_REFRESH_SKEW_MS = 30_000


@dataclass
class CodexAuthState:
    access_token: str
    refresh_token: str
    expires_at_ms: int
    account_id: str | None = None

    @property
    def needs_refresh(self) -> bool:
        return (
            not self.access_token
            or self.expires_at_ms <= int(time.time() * 1000) + _REFRESH_SKEW_MS
        )


def _auth_file_path() -> Path:
    return Path(settings.opencode_auth_file).expanduser()


def codex_timeout_value(
    configured_seconds: float, ceiling_seconds: float
) -> float | None:
    if ceiling_seconds <= 0:
        return None
    if configured_seconds <= 0:
        return ceiling_seconds
    return min(configured_seconds, ceiling_seconds)


def codex_transport_description() -> str:
    proxy = settings.codex_proxy.strip()
    if proxy:
        return f"显式代理 {proxy}"
    if settings.codex_trust_env_proxy:
        return "环境代理"
    return "未启用代理"


def codex_request_kwargs(
    *,
    total_timeout_seconds: float | None = None,
    connect_timeout_seconds: float | None = None,
    read_timeout_seconds: float | None = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "timeout": aiohttp.ClientTimeout(
            total=total_timeout_seconds,
            connect=connect_timeout_seconds,
            sock_connect=connect_timeout_seconds,
            sock_read=read_timeout_seconds,
        )
    }
    proxy = settings.codex_proxy.strip()
    if proxy:
        kwargs["proxy"] = proxy
    return kwargs


def codex_network_error(
    exc: Exception,
    *,
    action: str,
    target: str,
) -> RuntimeError:
    transport = codex_transport_description()
    if isinstance(exc, aiohttp.ClientHttpProxyError):
        return RuntimeError(
            f"{action}失败：代理返回 HTTP {exc.status}。当前使用{transport}。"
        )
    if isinstance(exc, aiohttp.ClientProxyConnectionError):
        proxy = settings.codex_proxy.strip() or "环境代理"
        return RuntimeError(
            f"{action}失败：无法连接代理 {proxy}。请检查本地代理是否可用。"
        )
    if isinstance(exc, aiohttp.ClientConnectorError):
        return RuntimeError(
            f"{action}失败：无法连接 {target}。当前使用{transport}；"
            "如果当前网络无法直连，请配置 CODEX_PROXY 或启用 CODEX_TRUST_ENV_PROXY。"
        )
    if isinstance(exc, aiohttp.ServerDisconnectedError):
        return RuntimeError(f"{action}失败：{target} 提前断开连接，请稍后重试。")
    if isinstance(exc, aiohttp.ClientOSError):
        detail = str(exc).strip() or "未知网络错误"
        return RuntimeError(f"{action}失败：{detail}。当前使用{transport}。")
    if isinstance(exc, asyncio.TimeoutError):
        return RuntimeError(f"{action}失败：连接 {target} 超时。当前使用{transport}。")
    return RuntimeError(f"{action}失败：{exc}")


def codex_target_label(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc:
        return parsed.netloc
    if parsed.path:
        return parsed.path
    return url


def _parse_expires(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return 0
        if stripped.isdigit():
            return int(stripped)
        try:
            return int(
                datetime.fromisoformat(stripped.replace("Z", "+00:00")).timestamp()
                * 1000
            )
        except ValueError:
            return 0
    return 0


def _pad_base64url(data: str) -> str:
    missing = len(data) % 4
    if not missing:
        return data
    return data + ("=" * (4 - missing))


def _parse_jwt_claims(token: str) -> dict[str, Any] | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = base64.urlsafe_b64decode(_pad_base64url(parts[1]))
        data = json.loads(payload.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _extract_account_id_from_claims(claims: dict[str, Any]) -> str | None:
    direct = claims.get("chatgpt_account_id")
    if isinstance(direct, str) and direct:
        return direct

    namespaced = claims.get("https://api.openai.com/auth")
    if isinstance(namespaced, dict):
        account_id = namespaced.get("chatgpt_account_id")
        if isinstance(account_id, str) and account_id:
            return account_id

    organizations = claims.get("organizations")
    if isinstance(organizations, list) and organizations:
        first = organizations[0]
        if isinstance(first, dict):
            org_id = first.get("id")
            if isinstance(org_id, str) and org_id:
                return org_id
    return None


def _extract_account_id(token_data: dict[str, Any], fallback: str | None) -> str | None:
    for field in ("id_token", "access_token"):
        token = token_data.get(field)
        if not isinstance(token, str) or not token:
            continue
        claims = _parse_jwt_claims(token)
        if claims is None:
            continue
        account_id = _extract_account_id_from_claims(claims)
        if account_id:
            return account_id
    return fallback


def _load_auth_document() -> tuple[Path, dict[str, Any]]:
    path = _auth_file_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"未找到 OpenCode 凭据文件：{path}。请先执行 opencode auth login 完成 OpenAI 登录。"
        ) from exc

    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"OpenCode 凭据文件不是合法 JSON：{path}") from exc

    if not isinstance(document, dict):
        raise RuntimeError(f"OpenCode 凭据文件结构异常：{path}")
    return path, document


def _load_auth_state_sync() -> tuple[Path, dict[str, Any], CodexAuthState]:
    path, document = _load_auth_document()
    provider = document.get(_OPENAI_PROVIDER_ID)
    if not isinstance(provider, dict):
        raise RuntimeError(
            f"OpenCode 凭据文件中没有 openai 登录态：{path}。请先执行 opencode auth login。"
        )
    if provider.get("type") != "oauth":
        raise RuntimeError(
            "OpenCode 当前 openai 凭据不是 OAuth 登录态，无法复用 ChatGPT/Codex 订阅路线。"
        )

    refresh_token = provider.get("refresh")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise RuntimeError("OpenCode 的 openai OAuth 登录态缺少 refresh token。")

    access_token = provider.get("access")
    expires_at_ms = _parse_expires(provider.get("expires"))
    account_id = provider.get("accountId")
    if not isinstance(account_id, str) or not account_id:
        account_id = None

    return (
        path,
        document,
        CodexAuthState(
            access_token=access_token if isinstance(access_token, str) else "",
            refresh_token=refresh_token,
            expires_at_ms=expires_at_ms,
            account_id=account_id,
        ),
    )


def _write_auth_state_sync(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")


async def _refresh_access_token(
    session: aiohttp.ClientSession,
    refresh_token: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    if timeout_seconds <= 0:
        raise RuntimeError("刷新 Codex OAuth 登录态前已超过 LLM 超时限制。")

    refresh_url = f"{settings.codex_auth_issuer.rstrip('/')}/oauth/token"
    request_kwargs = codex_request_kwargs(
        total_timeout_seconds=codex_timeout_value(
            settings.codex_refresh_timeout,
            timeout_seconds,
        ),
        connect_timeout_seconds=codex_timeout_value(
            settings.codex_connect_timeout,
            timeout_seconds,
        ),
    )

    try:
        response_ctx = session.post(
            refresh_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": _OPENAI_CLIENT_ID,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            **request_kwargs,
        )
        async with response_ctx as response:
            if response.status >= 400:
                detail = await response.text()
                detail = detail.strip()
                if response.status in {400, 401}:
                    raise RuntimeError(
                        "刷新 Codex OAuth token 失败：OpenCode 的 OpenAI 登录态可能已失效，"
                        "请重新执行 opencode auth login。"
                    )
                suffix = f" {detail}" if detail else ""
                raise RuntimeError(
                    f"刷新 Codex OAuth token 失败：HTTP {response.status}.{suffix}"
                )
            data = await response.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise codex_network_error(
            exc,
            action="刷新 Codex OAuth 登录态",
            target=codex_target_label(refresh_url),
        ) from exc

    if not isinstance(data, dict):
        raise RuntimeError("刷新 Codex OAuth token 失败：返回结构异常。")
    return data


async def load_codex_auth_state() -> CodexAuthState:
    """Load the stored OpenCode OAuth session without refreshing it."""
    _path, _document, state = await asyncio.to_thread(_load_auth_state_sync)
    return state


async def ensure_codex_auth_state(
    *,
    session: aiohttp.ClientSession,
    timeout_seconds: float,
) -> CodexAuthState:
    """Load and refresh the stored OpenCode OAuth session if needed."""
    path, document, state = await asyncio.to_thread(_load_auth_state_sync)
    if not state.needs_refresh:
        return state

    tokens = await _refresh_access_token(
        session=session,
        refresh_token=state.refresh_token,
        timeout_seconds=timeout_seconds,
    )
    access_token = tokens.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise RuntimeError("刷新 Codex OAuth token 失败：缺少 access_token。")

    refresh_token = tokens.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        refresh_token = state.refresh_token

    expires_in = tokens.get("expires_in")
    ttl_seconds = expires_in if isinstance(expires_in, (int, float)) else 3600
    account_id = _extract_account_id(tokens, state.account_id)

    provider = document[_OPENAI_PROVIDER_ID]
    if not isinstance(provider, dict):
        raise RuntimeError("OpenCode 凭据文件结构异常：openai 节点不是对象。")

    provider.update(
        {
            "type": "oauth",
            "refresh": refresh_token,
            "access": access_token,
            "expires": int(time.time() * 1000) + int(ttl_seconds * 1000),
        }
    )
    if account_id:
        provider["accountId"] = account_id

    await asyncio.to_thread(_write_auth_state_sync, path, document)
    return CodexAuthState(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at_ms=_parse_expires(provider.get("expires")),
        account_id=account_id,
    )
