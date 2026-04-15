from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ragtag_crew import session_routes
from ragtag_crew.config import settings


class SessionRoutesTests(unittest.TestCase):
    def test_default_route_without_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            route_file = Path(tmp) / "routes.json"
            with patch.object(settings, "session_routes_file", str(route_file)):
                route = session_routes.get_session_route(
                    frontend="telegram",
                    peer_id=123,
                    default_session_key=123,
                )

        self.assertEqual(route.peer_key, "telegram:123")
        self.assertEqual(route.current_session_key, 123)
        self.assertFalse(route.is_overridden)

    def test_set_and_reset_route_persist_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            route_file = Path(tmp) / "routes.json"
            with patch.object(settings, "session_routes_file", str(route_file)):
                overridden = session_routes.set_session_route(
                    frontend="telegram",
                    peer_id=123,
                    default_session_key=123,
                    session_key="weixin:abc",
                )
                restored = session_routes.get_session_route(
                    frontend="telegram",
                    peer_id=123,
                    default_session_key=123,
                )
                reset = session_routes.reset_session_route(
                    frontend="telegram",
                    peer_id=123,
                    default_session_key=123,
                )

        self.assertTrue(overridden.is_overridden)
        self.assertEqual(overridden.current_session_key, "weixin:abc")
        self.assertEqual(restored.current_session_key, "weixin:abc")
        self.assertFalse(reset.is_overridden)
        self.assertEqual(reset.current_session_key, 123)

    def test_detect_session_source(self) -> None:
        self.assertEqual(session_routes.detect_session_source("weixin:abc"), "weixin")
        self.assertEqual(session_routes.detect_session_source(0), "repl")
        self.assertEqual(session_routes.detect_session_source(123), "telegram")


if __name__ == "__main__":
    unittest.main()
