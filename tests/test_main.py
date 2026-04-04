from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

from ragtag_crew import main as main_module


class MainCliTests(unittest.TestCase):
    def test_help_shows_usage(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout), self.assertRaises(SystemExit) as ctx:
            main_module.main(["-h"])

        self.assertEqual(ctx.exception.code, 0)
        output = stdout.getvalue()
        self.assertIn("usage: ragtag-crew", output)
        self.assertIn("TELEGRAM_BOT_TOKEN", output)
