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
from ragtag_crew.external import manager as manager_module
from ragtag_crew.external import mcp_client as mcp_module
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


class ExternalManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_initialize_external_capabilities_combines_statuses(self) -> None:
        manager_module._initialized = False
        manager_module._capability_statuses = {}
        everything_status = manager_module.CapabilityStatus(
            key="everything",
            kind="platform",
            ready=True,
            tool_names=("everything_search",),
        )
        mcp_status = manager_module.CapabilityStatus(
            key="mcp:fs",
            kind="mcp",
            ready=True,
            tool_names=("mcp_fs_read_file",),
        )

        with patch(
            "ragtag_crew.external.manager.register_everything_tool",
            return_value=everything_status,
        ), patch(
            "ragtag_crew.external.manager.discover_mcp_tools",
            new=AsyncMock(return_value=[mcp_status]),
        ):
            statuses = await manager_module.initialize_external_capabilities(force=True)

        self.assertEqual([status.key for status in statuses], ["everything", "mcp:fs"])
        self.assertEqual([status.key for status in manager_module.get_mcp_statuses()], ["mcp:fs"])


if __name__ == "__main__":
    unittest.main()
