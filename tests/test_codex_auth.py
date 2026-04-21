from __future__ import annotations

import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ragtag_crew.codex_auth import ensure_codex_auth_state, load_codex_auth_state


def _jwt(payload: dict[str, object]) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode("ascii").rstrip("=")
    body = (
        base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )
    return f"{header}.{body}.sig"


class _FakeJsonResponse:
    def __init__(self, payload: dict[str, object], status: int = 200) -> None:
        self._payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return json.dumps(self._payload)


class _FakeSession:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload
        self.calls: list[tuple[str, dict[str, object]]] = []

    def post(self, url, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append((url, kwargs))
        return _FakeJsonResponse(self._payload)


class CodexAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_load_codex_auth_state_reads_existing_oauth_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            auth_path = Path(tmpdir) / "auth.json"
            auth_path.write_text(
                json.dumps(
                    {
                        "openai": {
                            "type": "oauth",
                            "access": "access-token",
                            "refresh": "refresh-token",
                            "expires": int(time.time() * 1000) + 60_000,
                            "accountId": "acct_1",
                        }
                    }
                ),
                encoding="utf-8",
            )

            with patch(
                "ragtag_crew.codex_auth.settings.opencode_auth_file", str(auth_path)
            ):
                state = await load_codex_auth_state()

        self.assertEqual(state.access_token, "access-token")
        self.assertEqual(state.refresh_token, "refresh-token")
        self.assertEqual(state.account_id, "acct_1")

    async def test_ensure_codex_auth_state_refreshes_expired_token_and_persists(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            auth_path = Path(tmpdir) / "auth.json"
            auth_path.write_text(
                json.dumps(
                    {
                        "openai": {
                            "type": "oauth",
                            "access": "stale-access",
                            "refresh": "stale-refresh",
                            "expires": 0,
                        }
                    }
                ),
                encoding="utf-8",
            )
            fresh_access = _jwt({"chatgpt_account_id": "acct_2"})
            fake_session = _FakeSession(
                {
                    "access_token": fresh_access,
                    "refresh_token": "fresh-refresh",
                    "expires_in": 1200,
                }
            )

            with (
                patch(
                    "ragtag_crew.codex_auth.settings.opencode_auth_file", str(auth_path)
                ),
                patch(
                    "ragtag_crew.codex_auth.settings.codex_auth_issuer",
                    "https://auth.openai.com",
                ),
            ):
                state = await ensure_codex_auth_state(
                    session=fake_session,
                    timeout_seconds=10,
                )

            persisted = json.loads(auth_path.read_text(encoding="utf-8"))

        self.assertEqual(len(fake_session.calls), 1)
        self.assertEqual(
            fake_session.calls[0][0],
            "https://auth.openai.com/oauth/token",
        )
        self.assertEqual(state.access_token, fresh_access)
        self.assertEqual(state.refresh_token, "fresh-refresh")
        self.assertEqual(state.account_id, "acct_2")
        self.assertEqual(persisted["openai"]["access"], fresh_access)
        self.assertEqual(persisted["openai"]["refresh"], "fresh-refresh")
        self.assertEqual(persisted["openai"]["accountId"], "acct_2")
        self.assertGreater(persisted["openai"]["expires"], int(time.time() * 1000))

    async def test_refresh_uses_explicit_proxy_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            auth_path = Path(tmpdir) / "auth.json"
            auth_path.write_text(
                json.dumps(
                    {
                        "openai": {
                            "type": "oauth",
                            "access": "stale-access",
                            "refresh": "stale-refresh",
                            "expires": 0,
                        }
                    }
                ),
                encoding="utf-8",
            )
            fake_session = _FakeSession(
                {
                    "access_token": _jwt({"chatgpt_account_id": "acct_3"}),
                    "refresh_token": "fresh-refresh",
                    "expires_in": 1200,
                }
            )

            with (
                patch(
                    "ragtag_crew.codex_auth.settings.opencode_auth_file", str(auth_path)
                ),
                patch(
                    "ragtag_crew.codex_auth.settings.codex_proxy",
                    "http://localhost:1087",
                ),
            ):
                await ensure_codex_auth_state(
                    session=fake_session,
                    timeout_seconds=10,
                )

        self.assertEqual(
            fake_session.calls[0][1]["proxy"],
            "http://localhost:1087",
        )

    async def test_refresh_timeout_reports_transport_context(self) -> None:
        class _TimeoutSession:
            def post(self, url, **kwargs):  # type: ignore[no-untyped-def]
                raise TimeoutError()

        with tempfile.TemporaryDirectory() as tmpdir:
            auth_path = Path(tmpdir) / "auth.json"
            auth_path.write_text(
                json.dumps(
                    {
                        "openai": {
                            "type": "oauth",
                            "access": "stale-access",
                            "refresh": "stale-refresh",
                            "expires": 0,
                        }
                    }
                ),
                encoding="utf-8",
            )

            with (
                patch(
                    "ragtag_crew.codex_auth.settings.opencode_auth_file", str(auth_path)
                ),
                patch("ragtag_crew.codex_auth.settings.codex_trust_env_proxy", True),
                patch("ragtag_crew.codex_auth.settings.codex_proxy", ""),
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    await ensure_codex_auth_state(
                        session=_TimeoutSession(),
                        timeout_seconds=10,
                    )

        self.assertIn("刷新 Codex OAuth 登录态失败", str(ctx.exception))
        self.assertIn("环境代理", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
