import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from odoo_source_mcp.runtime import inspect_runtime, resolve_runtime
from odoo_source_mcp.shell import execute, validate_read_expression


FAKE_ODOO = '''import os, sys, time
if os.environ.get("FAKE_ODOO_SLEEP"):
    time.sleep(5)
class Cursor:
    def commit(self):
        if os.environ.get("FAKE_ODOO_LOG"):
            with open(os.environ["FAKE_ODOO_LOG"], "a") as log: log.write("commit\\n")
    def rollback(self):
        if os.environ.get("FAKE_ODOO_LOG"):
            with open(os.environ["FAKE_ODOO_LOG"], "a") as log: log.write("rollback\\n")
class Model:
    _name = "res.partner"
    ids = [1, 2]
    def search_count(self, domain):
        return 2
    def search_read(self, domain, **kwargs):
        return [{"name": "Example"}]
    def search(self, domain):
        return self
    def write(self, values):
        return True
class Env:
    cr = Cursor()
    def __getitem__(self, model):
        return Model()
env = Env()
exec(sys.stdin.read())
env.cr.rollback()
'''


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / ".git").mkdir()
        (self.root / "odoo-bin").write_text(FAKE_ODOO)
        (self.root / "odoo.conf").write_text("[options]\ndb_name = discovered_db\n")
        (self.root / ".venv/bin").mkdir(parents=True)
        (self.root / ".venv/bin/python").symlink_to(sys.executable)
        self.clean_env = {key: value for key, value in os.environ.items() if not key.startswith("ODOO_MCP_")}
        self.env_patch = patch.dict(os.environ, self.clean_env, clear=True)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def test_discovery_and_config_database(self):
        nested = self.root / "addons/module"
        nested.mkdir(parents=True)
        runtime = resolve_runtime(nested)
        self.assertEqual(runtime.python, self.root / ".venv/bin/python")
        self.assertEqual(runtime.executable, self.root / "odoo-bin")
        self.assertEqual(runtime.database, "discovered_db")

    def test_project_config_and_environment_precedence(self):
        (self.root / "addons").mkdir()
        (self.root / ".odoo-mcp.toml").write_text('''[runtime]
python = ".venv/bin/python"
executable = "odoo-bin"
config = "odoo.conf"
database = "project_db"
addons_path = ["addons"]
''')
        nested = self.root / "addons"
        os.environ["ODOO_MCP_DATABASE"] = "environment_db"
        runtime = resolve_runtime(nested)
        self.assertEqual(runtime.database, "environment_db")
        self.assertEqual(runtime.addons, (nested,))
        self.assertEqual(runtime.python, self.root / ".venv/bin/python")

    def test_shell_working_directory_and_relative_config_addons(self):
        project = self.root / "project"
        project.mkdir()
        (self.root / "addons").mkdir()
        (self.root / "custom").mkdir()
        (self.root / "odoo.conf").write_text("[options]\ndb_name = discovered_db\naddons_path = addons, custom, missing\n")
        (project / ".odoo-mcp.toml").write_text('''[runtime]
python = "../.venv/bin/python"
executable = "../odoo-bin"
config = "../odoo.conf"
database = "project_db"
working_directory = ".."
''')
        (self.root / "odoo-bin").write_text('''import os, sys
assert os.path.isdir("addons") and os.path.isdir("custom")
assert "--addons-path" not in sys.argv
''' + FAKE_ODOO)
        status = inspect_runtime(project)
        self.assertTrue(status["ready"], status)
        self.assertEqual(status["working_directory"], str(self.root))
        self.assertEqual(status["sources"]["working_directory"]["kind"], "project_file")
        self.assertEqual(status["sources"]["working_directory"]["relative_base"], str(project))
        entries = status["config_addons_path"]["entries"]
        self.assertEqual([entry["resolved"] for entry in entries], [str(self.root / name) for name in ("addons", "custom", "missing")])
        self.assertEqual([entry["exists"] for entry in entries], [True, True, False])
        self.assertEqual(status["config_addons_path"]["raw"], "addons, custom, missing")
        self.assertFalse(status["config_addons_path"]["overridden"])
        self.assertEqual(status["warnings"][0]["path"], str(self.root / "missing"))
        self.assertTrue(execute(resolve_runtime(project), "env['res.partner'].search_count([])")["ok"])

    def test_working_directory_default_environment_and_override(self):
        project = self.root / "project"
        project.mkdir()
        (self.root / "addons").mkdir()
        (self.root / "odoo.conf").write_text("[options]\ndb_name = discovered_db\naddons_path = addons, $MCP_TEST_ADDONS\n")
        (project / ".odoo-mcp.toml").write_text('''[runtime]
python = "../.venv/bin/python"
executable = "../odoo-bin"
config = "../odoo.conf"
database = "project_db"
''')
        os.environ["MCP_TEST_ADDONS"] = str(self.root / "addons")
        status = inspect_runtime(project)
        self.assertEqual(status["working_directory"], str(project))
        self.assertEqual(status["sources"]["working_directory"]["kind"], "workspace_default")
        self.assertTrue(any(w["field"] == "working_directory" for w in status["warnings"]))
        self.assertEqual(status["config_addons_path"]["entries"][0]["resolved"], str(project / "addons"))
        self.assertEqual(status["config_addons_path"]["entries"][1]["resolved"], str(self.root / "addons"))
        os.environ["ODOO_MCP_WORKING_DIRECTORY"] = ".."
        os.environ["ODOO_MCP_ADDONS_PATH"] = str(self.root / "addons")
        status = inspect_runtime(project)
        self.assertEqual(status["working_directory"], str(self.root))
        self.assertEqual(status["sources"]["working_directory"]["variable"], "ODOO_MCP_WORKING_DIRECTORY")
        self.assertTrue(status["config_addons_path"]["overridden"])
        self.assertEqual(resolve_runtime(project).addons, (self.root / "addons",))
        os.environ["ODOO_MCP_WORKING_DIRECTORY"] = "does-not-exist"
        with self.assertRaisesRegex(ValueError, "working_directory is not a directory"):
            resolve_runtime(project)

    def test_override_marks_missing_config_addons_as_ineffective(self):
        (self.root / "addons").mkdir()
        (self.root / "odoo.conf").write_text("[options]\ndb_name = db\naddons_path = missing\n")
        os.environ["ODOO_MCP_ADDONS_PATH"] = str(self.root / "addons")
        status = inspect_runtime(self.root)
        self.assertTrue(status["ready"])
        self.assertTrue(status["config_addons_path"]["overridden"])
        self.assertFalse(status["warnings"][0]["effective"])
        self.assertFalse(any(w["field"] == "working_directory" for w in status["warnings"]))

    def test_symlink_working_directory_resolves_addons_from_actual_cwd(self):
        (self.root / "actual/odoo").mkdir(parents=True)
        (self.root / "actual/addons").mkdir()
        (self.root / "linked-odoo").symlink_to(self.root / "actual/odoo", target_is_directory=True)
        (self.root / "odoo.conf").write_text("[options]\ndb_name = db\naddons_path = ../addons\n")
        (self.root / ".odoo-mcp.toml").write_text('[runtime]\nworking_directory = "linked-odoo"\n')
        status = inspect_runtime(self.root)
        entry = status["config_addons_path"]["entries"][0]
        self.assertEqual(entry["resolved"], str(self.root / "actual/addons"))
        self.assertTrue(entry["exists"])

    def test_explicit_invalid_paths_and_database_fail_closed(self):
        os.environ["ODOO_MCP_PYTHON"] = "missing"
        with self.assertRaisesRegex(ValueError, "python is not a file"):
            resolve_runtime(self.root)
        del os.environ["ODOO_MCP_PYTHON"]
        os.environ["ODOO_MCP_DATABASE"] = "one,two"
        with self.assertRaisesRegex(ValueError, "exactly one database"):
            resolve_runtime(self.root)

    def test_invalid_project_config_is_not_executed(self):
        (self.root / ".odoo-mcp.toml").write_text('[runtime]\npython = "$(touch unexpected)"\n')
        with self.assertRaisesRegex(ValueError, "python is not a file"):
            resolve_runtime(self.root)
        self.assertFalse((self.root / "unexpected").exists())
        (self.root / ".odoo-mcp.toml").write_text("[runtime\n")
        with self.assertRaisesRegex(ValueError, "Invalid"):
            resolve_runtime(self.root)

    def test_discovery_does_not_cross_git_root(self):
        child = self.root / "child"
        child.mkdir()
        (child / ".git").mkdir()
        with self.assertRaisesRegex(ValueError, "incomplete"):
            resolve_runtime(child)

    def test_read_only_allowlist(self):
        for expression in ("env['res.partner'].search_count([])", "env['res.partner'].search([]).ids", "env['res.partner'].search_read([], fields=['name'], limit=5)"):
            validate_read_expression(expression)
        for expression in ("env.cr.commit()", "env['res.partner'].create({'name':'x'})", "env['res.partner'].search([]).write({'name':'x'})", "env['res.partner'].search([__import__('os')])", "env['res.partner'].search([]).__class__"):
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                validate_read_expression(expression)

    def test_shell_results_mutation_commit_and_errors(self):
        runtime = resolve_runtime(self.root)
        log = self.root / "transactions.log"
        os.environ["FAKE_ODOO_LOG"] = str(log)
        result = execute(runtime, "env['res.partner'].search_read([], fields=['name'])")
        self.assertEqual(result, {"ok": True, "result": [{"name": "Example"}], "committed": False})
        self.assertEqual(execute(runtime, "env['res.partner'].search([])")["result"], {"model": "res.partner", "ids": [1, 2]})
        with self.assertRaisesRegex(ValueError, "commit requires"):
            execute(runtime, "env['res.partner'].search_count([])", commit=True)
        expression = "env['res.partner'].search([]).write({'name': 'Updated'})"
        with self.assertRaises(ValueError):
            execute(runtime, expression)
        self.assertFalse(execute(runtime, expression, allow_mutation=True)["committed"])
        self.assertNotIn("commit", log.read_text())
        self.assertTrue(execute(runtime, expression, allow_mutation=True, commit=True)["committed"])
        self.assertEqual(log.read_text().count("commit"), 1)
        blocked = execute(runtime, "env.cr.commit()", allow_mutation=True)
        self.assertEqual(blocked["error"]["message"], "Commit is disabled for this call")
        error = execute(runtime, "env['res.partner'].search_count([]) / 0", allow_mutation=True)
        self.assertEqual(error["error"]["type"], "ZeroDivisionError")

    def test_timeout_and_runtime_environment(self):
        runtime = resolve_runtime(self.root)
        os.environ["FAKE_ODOO_SLEEP"] = "1"
        result = execute(runtime, "env['res.partner'].search_count([])", timeout=1)
        self.assertEqual(result["error"]["type"], "Timeout")
        result = execute(runtime, "env['res.partner'].search_count([])", allow_mutation=True, commit=True, timeout=1)
        self.assertIsNone(result["committed"])
        del os.environ["FAKE_ODOO_SLEEP"]
        script = (self.root / "odoo-bin")
        script.write_text('''import os, sys
assert "-c default_transaction_read_only=on" in os.environ["PGOPTIONS"]
assert "PYTHONPATH" not in os.environ and "VIRTUAL_ENV" not in os.environ
''' + FAKE_ODOO)
        os.environ["PYTHONPATH"] = "/bad/path"
        os.environ["VIRTUAL_ENV"] = "/bad/venv"
        self.assertTrue(execute(runtime, "env['res.partner'].search_count([])")["ok"])


if __name__ == "__main__":
    unittest.main()
