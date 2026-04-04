"""Browser automation provider backed by agent-browser CLI."""

from __future__ import annotations

import asyncio
import fnmatch
import locale
import shutil
import subprocess
from collections.abc import Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote, urlparse

from ragtag_crew.config import settings
from ragtag_crew.external.base import CapabilityStatus
from ragtag_crew.tools import Tool, register_tool
from ragtag_crew.tools.path_utils import resolve_path

_BROWSER_MODE: ContextVar[str] = ContextVar("browser_mode", default="isolated")
_ATTACHED_CONFIRMED: ContextVar[bool] = ContextVar("browser_attached_confirmed", default=False)
_BROWSER_TOOL_NAMES = (
    "browser_open",
    "browser_snapshot",
    "browser_click",
    "browser_type",
    "browser_tab_list",
    "browser_tab_switch",
    "browser_screenshot",
    "browser_close",
)
_ATTACHED_CONNECTED = False
_SMOKE_TEST_TITLE = "Ragtag Crew Smoke Test"
_SMOKE_TEST_URL = (
    "data:text/html,"
    f"<html><head><title>{quote(_SMOKE_TEST_TITLE)}</title></head>"
    "<body><main><h1>Ragtag%20Crew%20Smoke%20Test</h1>"
    "<button>Continue</button></main></body></html>"
)
_SMOKE_TEST_TARGETS = (
    (_SMOKE_TEST_URL, _SMOKE_TEST_TITLE, ("Continue", "button")),
    ("https://example.com", "Example Domain", ("Example Domain", "Learn more")),
)


@dataclass(frozen=True)
class BrowserRuntimeState:
    enabled: bool
    command: str
    command_available: bool
    default_mode: str
    session_mode: str
    isolated_profile_dir: str
    attached_enabled: bool
    attached_connected: bool
    attached_target: str


def _command_available(command: str) -> bool:
    return _resolve_command_path(command) is not None


def _resolve_command_path(command: str) -> str | None:
    path = Path(command).expanduser()
    if path.is_absolute() or path.parent != Path("."):
        return str(path) if path.exists() else None
    return shutil.which(command)


def _normalize_mode(mode: str | None) -> str:
    candidate = (mode or settings.browser_mode_default or "isolated").strip().lower()
    if candidate not in {"isolated", "attached"}:
        return "isolated"
    return candidate


def _isolated_profile_dir() -> Path:
    path = Path(settings.browser_profile_dir).expanduser()
    path = path if path.is_absolute() else Path.cwd() / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def _attached_target() -> str:
    cdp_url = settings.browser_attached_cdp_url.strip()
    if cdp_url:
        return cdp_url
    if settings.browser_attached_auto_connect:
        return "auto-connect"
    return "not-configured"


def _attached_ready_detail() -> tuple[bool, str]:
    if not settings.browser_attached_enabled:
        return False, "disabled"
    target = _attached_target()
    if target == "not-configured":
        return False, "not-configured"
    if _ATTACHED_CONNECTED:
        return True, f"connected via {target}"
    return True, f"detached ({target})"


def _allowed_domain_patterns() -> tuple[str, ...]:
    raw = settings.browser_allowed_domains.replace("\n", ",")
    patterns = [item.strip().lower() for item in raw.split(",") if item.strip()]
    return tuple(patterns)


def _host_matches_pattern(host: str, pattern: str) -> bool:
    host = host.lower()
    pattern = pattern.lower()
    if pattern.startswith("."):
        pattern = f"*{pattern}"
    if any(char in pattern for char in "*?["):
        return fnmatch.fnmatch(host, pattern)
    return host == pattern or host.endswith(f".{pattern}")


def _is_url_allowed(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme in {"about", "data", "file"}:
        return True
    patterns = _allowed_domain_patterns()
    if not patterns:
        return True
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    return any(_host_matches_pattern(host, pattern) for pattern in patterns)


@contextmanager
def browser_execution_context(mode: str, *, attached_confirmed: bool = False) -> Iterator[None]:
    token = _BROWSER_MODE.set(_normalize_mode(mode))
    confirm_token = _ATTACHED_CONFIRMED.set(attached_confirmed)
    try:
        yield
    finally:
        _BROWSER_MODE.reset(token)
        _ATTACHED_CONFIRMED.reset(confirm_token)


def get_browser_runtime_state(*, session_mode: str | None = None) -> BrowserRuntimeState:
    return BrowserRuntimeState(
        enabled=settings.agent_browser_enabled,
        command=settings.agent_browser_command,
        command_available=_command_available(settings.agent_browser_command),
        default_mode=_normalize_mode(settings.browser_mode_default),
        session_mode=_normalize_mode(session_mode or _BROWSER_MODE.get()),
        isolated_profile_dir=str(_isolated_profile_dir()),
        attached_enabled=settings.browser_attached_enabled,
        attached_connected=_ATTACHED_CONNECTED,
        attached_target=_attached_target(),
    )


async def _get_current_url() -> str:
    result = await _run_command(["get", "url"])
    return "" if result.startswith("ERROR:") else result.strip()


async def _enforce_browser_policy(
    action: str,
    *,
    target_url: str = "",
    check_current_url: bool = False,
) -> str | None:
    mode = _normalize_mode(_BROWSER_MODE.get())

    if target_url and not _is_url_allowed(target_url):
        return (
            "ERROR: target URL is outside the browser allowed domains policy. "
            f"Allowed: {', '.join(_allowed_domain_patterns()) or '(not configured)'}"
        )

    if mode != "attached":
        return None

    if settings.browser_attached_require_confirmation and not _ATTACHED_CONFIRMED.get():
        return "ERROR: attached browser actions require explicit confirmation. Run /browser confirm-attached first."

    if check_current_url:
        current_url = await _get_current_url()
        if not current_url:
            return f"ERROR: could not verify current page before browser action '{action}'."
        if not _is_url_allowed(current_url):
            return (
                "ERROR: current page is outside the browser allowed domains policy. "
                f"Current URL: {current_url}"
            )

    return None


def _build_command(
    subcommand: Sequence[str],
    *,
    mode: str | None = None,
    require_connected: bool = True,
    require_enabled: bool = True,
) -> list[str]:
    if require_enabled and not settings.agent_browser_enabled:
        raise RuntimeError("agent-browser integration is disabled")
    executable = _resolve_command_path(settings.agent_browser_command)
    if executable is None:
        raise FileNotFoundError(settings.agent_browser_command)

    active_mode = _normalize_mode(mode or _BROWSER_MODE.get())
    command = [executable]
    if active_mode == "isolated":
        if settings.browser_headed:
            command.append("--headed")
        command.extend(["--profile", str(_isolated_profile_dir())])
    else:
        if not settings.browser_attached_enabled:
            raise RuntimeError("attached browser mode is disabled")
        if require_connected and not _ATTACHED_CONNECTED:
            raise RuntimeError("attached browser mode is not connected; run /browser connect first")
        target = _attached_target()
        if target == "not-configured":
            raise RuntimeError("attached browser target is not configured")
        if target == "auto-connect":
            command.append("--auto-connect")
        else:
            command.extend(["--cdp", target])
    command.extend(subcommand)
    return command


async def _run_command(
    subcommand: Sequence[str],
    *,
    mode: str | None = None,
    require_connected: bool = True,
    require_enabled: bool = True,
) -> str:
    try:
        command = _build_command(
            subcommand,
            mode=mode,
            require_connected=require_connected,
            require_enabled=require_enabled,
        )
    except FileNotFoundError:
        return f"ERROR: agent-browser command not found: {settings.agent_browser_command}"
    except RuntimeError as exc:
        return f"ERROR: {exc}"

    process_args = _build_process_args(command)
    if process_args[:2] == ["cmd.exe", "/c"]:
        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                process_args,
                capture_output=True,
                timeout=settings.browser_default_timeout,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return f"ERROR: browser command timed out after {settings.browser_default_timeout}s."

        output = _decode_output(completed.stdout)
        error = _decode_output(completed.stderr)
        if completed.returncode != 0:
            detail = error or output or f"agent-browser exited with code {completed.returncode}"
            return f"ERROR: {detail}"
        return output or "OK"

    proc = await asyncio.create_subprocess_exec(
        *process_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=settings.browser_default_timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return f"ERROR: browser command timed out after {settings.browser_default_timeout}s."
    except asyncio.CancelledError:
        proc.kill()
        await proc.communicate()
        raise

    output = _decode_output(stdout)
    error = _decode_output(stderr)
    if proc.returncode != 0:
        detail = error or output or f"agent-browser exited with code {proc.returncode}"
        return f"ERROR: {detail}"
    return output or "OK"


async def _browser_open(url: str) -> str:
    url = url.strip()
    if not url:
        return "ERROR: url must not be empty."
    blocked = await _enforce_browser_policy("open", target_url=url)
    if blocked:
        return blocked
    return await _run_command(["open", url])


async def _browser_snapshot(interactive_only: bool = True, compact: bool = True) -> str:
    blocked = await _enforce_browser_policy("snapshot", check_current_url=True)
    if blocked:
        return blocked
    command = ["snapshot"]
    if interactive_only:
        command.append("-i")
    if compact:
        command.append("-c")
    return await _run_command(command)


async def _browser_click(selector: str) -> str:
    selector = selector.strip()
    if not selector:
        return "ERROR: selector must not be empty."
    blocked = await _enforce_browser_policy("click", check_current_url=True)
    if blocked:
        return blocked
    return await _run_command(["click", selector])


async def _browser_type(text: str, selector: str = "") -> str:
    text = text.strip()
    if not text:
        return "ERROR: text must not be empty."
    blocked = await _enforce_browser_policy("type", check_current_url=True)
    if blocked:
        return blocked
    selector = selector.strip()
    if selector:
        return await _run_command(["fill", selector, text])
    return await _run_command(["keyboard", "type", text])


async def _browser_tab_list() -> str:
    blocked = await _enforce_browser_policy("tab_list")
    if blocked:
        return blocked
    return await _run_command(["tab"])


async def _browser_tab_switch(index: int) -> str:
    blocked = await _enforce_browser_policy("tab_switch")
    if blocked:
        return blocked
    return await _run_command(["tab", str(index)])


async def _browser_screenshot(path: str = "", full_page: bool = False) -> str:
    blocked = await _enforce_browser_policy("screenshot", check_current_url=True)
    if blocked:
        return blocked
    command = ["screenshot"]
    if full_page:
        command.append("--full")
    screenshot_path = path.strip()
    if screenshot_path:
        command.append(str(resolve_path(screenshot_path)))
    return await _run_command(command)


async def _browser_close() -> str:
    if _normalize_mode(_BROWSER_MODE.get()) == "attached":
        return "ERROR: browser_close is disabled in attached mode. Use /browser disconnect instead."
    return await _run_command(["close"])


async def connect_attached_browser() -> tuple[bool, str]:
    global _ATTACHED_CONNECTED
    result = await _run_command(["tab"], mode="attached", require_connected=False)
    if result.startswith("ERROR:"):
        _ATTACHED_CONNECTED = False
        return False, result
    _ATTACHED_CONNECTED = True
    return True, f"Connected attached browser via {_attached_target()}."


def disconnect_attached_browser() -> str:
    global _ATTACHED_CONNECTED
    _ATTACHED_CONNECTED = False
    return "Detached from current browser."


def _clip_output(text: str, limit: int = 400) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _build_process_args(command: Sequence[str]) -> list[str]:
    executable = Path(command[0])
    if executable.suffix.lower() in {".cmd", ".bat"}:
        return ["cmd.exe", "/c", *command]
    return list(command)


def _decode_output(data: bytes) -> str:
    if not data:
        return ""
    encodings = ["utf-8", locale.getpreferredencoding(False), "gbk", "cp936"]
    for encoding in encodings:
        if not encoding:
            continue
        try:
            return data.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return data.decode(errors="replace").strip()


async def _run_smoke_target(url: str, expected_title: str, snapshot_markers: Sequence[str]) -> tuple[bool, str]:
    open_result = await _run_command(
        ["open", url],
        mode="isolated",
        require_connected=False,
        require_enabled=False,
    )
    if open_result.startswith("ERROR:"):
        return False, f"open failed: {open_result}"

    title_result = await _run_command(
        ["get", "title"],
        mode="isolated",
        require_connected=False,
        require_enabled=False,
    )
    if title_result.startswith("ERROR:"):
        return False, f"title read failed: {title_result}"

    snapshot_result = await _run_command(
        ["snapshot", "-i", "-c"],
        mode="isolated",
        require_connected=False,
        require_enabled=False,
    )
    if snapshot_result.startswith("ERROR:"):
        return False, f"snapshot failed: {snapshot_result}"

    title_ok = expected_title in title_result
    snapshot_ok = any(marker in snapshot_result for marker in snapshot_markers)
    if not title_ok or not snapshot_ok:
        return False, (
            "page verification failed. "
            f"title={_clip_output(title_result)}; snapshot={_clip_output(snapshot_result)}"
        )

    return True, (
        f"title={_clip_output(title_result)}; "
        f"snapshot={_clip_output(snapshot_result)}"
    )


async def run_browser_smoke_test() -> tuple[bool, str]:
    if not _command_available(settings.agent_browser_command):
        return False, f"Browser smoke check failed: command not found: {settings.agent_browser_command}"

    failures: list[str] = []
    for url, expected_title, snapshot_markers in _SMOKE_TEST_TARGETS:
        try:
            ok, detail = await _run_smoke_target(url, expected_title, snapshot_markers)
            if ok:
                return True, f"Browser smoke check passed. target={url}; {detail}"
            failures.append(f"target={url}; {detail}")
        finally:
            await _run_command(
                ["close"],
                mode="isolated",
                require_connected=False,
                require_enabled=False,
            )

    return False, "Browser smoke check failed. " + " | ".join(failures)


def register_browser_tools() -> list[CapabilityStatus]:
    if not settings.agent_browser_enabled:
        return [
            CapabilityStatus(key="browser-isolated", kind="browser", ready=False, detail="disabled"),
            CapabilityStatus(key="browser-attached", kind="browser", ready=False, detail="disabled"),
        ]

    if not _command_available(settings.agent_browser_command):
        detail = f"command not found: {settings.agent_browser_command}"
        return [
            CapabilityStatus(key="browser-isolated", kind="browser", ready=False, detail=detail),
            CapabilityStatus(key="browser-attached", kind="browser", ready=False, detail=detail),
        ]

    tool_names: list[str] = []
    definitions = [
        Tool(
            name="browser_open",
            description="Open a URL in the active browser mode managed by agent-browser.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to open"},
                },
                "required": ["url"],
            },
            execute=_browser_open,
            source_type="browser",
            source_name="agent-browser",
            enabled_in_presets=("coding",),
        ),
        Tool(
            name="browser_snapshot",
            description="Read the current page as an accessibility snapshot.",
            parameters={
                "type": "object",
                "properties": {
                    "interactive_only": {
                        "type": "boolean",
                        "description": "Only include interactive elements",
                        "default": True,
                    },
                    "compact": {
                        "type": "boolean",
                        "description": "Reduce structural noise in the snapshot",
                        "default": True,
                    },
                },
            },
            execute=_browser_snapshot,
            source_type="browser",
            source_name="agent-browser",
            enabled_in_presets=("coding", "readonly"),
        ),
        Tool(
            name="browser_click",
            description="Click an element in the active browser page.",
            parameters={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "Element selector or ref"},
                },
                "required": ["selector"],
            },
            execute=_browser_click,
            source_type="browser",
            source_name="agent-browser",
            enabled_in_presets=("coding",),
        ),
        Tool(
            name="browser_type",
            description="Type text into the browser page, with or without a selector.",
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to enter"},
                    "selector": {
                        "type": "string",
                        "description": "Optional element selector or ref; if omitted, types into current focus",
                        "default": "",
                    },
                },
                "required": ["text"],
            },
            execute=_browser_type,
            source_type="browser",
            source_name="agent-browser",
            enabled_in_presets=("coding",),
        ),
        Tool(
            name="browser_tab_list",
            description="List current browser tabs.",
            parameters={"type": "object", "properties": {}},
            execute=_browser_tab_list,
            source_type="browser",
            source_name="agent-browser",
            enabled_in_presets=("coding", "readonly"),
        ),
        Tool(
            name="browser_tab_switch",
            description="Switch to a tab by index.",
            parameters={
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "Target tab index"},
                },
                "required": ["index"],
            },
            execute=_browser_tab_switch,
            source_type="browser",
            source_name="agent-browser",
            enabled_in_presets=("coding",),
        ),
        Tool(
            name="browser_screenshot",
            description="Take a screenshot of the current page.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Optional output path inside WORKING_DIR",
                        "default": "",
                    },
                    "full_page": {
                        "type": "boolean",
                        "description": "Capture the full page instead of the viewport",
                        "default": False,
                    },
                },
            },
            execute=_browser_screenshot,
            source_type="browser",
            source_name="agent-browser",
            enabled_in_presets=("coding", "readonly"),
        ),
        Tool(
            name="browser_close",
            description="Close the isolated browser session.",
            parameters={"type": "object", "properties": {}},
            execute=_browser_close,
            source_type="browser",
            source_name="agent-browser",
            enabled_in_presets=("coding",),
        ),
    ]

    for definition in definitions:
        tool = register_tool(definition)
        tool_names.append(tool.name)

    isolated_status = CapabilityStatus(
        key="browser-isolated",
        kind="browser",
        ready=True,
        detail=f"profile={_isolated_profile_dir()} headed={settings.browser_headed}",
        tool_names=tuple(tool_names),
    )
    attached_ready, attached_detail = _attached_ready_detail()
    attached_status = CapabilityStatus(
        key="browser-attached",
        kind="browser",
        ready=attached_ready,
        detail=attached_detail,
        tool_names=tuple(tool_names),
    )
    return [isolated_status, attached_status]
