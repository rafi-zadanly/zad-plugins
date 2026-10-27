import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import ListRootsResult, Root
from test_runtime import FAKE_ODOO
from test_diagnostics import FAKE_DIAGNOSTIC_ODOO


class MCPWorkspaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_diagnostics_and_access_over_stdio(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / ".git").mkdir()
            (project / ".venv/bin").mkdir(parents=True)
            (project / ".venv/bin/python").symlink_to(sys.executable)
            (project / "odoo-bin").write_text(FAKE_DIAGNOSTIC_ODOO)
            (project / "odoo.conf").write_text("[options]\ndb_name = test_db\n")
            addon = project / "addons/sample"
            addon.mkdir(parents=True)
            (addon / "__manifest__.py").write_text("{}")
            (addon / "__init__.py").write_text("from . import model\n")
            (addon / "model.py").write_text("class A:\n    _name = 'missing.model'\n")
            env = {key: value for key, value in os.environ.items() if not key.startswith("ODOO_MCP_") and key != "ODOO_SOURCE_ROOT"}
            params = StdioServerParameters(command=sys.executable, args=["-m", "odoo_source_mcp.server"], env=env)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    response = await session.call_tool("diagnose_model", {"workspace": str(project), "model": "missing.model"})
                    self.assertFalse(response.isError, response.content)
                    data = response.structuredContent or json.loads(response.content[0].text)
                    self.assertTrue(data["source"]["found"])
                    self.assertFalse(data["runtime"]["result"]["registry"]["present"])
                    self.assertEqual(data["source"]["items"][0]["import_trace"]["status"], "statically_reachable")
                    response = await session.call_tool("inspect_model_access", {"workspace": str(project), "model": "present.model", "user_id": 7, "company_ids": [3], "record_ids": [11]})
                    self.assertFalse(response.isError, response.content)
                    data = response.structuredContent or json.loads(response.content[0].text)
                    self.assertEqual(data["result"]["company_id"], 3)
                    self.assertFalse(data["result"]["records"][0]["record_rules"]["write"])
                    (project / "odoo-bin").unlink()
                    response = await session.call_tool("diagnose_model", {"workspace": str(project), "model": "missing.model"})
                    data = response.structuredContent or json.loads(response.content[0].text)
                    self.assertTrue(data["source"]["found"])
                    self.assertEqual(data["findings"][0]["kind"], "runtime_unknown")
                    self.assertIsNone(data["runtime"]["result"]["registry"]["present"])
                    self.assertIsNone(data["runtime"]["result"]["metadata"]["present"])

    async def test_shell_tools_over_stdio(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / ".git").mkdir()
            (project / "odoo-bin").write_text(FAKE_ODOO)
            (project / "odoo.conf").write_text("[options]\ndb_name = test_db\naddons_path = addons, missing\n")
            (project / ".odoo-mcp.toml").write_text('[runtime]\nworking_directory = "runtime"\n')
            (project / "runtime/addons").mkdir(parents=True)
            (project / ".venv/bin").mkdir(parents=True)
            (project / ".venv/bin/python").symlink_to(sys.executable)
            env = {key: value for key, value in os.environ.items() if not key.startswith("ODOO_MCP_") and key != "ODOO_SOURCE_ROOT"}
            params = StdioServerParameters(command=sys.executable, args=["-m", "odoo_source_mcp.server"], env=env)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                    self.assertTrue(tools["execute_odoo_expression"].annotations.destructiveHint)
                    status = await session.call_tool("odoo_runtime_status", {"workspace": str(project)})
                    self.assertFalse(status.isError, status.content)
                    status_data = status.structuredContent or json.loads(status.content[0].text)
                    self.assertEqual(status_data["database"], "test_db")
                    self.assertEqual(status_data["working_directory"], str((project / "runtime").resolve()))
                    self.assertEqual(status_data["sources"]["working_directory"]["kind"], "project_file")
                    self.assertEqual(status_data["config_addons_path"]["entries"][0]["resolved"], str((project / "runtime/addons").resolve()))
                    self.assertTrue(status_data["config_addons_path"]["entries"][0]["exists"])
                    self.assertFalse(status_data["config_addons_path"]["entries"][1]["exists"])
                    result = await session.call_tool("execute_odoo_expression", {"workspace": str(project), "expression": "env['res.partner'].search_count([])"})
                    self.assertFalse(result.isError)
                    result_data = result.structuredContent or json.loads(result.content[0].text)
                    self.assertEqual(result_data["result"], 2)
                    rejected = await session.call_tool("execute_odoo_expression", {"workspace": str(project), "expression": "env.cr.commit()"})
                    self.assertTrue(rejected.isError)

    async def test_roots_and_explicit_workspace_are_isolated(self):
        with tempfile.TemporaryDirectory(prefix="odoo%20test-") as tmp:
            base = Path(tmp)
            for project in ("one", "two"):
                module = base / project / "addons" / project
                module.mkdir(parents=True)
                (base / project / ".git").mkdir()
                (module / "__manifest__.py").write_text("{}")
                (module / "model.py").write_text(f'class A:\n    _name = "{project}.model"\n    def open(self): pass\n')
                (module / "broken.py").write_text('def broken(:\n')
                (module / "views.xml").write_text(f'''<odoo>
<record id="view" model="ir.ui.view"><field name="model">{project}.model</field></record>
<record id="action" model="ir.actions.act_window"><field name="res_model">{project}.model</field></record>
<menuitem id="menu" action="action"/>
</odoo>''')

            async def roots(context):
                return ListRootsResult(roots=[Root(uri=(base / "one").as_uri())])

            env = {key: value for key, value in os.environ.items() if key not in ("ODOO_ADDONS_PATH", "ODOO_SOURCE_ROOT")}
            params = StdioServerParameters(command=sys.executable, args=["-m", "odoo_source_mcp.server"], env=env)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write, list_roots_callback=roots) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    self.assertTrue({"inspect_runtime_fields", "find_automation_triggers"}.issubset({tool.name for tool in listed.tools}))
                    response = await session.call_tool("find_automation_triggers", {"workspace": str(base / "one"), "module": "one"})
                    self.assertFalse(response.isError, response.content)
                    self.assertTrue({"find_state_assignments", "find_object_buttons"}.issubset({tool.name for tool in listed.tools}))
                    for tool, args in (("find_state_assignments", {"model": "one.model"}),
                                       ("find_object_buttons", {"model": "one.model"}),
                                       ("trace_method_calls", {"model": "one.model", "method": "open", "caller_module": "one"})):
                        response = await session.call_tool(tool, {**args, "workspace": str(base / "one")})
                        self.assertFalse(response.isError, response.content)
                    self.assertTrue({"trace_model", "trace_view", "find_method", "trace_action"}.issubset({tool.name for tool in listed.tools}))
                    self.assertTrue({"discover_models", "find_models"}.issubset({tool.name for tool in listed.tools}))
                    self.assertTrue({"inspect_module", "inspect_field_behavior", "trace_method_calls"}.issubset({tool.name for tool in listed.tools}))
                    for tool, args in (("inspect_module", {"module": "one"}),
                                       ("inspect_field_behavior", {"model": "one.model", "field": "name"}),
                                       ("trace_method_calls", {"model": "one.model", "method": "open"})):
                        response = await session.call_tool(tool, {**args, "workspace": str(base / "one")})
                        self.assertFalse(response.isError, response.content)
                    response = await session.call_tool("discover_models", {"workspace": str(base / "one"), "module": "one", "limit": 1})
                    self.assertFalse(response.isError, response.content)
                    data = response.structuredContent or json.loads(response.content[0].text)
                    self.assertEqual(data["items"][0]["model"], "one.model")
                    self.assertEqual(data["index_error_count"], 1)
                    self.assertIn("source_status", data["index_error_note"])
                    response = await session.call_tool("find_models", {"workspace": str(base / "one"), "models": ["one.model", "missing.model"], "limit": 100})
                    self.assertFalse(response.isError, response.content)
                    data = response.structuredContent or json.loads(response.content[0].text)
                    self.assertEqual([item["total"] for item in data["results"]], [1, 0])
                    self.assertEqual(data["limit_per_model"], 10)
                    response = await session.call_tool("find_models", {"workspace": str(base / "one"), "models": ["one.model"] * 21})
                    self.assertTrue(response.isError)
                    for model, workspace, expected in (("one.model", None, 1), ("two.model", str(base / "two"), 1), ("one.model", str(base / "two"), 0)):
                        args = {"model": model}
                        if workspace:
                            args["workspace"] = workspace
                        response = await session.call_tool("find_model", args)
                        self.assertFalse(response.isError)
                        data = response.structuredContent or json.loads(response.content[0].text)
                        self.assertEqual(data["total"], expected)
                    for tool, args, collection in (("trace_model", {"model": "one.model"}, "nodes"),
                                                   ("trace_view", {"xml_id": "one.view"}, "views"),
                                                   ("find_method", {"model": "one.model", "method": "open"}, "definitions"),
                                                   ("trace_action", {"xml_id": "one.action"}, "menus")):
                        response = await session.call_tool(tool, {**args, "workspace": str(base / "one")})
                        self.assertFalse(response.isError, tool)
                        data = response.structuredContent or json.loads(response.content[0].text)
                        self.assertEqual(data[collection]["total"], 1, tool)

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
