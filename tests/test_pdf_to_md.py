from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch


def _load_pdf_to_md_module() -> ModuleType:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "pdf_to_md.py"
    spec = importlib.util.spec_from_file_location("pdf_to_md_script", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载脚本模块: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pdf_to_md = _load_pdf_to_md_module()


class PdfToMdTokenTests(unittest.TestCase):
    def test_auth_headers_raises_without_token(self) -> None:
        with patch.dict(pdf_to_md.os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                pdf_to_md.auth_headers()

        self.assertIn(pdf_to_md.TOKEN_ENV_VAR, str(ctx.exception))

    def test_submit_and_upload_fails_before_network_without_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "sample.pdf"
            pdf_path.write_bytes(b"%PDF-1.7\n")

            with (
                patch.dict(pdf_to_md.os.environ, {}, clear=True),
                patch.object(pdf_to_md.requests, "post") as post_mock,
            ):
                with self.assertRaises(RuntimeError):
                    pdf_to_md.submit_and_upload(pdf_path)

        post_mock.assert_not_called()

    def test_auth_headers_includes_bearer_token(self) -> None:
        with patch.dict(
            pdf_to_md.os.environ,
            {pdf_to_md.TOKEN_ENV_VAR: "test-token"},
            clear=True,
        ):
            headers = pdf_to_md.auth_headers()

        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["Authorization"], "Bearer test-token")
