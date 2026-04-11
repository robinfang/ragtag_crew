"""Portable binary resolver: system PATH → local cache → auto-download.

Provides a three-level fallback to locate required command-line tools
(ripgrep, fd, etc.) across different deployment environments.
"""

from __future__ import annotations

import logging
import platform
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

from ragtag_crew.config import settings

log = logging.getLogger(__name__)

_SYSTEM = platform.system()
_MACHINE = platform.machine().lower()

_PLATFORM_ASSET_MAP: dict[str, dict[str, str]] = {
    "Windows": {
        "amd64": "x86_64-pc-windows-msvc.zip",
        "x86_64": "x86_64-pc-windows-msvc.zip",
        "arm64": "aarch64-pc-windows-msvc.zip",
        "aarch64": "aarch64-pc-windows-msvc.zip",
    },
    "Darwin": {
        "amd64": "x86_64-apple-darwin.tar.gz",
        "x86_64": "x86_64-apple-darwin.tar.gz",
        "arm64": "aarch64-apple-darwin.tar.gz",
        "aarch64": "aarch64-apple-darwin.tar.gz",
    },
    "Linux": {
        "amd64": "x86_64-unknown-linux-musl.tar.gz",
        "x86_64": "x86_64-unknown-linux-musl.tar.gz",
        "arm64": "aarch64-unknown-linux-musl.tar.gz",
        "aarch64": "aarch64-unknown-linux-musl.tar.gz",
    },
}


@dataclass(frozen=True)
class DownloadInfo:
    github_repo: str
    version: str
    binary_name: str
    asset_pattern: str = ""

    def _asset_filename(self) -> str:
        if self.asset_pattern:
            return self.asset_pattern
        suffix = _PLATFORM_ASSET_MAP.get(_SYSTEM, {}).get(_MACHINE, "")
        if not suffix:
            raise RuntimeError(
                f"Unsupported platform for auto-download: {_SYSTEM}/{_MACHINE}"
            )
        return f"{self.github_repo.split('/')[-1]}-{self.version}-{suffix}"

    def download_url(self) -> str:
        return (
            f"https://github.com/{self.github_repo}"
            f"/releases/download/{self.version}/{self._asset_filename()}"
        )


_RG_DOWNLOAD = DownloadInfo(
    github_repo="BurntSushi/ripgrep",
    version="14.1.1",
    binary_name="rg.exe" if _SYSTEM == "Windows" else "rg",
)

_FD_DOWNLOAD = DownloadInfo(
    github_repo="sharkdp/fd",
    version="10.4.2",
    binary_name="fd.exe" if _SYSTEM == "Windows" else "fd",
)

_DOWNLOAD_REGISTRY: dict[str, DownloadInfo] = {
    "rg": _RG_DOWNLOAD,
    "fd": _FD_DOWNLOAD,
}


def _cache_dir() -> Path:
    return Path(settings.tools_cache_dir).expanduser()


def _cached_binary(name: str) -> Path:
    ext = ".exe" if _SYSTEM == "Windows" else ""
    return _cache_dir() / f"{name}{ext}"


def resolve_binary(
    name: str,
    *,
    download_info: DownloadInfo | None = None,
) -> Path:
    """Locate a binary using three-level fallback.

    1. ``shutil.which(name)`` — system-installed binary
    2. Local cache (``~/.ragtag_crew/bin/``)
    3. Auto-download from GitHub Releases

    Returns the resolved path or raises ``FileNotFoundError``.
    """
    system_path = shutil.which(name)
    if system_path:
        log.debug("Found %s in system PATH: %s", name, system_path)
        return Path(system_path)

    cached = _cached_binary(name)
    if cached.is_file():
        log.debug("Found %s in local cache: %s", name, cached)
        return cached

    info = download_info or _DOWNLOAD_REGISTRY.get(name)
    if info:
        return _download_binary(name, info)

    raise FileNotFoundError(
        f"'{name}' not found in PATH or local cache, "
        f"and no download info configured.  "
        f"Install it or ensure internet access for auto-download."
    )


def _download_binary(name: str, info: DownloadInfo) -> Path:
    url = info.download_url()
    cache = _cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    dest = _cached_binary(name)

    log.info("Downloading %s %s from %s ...", name, info.version, url)

    import urllib.request

    try:
        tmp_zip = cache / f"{name}.tmp"
        urllib.request.urlretrieve(url, tmp_zip)
    except Exception as exc:
        raise FileNotFoundError(
            f"Failed to download {name} from {url}: {exc}.  "
            f"Please check your internet connection and retry."
        ) from exc

    try:
        _extract_binary(tmp_zip, info.binary_name, dest)
    finally:
        tmp_zip.unlink(missing_ok=True)

    if not _SYSTEM == "Windows":
        dest.chmod(0o755)

    log.info("Installed %s %s -> %s", name, info.version, dest)
    return dest


def _extract_binary(archive: Path, binary_name: str, dest: Path) -> None:
    try:
        with zipfile.ZipFile(archive, "r") as zf:
            _extract_from_zip(zf, binary_name, dest)
            return
    except FileNotFoundError:
        raise
    except (zipfile.BadZipFile, Exception):
        pass

    import tarfile

    with tarfile.open(archive, "r:*") as tf:
        _extract_from_tar(tf, binary_name, dest)


def _extract_from_zip(zf: zipfile.ZipFile, binary_name: str, dest: Path) -> None:
    for name in zf.namelist():
        if name.endswith(binary_name) or name == binary_name:
            data = zf.read(name)
            dest.write_bytes(data)
            return
    names = ", ".join(zf.namelist()[:10])
    raise FileNotFoundError(
        f"'{binary_name}' not found in zip archive.  Contents: {names}..."
    )


def _extract_from_tar(tf: "tarfile.TarFile", binary_name: str, dest: Path) -> None:
    for member in tf.getmembers():
        if member.name.endswith(binary_name) or member.name == binary_name:
            f = tf.extractfile(member)
            if f is not None:
                dest.write_bytes(f.read())
                return
    names = ", ".join(m.name for m in tf.getmembers()[:10])
    raise FileNotFoundError(
        f"'{binary_name}' not found in tar archive.  Contents: {names}..."
    )
