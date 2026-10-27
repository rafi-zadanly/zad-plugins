import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import ListRootsResult, Root


class MCPWorkspaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_roots_and_explicit_workspace_are_isolated(self):
        with tempfile.TemporaryDirectory(prefix="odoo%20test-") as tmp:
            base = Path(tmp)
            for project in ("one", "two"):
                module = base / project / "addons" / project
                module.mkdir(parents=True)
                (base / project / ".git").mkdir()
                (module / "__manifest__.py").write_text("{}")
                (module / "model.py").write_text(f'class A:\n    _name = "{project}.model"\n')

            async def roots(context):
                return ListRootsResult(roots=[Root(uri=(base / "one").as_uri())])

            env = {key: value for key, value in os.environ.items() if key not in ("ODOO_ADDONS_PATH", "ODOO_SOURCE_ROOT")}
            params = StdioServerParameters(command=sys.executable, args=["-m", "odoo_source_mcp.server"], env=env)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write, list_roots_callback=roots) as session:
                    await session.initialize()
                    await session.list_tools()
                    for model, workspace, expected in (("one.model", None, 1), ("two.model", str(base / "two"), 1), ("one.model", str(base / "two"), 0)):
                        args = {"model": model}
                        if workspace:
                            args["workspace"] = workspace
                        response = await session.call_tool("find_model", args)
                        self.assertFalse(response.isError)
                        data = response.structuredContent or json.loads(response.content[0].text)
                        self.assertEqual(data["total"], expected)

    async def test_missing_workspace_returns_clear_error(self):
        env = {key: value for key, value in os.environ.items() if key not in ("ODOO_ADDONS_PATH", "ODOO_SOURCE_ROOT")}
        params = StdioServerParameters(command=sys.executable, args=["-m", "odoo_source_mcp.server"], env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("source_status", {})
                self.assertTrue(result.isError)
                self.assertIn("workspace", result.content[0].text)

    async def test_invalid_addons_path_reports_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "missing")
            env = {key: value for key, value in os.environ.items() if key != "ODOO_SOURCE_ROOT"}
            env["ODOO_ADDONS_PATH"] = os.pathsep.join((tmp, missing))
            params = StdioServerParameters(command=sys.executable, args=["-m", "odoo_source_mcp.server"], env=env)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("source_status", {})
                    self.assertTrue(result.isError)
                    self.assertIn("ODOO_ADDONS_PATH", result.content[0].text)
                    self.assertIn(missing, result.content[0].text)


if __name__ == "__main__":
    unittest.main()
