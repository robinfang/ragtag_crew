from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ragtag_crew.config import settings
from ragtag_crew.external import everything as everything_module
from ragtag_crew.external import browser_agent as browser_module
from ragtag_crew.external import manager as manager_module
from ragtag_crew.external import mcp_client as mcp_module
from ragtag_crew.external import openapi_provider as openapi_module
from ragtag_crew.external import web_search as web_search_module
from ragtag_crew.tools import _ALL_TOOLS, get_tools_for_preset


@contextmanager
def working_dir(path: Path):
    original = settings.working_dir
    settings.working_dir = str(path)
    try:
        yield
    finally:
        settings.working_dir = original


@contextmanager
def temp_setting(name: str, value):  # type: ignore[no-untyped-def]
    original = getattr(settings, name)
    setattr(settings, name, value)
    try:
        yield
    finally:
        setattr(settings, name, original)


class EverythingTests(unittest.TestCase):
    def tearDown(self) -> None:
        _ALL_TOOLS.pop("everything_search", None)

    def test_build_everything_command_scopes_to_working_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with working_dir(root), temp_setting("everything_command", "es.exe"):
                command = everything_module._build_everything_command(
                    query="main.py",
                    path=".",
                    max_results=25,
                )

        self.assertEqual(command[0], "es.exe")
        self.assertIn("-path", command)
        self.assertIn(str(root), command)
        self.assertIn("-n", command)
        self.assertIn("25", command)
        self.assertEqual(command[-1], "main.py")

    def test_register_everything_tool_adds_tool_to_readonly_preset(self) -> None:
        with temp_setting("everything_enabled", True), patch(
            "ragtag_crew.external.everything.platform.system", return_value="Windows"
        ):
            status = everything_module.register_everything_tool()

        tool_names = [tool.name for tool in get_tools_for_preset("readonly")]
        self.assertTrue(status.ready)
        self.assertIn("everything_search", tool_names)


class BrowserAgentTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        for name in list(_ALL_TOOLS):
            if name.startswith("browser_"):
                _ALL_TOOLS.pop(name, None)
        browser_module.disconnect_attached_browser()

    def test_register_browser_tools_adds_snapshot_to_readonly(self) -> None:
        with temp_setting("agent_browser_enabled", True), temp_setting(
            "agent_browser_command", "agent-browser"
        ), patch("ragtag_crew.external.browser_agent._command_available", return_value=True):
            statuses = browser_module.register_browser_tools()

        readonly_names = [tool.name for tool in get_tools_for_preset("readonly")]
        self.assertIn("browser_snapshot", readonly_names)
        self.assertEqual([status.key for status in statuses], ["browser-isolated", "browser-attached"])

    def test_resolve_command_path_returns_real_executable(self) -> None:
        with patch("ragtag_crew.external.browser_agent.shutil.which", return_value="C:/bin/agent-browser.cmd"):
            resolved = browser_module._resolve_command_path("agent-browser")

        self.assertEqual(resolved, "C:/bin/agent-browser.cmd")

    def test_build_process_args_wraps_cmd_scripts(self) -> None:
        process_args = browser_module._build_process_args(["C:/bin/agent-browser.cmd", "open", "https://example.com"])

        self.assertEqual(process_args[:2], ["cmd.exe", "/c"])
        self.assertEqual(process_args[2], "C:/bin/agent-browser.cmd")

    async def test_browser_open_blocks_non_whitelisted_domain(self) -> None:
        with temp_setting("browser_allowed_domains", "example.com"):
            result = await browser_module._browser_open("https://not-allowed.test")

        self.assertIn("outside the browser allowed domains policy", result)

    async def test_browser_click_requires_attached_confirmation(self) -> None:
        with temp_setting("browser_attached_require_confirmation", True), browser_module.browser_execution_context(
            "attached", attached_confirmed=False
        ):
            result = await browser_module._browser_click("#submit")

        self.assertIn("require explicit confirmation", result)

    async def test_connect_attached_browser_marks_runtime_connected(self) -> None:
        with temp_setting("agent_browser_enabled", True), temp_setting(
            "browser_attached_enabled", True
        ), temp_setting("browser_attached_auto_connect", True), patch(
            "ragtag_crew.external.browser_agent._run_command",
            new=AsyncMock(return_value="tab list"),
        ):
            ok, detail = await browser_module.connect_attached_browser()
            state = browser_module.get_browser_runtime_state(session_mode="attached")

        self.assertTrue(ok)
        self.assertIn("Connected attached browser", detail)
        self.assertTrue(state.attached_connected)

    async def test_run_browser_smoke_test_executes_basic_flow(self) -> None:
        side_effect = [
            "OK",
            browser_module._SMOKE_TEST_TITLE,
            "button Continue",
            "OK",
        ]
        with temp_setting("agent_browser_command", "agent-browser"), patch(
            "ragtag_crew.external.browser_agent._command_available",
            return_value=True,
        ), patch(
            "ragtag_crew.external.browser_agent._run_command",
            new=AsyncMock(side_effect=side_effect),
        ) as run_command:
            ok, detail = await browser_module.run_browser_smoke_test()

        self.assertTrue(ok)
        self.assertIn("Browser smoke check passed.", detail)
        self.assertEqual(run_command.await_count, 4)

    async def test_run_browser_smoke_test_reports_missing_command(self) -> None:
        with patch("ragtag_crew.external.browser_agent._command_available", return_value=False):
            ok, detail = await browser_module.run_browser_smoke_test()

        self.assertFalse(ok)
        self.assertIn("command not found", detail)


class WebSearchTests(unittest.TestCase):
    def tearDown(self) -> None:
        _ALL_TOOLS.pop("web_search", None)

    def test_normalize_serper_results(self) -> None:
        with temp_setting("web_search_provider", "serper"):
            results = web_search_module._normalize_search_results(
                {
                    "organic": [
                        {
                            "title": "Example title",
                            "link": "https://example.com/a",
                            "snippet": "Example snippet",
                        }
                    ]
                }
            )

        self.assertEqual(results[0].title, "Example title")
        self.assertEqual(results[0].url, "https://example.com/a")

    def test_register_web_search_tool_adds_tool_to_readonly_preset(self) -> None:
        with temp_setting("web_search_enabled", True), temp_setting("web_search_provider", "serper"):
            status = web_search_module.register_web_search_tool()

        tool_names = [tool.name for tool in get_tools_for_preset("readonly")]
        self.assertTrue(status.ready)
        self.assertIn("web_search", tool_names)


class OpenAPIConfigTests(unittest.TestCase):
    def test_load_openapi_provider_configs_reads_list_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "openapi_tools.local.json"
            config_path.write_text(
                json.dumps(
                    [
                        {
                            "name": "search-gateway",
                            "base_url": "http://127.0.0.1:8080",
                            "enabled": True,
                            "tools": [
                                {
                                    "name": "search_gateway_query",
                                    "description": "Search via gateway",
                                    "path": "/search",
                                    "presets": ["coding", "readonly"],
                                    "parameters": {
                                        "type": "object",
                                        "properties": {"query": {"type": "string"}},
                                        "required": ["query"],
                                    },
                                    "request_body": {"query": "$query"},
                                    "result_mode": "search_results",
                                }
                            ],
                        }
                    ]
                ),
                encoding="utf-8",
            )

            with temp_setting("openapi_tools_file", str(config_path)):
                providers = openapi_module.load_openapi_provider_configs()

        self.assertEqual(len(providers), 1)
        self.assertEqual(providers[0].name, "search-gateway")
        self.assertEqual(providers[0].tools[0].name, "search_gateway_query")


class OpenAPIToolTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        for name in list(_ALL_TOOLS):
            if name.startswith("search_gateway"):
                _ALL_TOOLS.pop(name, None)

    async def test_register_openapi_tools_adds_tool_to_readonly_preset(self) -> None:
        provider = openapi_module.OpenAPIProviderConfig(
            name="search-gateway",
            base_url="http://127.0.0.1:8080",
            tools=(
                openapi_module.OpenAPIToolConfig(
                    name="search_gateway_query",
                    description="Search via gateway",
                    path="/search",
                    presets=("coding", "readonly"),
                    parameters={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                    request_body={"query": "$query"},
                    result_mode="search_results",
                ),
            ),
        )

        with patch(
            "ragtag_crew.external.openapi_provider.load_openapi_provider_configs",
            return_value=[provider],
        ):
            statuses = openapi_module.register_openapi_tools()

        readonly_names = [tool.name for tool in get_tools_for_preset("readonly")]
        self.assertEqual([status.key for status in statuses], ["openapi:search-gateway"])
        self.assertIn("search_gateway_query", readonly_names)

    async def test_openapi_tool_formats_search_results(self) -> None:
        provider = openapi_module.OpenAPIProviderConfig(
            name="search-gateway",
            base_url="http://127.0.0.1:8080",
            tools=(
                openapi_module.OpenAPIToolConfig(
                    name="search_gateway_query",
                    description="Search via gateway",
                    path="/search",
                    presets=("readonly",),
                    parameters={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "num_results": {"type": "integer"},
                        },
                        "required": ["query"],
                    },
                    request_body={"query": "$query", "num_results": "$num_results"},
                    result_mode="search_results",
                ),
            ),
        )

        with patch(
            "ragtag_crew.external.openapi_provider.load_openapi_provider_configs",
            return_value=[provider],
        ), patch(
            "ragtag_crew.external.openapi_provider._fetch_json_response",
            return_value={
                "results": [
                    {
                        "title": "Example title",
                        "url": "https://example.com/doc",
                        "snippet": "Example snippet",
                    }
                ]
            },
        ):
            openapi_module.register_openapi_tools()
            tool = next(tool for tool in get_tools_for_preset("readonly") if tool.name == "search_gateway_query")
            result = await tool.execute(query="hello", num_results=3)

        self.assertIn("OpenAPI search results:", result)
        self.assertIn("https://example.com/doc", result)


class MCPConfigTests(unittest.TestCase):
    def test_load_mcp_server_configs_reads_list_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "mcp_servers.local.json"
            config_path.write_text(
                json.dumps(
                    [
                        {
                            "name": "filesystem",
                            "command": "npx",
                            "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
                            "cwd": ".",
                            "env": {"FOO": "bar"},
                            "enabled": True,
                            "tool_prefix": "fs",
                            "presets": ["coding", "readonly"],
                        }
                    ]
                ),
                encoding="utf-8",
            )

            with temp_setting("mcp_servers_file", str(config_path)):
                servers = mcp_module.load_mcp_server_configs()

        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0].name, "filesystem")
        self.assertEqual(servers[0].args[-1], ".")
        self.assertEqual(servers[0].env["FOO"], "bar")
        self.assertEqual(servers[0].presets, ("coding", "readonly"))


class MCPDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        for name in list(_ALL_TOOLS):
            if name.startswith("mcp_"):
                _ALL_TOOLS.pop(name, None)

    async def test_discover_mcp_tools_registers_prefixed_tool(self) -> None:
        server = mcp_module.MCPServerConfig(
            name="filesystem",
            command="npx",
            tool_prefix="fs",
            presets=("coding",),
        )
        remote_tool = SimpleNamespace(
            name="read_file",
            description="Read file from MCP",
            inputSchema={"type": "object", "properties": {"path": {"type": "string"}}},
        )

        with patch(
            "ragtag_crew.external.mcp_client.load_mcp_server_configs",
            return_value=[server],
        ), patch(
            "ragtag_crew.external.mcp_client._list_tools_for_server",
            new=AsyncMock(return_value=[remote_tool]),
        ):
            statuses = await mcp_module.discover_mcp_tools()

        self.assertEqual(len(statuses), 1)
        self.assertTrue(statuses[0].ready)
        self.assertIn("mcp_fs_read_file", statuses[0].tool_names)
        tool_names = [tool.name for tool in get_tools_for_preset("coding")]
        self.assertIn("mcp_fs_read_file", tool_names)


class OpenAPIPathParamTests(unittest.TestCase):
    def test_path_param_replaces_variable(self) -> None:
        provider = openapi_module.OpenAPIProviderConfig(
            name="test",
            base_url="http://127.0.0.1:10001",
        )
        tool = openapi_module.OpenAPIToolConfig(
            name="paper_collection_get",
            description="Get collection",
            path="/api/collections/$collection_id",
            method="GET",
        )
        url = openapi_module._build_url(provider, tool, {"collection_id": 42})
        self.assertEqual(url, "http://127.0.0.1:10001/api/collections/42")

    def test_path_param_long_key_does_not_corrupt_short_key(self) -> None:
        provider = openapi_module.OpenAPIProviderConfig(
            name="test",
            base_url="http://127.0.0.1:10001",
        )
        tool = openapi_module.OpenAPIToolConfig(
            name="test",
            description="Test",
            path="/api/collections/$collection_id/items/$item_id",
            method="GET",
        )
        url = openapi_module._build_url(provider, tool, {"collection_id": 1, "item_id": 99})
        self.assertEqual(url, "http://127.0.0.1:10001/api/collections/1/items/99")

    def test_path_param_url_encodes_special_chars(self) -> None:
        provider = openapi_module.OpenAPIProviderConfig(
            name="test",
            base_url="http://127.0.0.1:10001",
        )
        tool = openapi_module.OpenAPIToolConfig(
            name="test",
            description="Test",
            path="/api/search/$query",
            method="GET",
        )
        url = openapi_module._build_url(provider, tool, {"query": "hello world"})
        self.assertEqual(url, "http://127.0.0.1:10001/api/search/hello%20world")

    def test_path_param_skips_none_values(self) -> None:
        provider = openapi_module.OpenAPIProviderConfig(
            name="test",
            base_url="http://127.0.0.1:10001",
        )
        tool = openapi_module.OpenAPIToolConfig(
            name="test",
            description="Test",
            path="/api/collections/$collection_id/items",
            method="GET",
        )
        url = openapi_module._build_url(provider, tool, {"collection_id": 5, "filter": None})
        self.assertEqual(url, "http://127.0.0.1:10001/api/collections/5/items")

    def test_path_param_preserves_query_params(self) -> None:
        provider = openapi_module.OpenAPIProviderConfig(
            name="test",
            base_url="http://127.0.0.1:10001",
        )
        tool = openapi_module.OpenAPIToolConfig(
            name="test",
            description="Test",
            path="/api/collections/$collection_id/export",
            method="GET",
            query_params={"format": "json"},
        )
        url = openapi_module._build_url(provider, tool, {"collection_id": 3})
        self.assertEqual(url, "http://127.0.0.1:10001/api/collections/3/export?format=json")

    def test_path_param_url_encodes_reserved_chars(self) -> None:
        provider = openapi_module.OpenAPIProviderConfig(
            name="test",
            base_url="http://127.0.0.1:10001",
        )
        tool = openapi_module.OpenAPIToolConfig(
            name="test",
            description="Test",
            path="/api/items/$item_id",
            method="GET",
        )
        url = openapi_module._build_url(provider, tool, {"item_id": "abc def+xyz"})
        self.assertEqual(url, "http://127.0.0.1:10001/api/items/abc%20def%2Bxyz")


class ExternalManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_initialize_external_capabilities_combines_statuses(self) -> None:
        manager_module._initialized = False
        manager_module._capability_statuses = {}
        web_status = manager_module.CapabilityStatus(
            key="web-search",
            kind="search",
            ready=True,
            tool_names=("web_search",),
        )
        everything_status = manager_module.CapabilityStatus(
            key="everything",
            kind="platform",
            ready=True,
            tool_names=("everything_search",),
        )
        browser_isolated = manager_module.CapabilityStatus(
            key="browser-isolated",
            kind="browser",
            ready=True,
            tool_names=("browser_open",),
        )
        browser_attached = manager_module.CapabilityStatus(
            key="browser-attached",
            kind="browser",
            ready=True,
            detail="detached (auto-connect)",
            tool_names=("browser_open",),
        )
        openapi_status = manager_module.CapabilityStatus(
            key="openapi:search-gateway",
            kind="openapi",
            ready=True,
            tool_names=("search_gateway_query",),
        )
        mcp_status = manager_module.CapabilityStatus(
            key="mcp:fs",
            kind="mcp",
            ready=True,
            tool_names=("mcp_fs_read_file",),
        )

        with patch(
            "ragtag_crew.external.manager.register_web_search_tool",
            return_value=web_status,
        ), patch(
            "ragtag_crew.external.manager.register_everything_tool",
            return_value=everything_status,
        ), patch(
            "ragtag_crew.external.manager.register_browser_tools",
            return_value=[browser_isolated, browser_attached],
        ), patch(
            "ragtag_crew.external.manager.register_openapi_tools",
            return_value=[openapi_status],
        ), patch(
            "ragtag_crew.external.manager.discover_mcp_tools",
            new=AsyncMock(return_value=[mcp_status]),
        ):
            statuses = await manager_module.initialize_external_capabilities(force=True)

        self.assertEqual(
            [status.key for status in statuses],
            ["web-search", "everything", "browser-isolated", "browser-attached", "openapi:search-gateway", "mcp:fs"],
        )
        self.assertEqual([status.key for status in manager_module.get_mcp_statuses()], ["mcp:fs"])


if __name__ == "__main__":
    unittest.main()
