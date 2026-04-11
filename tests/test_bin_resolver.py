from __future__ import annotations

import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from ragtag_crew.config import settings
from ragtag_crew.tools import bin_resolver as br


class _TempSetting:
    def __init__(self, name: str, value):
        self._name = name
        self._new = value
        self._old = getattr(settings, name)

    def __enter__(self):
        setattr(settings, self._name, self._new)
        return self

    def __exit__(self, *exc):
        setattr(settings, self._name, self._old)


class DownloadInfoTests(unittest.TestCase):
    def test_download_url_constructs_correctly(self) -> None:
        info = br.DownloadInfo(
            github_repo="BurntSushi/ripgrep",
            version="14.1.1",
            binary_name="rg.exe",
            asset_pattern="ripgrep-14.1.1-x86_64-pc-windows-msvc.zip",
        )
        self.assertEqual(
            info.download_url(),
            "https://github.com/BurntSushi/ripgrep/releases/download/14.1.1/"
            "ripgrep-14.1.1-x86_64-pc-windows-msvc.zip",
        )

    def test_download_url_with_explicit_asset(self) -> None:
        info = br.DownloadInfo(
            github_repo="sharkdp/fd",
            version="10.4.2",
            binary_name="fd.exe",
            asset_pattern="fd-v10.4.2-x86_64-pc-windows-msvc.zip",
        )
        url = info.download_url()
        self.assertIn("sharkdp/fd", url)
        self.assertIn("10.4.2", url)
        self.assertIn("fd-v10.4.2", url)


class ResolveBinaryTests(unittest.TestCase):
    def test_resolve_binary_finds_system_binary(self) -> None:
        sentinel = "/usr/bin/test_rg"
        with patch(
            "ragtag_crew.tools.bin_resolver.shutil.which", return_value=sentinel
        ):
            result = br.resolve_binary("rg")
        self.assertEqual(result, Path(sentinel))

    def test_resolve_binary_finds_cached_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "bin"
            cache.mkdir()
            cached_rg = cache / "rg.exe"
            cached_rg.write_bytes(b"fake rg")

            with _TempSetting("tools_cache_dir", str(cache)):
                with patch(
                    "ragtag_crew.tools.bin_resolver.shutil.which", return_value=None
                ):
                    result = br.resolve_binary("rg")

            self.assertEqual(result, cached_rg)

    def test_resolve_binary_raises_when_no_download_info(self) -> None:
        with (
            patch("ragtag_crew.tools.bin_resolver.shutil.which", return_value=None),
            _TempSetting("tools_cache_dir", "/nonexistent_cache_dir_for_test"),
            self.assertRaises(FileNotFoundError) as ctx,
        ):
            br.resolve_binary("nonexistent_tool_xyz")

        self.assertIn("nonexistent_tool_xyz", str(ctx.exception))
        self.assertIn("no download info", str(ctx.exception))

    def test_resolve_binary_downloads_when_needed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "bin"
            cache.mkdir()
            fake_rg = cache / "rg.exe"
            fake_rg.write_bytes(b"downloaded rg content")

            fake_info = br.DownloadInfo(
                github_repo="BurntSushi/ripgrep",
                version="14.1.1",
                binary_name="rg.exe",
                asset_pattern="ripgrep-14.1.1-x86_64-pc-windows-msvc.zip",
            )

            with (
                _TempSetting("tools_cache_dir", str(cache)),
                patch("ragtag_crew.tools.bin_resolver.shutil.which", return_value=None),
                patch(
                    "ragtag_crew.tools.bin_resolver._download_binary",
                    return_value=fake_rg,
                ),
            ):
                result = br.resolve_binary("rg", download_info=fake_info)

            self.assertEqual(result, fake_rg)

    def test_resolve_binary_custom_download_info_overrides_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "bin"
            cache.mkdir()
            custom = cache / "mytool.exe"
            custom.write_bytes(b"custom")

            info = br.DownloadInfo(
                github_repo="user/tool",
                version="1.0.0",
                binary_name="mytool.exe",
                asset_pattern="tool-1.0.0.zip",
            )

            with (
                _TempSetting("tools_cache_dir", str(cache)),
                patch("ragtag_crew.tools.bin_resolver.shutil.which", return_value=None),
                patch(
                    "ragtag_crew.tools.bin_resolver._download_binary",
                    return_value=custom,
                ),
            ):
                result = br.resolve_binary("mytool", download_info=info)

            self.assertEqual(result, custom)


class ExtractBinaryTests(unittest.TestCase):
    def test_extract_from_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zip_path = tmp_path / "archive.zip"
            dest = tmp_path / "extracted.exe"

            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("rg-14.1.1/rg.exe", b"fake binary content")

            br._extract_binary(zip_path, "rg.exe", dest)
            self.assertEqual(dest.read_bytes(), b"fake binary content")

    def test_extract_from_zip_raises_if_binary_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zip_path = tmp_path / "archive.zip"
            dest = tmp_path / "extracted.exe"

            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("readme.txt", b"no binary here")

            with self.assertRaises(FileNotFoundError) as ctx:
                br._extract_binary(zip_path, "rg.exe", dest)

            self.assertIn("not found in zip archive", str(ctx.exception))

    def test_extract_from_tar_gz(self) -> None:
        import tarfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tar_path = tmp_path / "archive.tar.gz"
            dest = tmp_path / "extracted"

            with tarfile.open(tar_path, "w:gz") as tf:
                data = b"fake rg binary"
                info = tarfile.TarInfo(name="rg-14.1.1/rg")
                info.size = len(data)
                tf.addfile(info, __import__("io").BytesIO(data))

            br._extract_binary(tar_path, "rg", dest)
            self.assertEqual(dest.read_bytes(), b"fake rg binary")


class CacheDirTests(unittest.TestCase):
    def test_cache_dir_uses_config(self) -> None:
        with _TempSetting("tools_cache_dir", "/custom/bin"):
            self.assertEqual(br._cache_dir(), Path("/custom/bin"))

    def test_cache_dir_expands_user_home(self) -> None:
        raw = "~/.ragtag_crew/bin"
        with _TempSetting("tools_cache_dir", raw):
            self.assertEqual(br._cache_dir(), Path(raw).expanduser())

    def test_cached_binary_appends_exe_on_windows(self) -> None:
        with (
            _TempSetting("tools_cache_dir", "/cache"),
            patch("ragtag_crew.tools.bin_resolver._SYSTEM", "Windows"),
        ):
            result = br._cached_binary("rg")
            self.assertEqual(result, Path("/cache/rg.exe"))

    def test_cached_binary_no_exe_on_linux(self) -> None:
        with (
            _TempSetting("tools_cache_dir", "/cache"),
            patch("ragtag_crew.tools.bin_resolver._SYSTEM", "Linux"),
        ):
            result = br._cached_binary("rg")
            self.assertEqual(result, Path("/cache/rg"))


if __name__ == "__main__":
    unittest.main()
