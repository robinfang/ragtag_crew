from __future__ import annotations

import logging
import logging.handlers
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ragtag_crew.config import settings


class SetupLoggingTests(unittest.TestCase):
    def tearDown(self) -> None:
        root = logging.getLogger()
        for h in list(root.handlers):
            root.removeHandler(h)
            h.flush()
            h.close()

    def test_setup_logging_creates_log_dir(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        log_dir = tmp / "sub" / "logs"
        with patch("ragtag_crew.main.settings") as mock_settings:
            mock_settings.log_dir = str(log_dir)
            mock_settings.log_level = "INFO"
            mock_settings.log_max_bytes = 1024
            mock_settings.log_backup_count = 1

            from ragtag_crew.main import _setup_logging

            _setup_logging()

        self.assertTrue(log_dir.exists())

    def test_setup_logging_writes_to_file(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        log_dir = tmp
        log_file = log_dir / "ragtag_crew.log"
        with patch("ragtag_crew.main.settings") as mock_settings:
            mock_settings.log_dir = str(log_dir)
            mock_settings.log_level = "DEBUG"
            mock_settings.log_max_bytes = 1024
            mock_settings.log_backup_count = 1

            from ragtag_crew.main import _setup_logging

            _setup_logging()
            logging.getLogger("test_setup").info("hello from test")

        content = log_file.read_text(encoding="utf-8")
        self.assertIn("hello from test", content)

    def test_setup_logging_respects_log_level(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        log_dir = tmp
        log_file = log_dir / "ragtag_crew.log"
        with patch("ragtag_crew.main.settings") as mock_settings:
            mock_settings.log_dir = str(log_dir)
            mock_settings.log_level = "WARNING"
            mock_settings.log_max_bytes = 1024
            mock_settings.log_backup_count = 1

            from ragtag_crew.main import _setup_logging

            _setup_logging()
            logger = logging.getLogger("test_level")
            logger.debug("should not appear")
            logger.warning("should appear")

        content = log_file.read_text(encoding="utf-8")
        self.assertNotIn("should not appear", content)
        self.assertIn("should appear", content)


if __name__ == "__main__":
    unittest.main()
