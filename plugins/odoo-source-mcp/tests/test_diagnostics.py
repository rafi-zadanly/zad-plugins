import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from odoo_source_mcp.diagnostics import diagnose_runtime, inspect_access, inspect_runtime_fields, observations, source_diagnostic
from odoo_source_mcp.index import SourceIndex
from odoo_source_mcp.runtime import inspect_runtime, resolve_runtime
from odoo_source_mcp.shell import execute


FAKE_DIAGNOSTIC_ODOO = '''import os, sys, types
assert "default_transaction_read_only=on" in os.environ.get("PGOPTIONS", "")
odoo = types.ModuleType("odoo")
odoo.release = types.ModuleType("odoo.release")
odoo.release.version = "16.0"
sys.modules["odoo.release"] = odoo.release
exceptions = types.ModuleType("odoo.exceptions")
class AccessError(Exception): pass
class MissingError(Exception): pass
exceptions.AccessError = AccessError
exceptions.MissingError = MissingError
odoo.exceptions = exceptions
sys.modules["odoo"] = odoo
sys.modules["odoo.exceptions"] = exceptions
class Cursor:
    dbname = "fixture_db"
    def commit(self): pass
    def rollback(self): pass
class Companies:
    def __init__(self, ids): self.ids = ids
class Company:
    def __init__(self, id): self.id = id
class User:
    def __init__(self, id):
        self.id = id
        self.company_id = Company(2)
        self.company_ids = Companies([2, 3])
        self.groups_id = Companies([5])
    def exists(self): return self if self.id in (1, 7) else None
class Env:
    def __init__(self, uid=1, companies=None):
        self.cr = Cursor()
        self.uid = uid
        self.su = uid == 1
        self.companies = Companies(companies or [2])
        self.company = Company(self.companies.ids[0])
    def __contains__(self, model): return model == "present.model"
    def __getitem__(self, model):
        if model == "res.users": return Users()
        if model == "ir.model.access": return Access(self)
        if model == "ir.rule": return Rules()
        return Model(self, model)
class Access:
    def __init__(self, env): self.env = env
    def check(self, model, operation, raise_exception=False): return self.env.su or operation in ("read", "create")
    def sudo(self): return self
    def search(self, domain, limit):
        return [types.SimpleNamespace(id=1, name="ACL", group_id=Company(5), perm_read=True, perm_create=False, perm_write=False, perm_unlink=False)]
class Rules:
    def _compute_domain(self, model, operation): return [("operation", "=", operation)]
    def sudo(self): return self
    def search(self, domain, limit):
        return [types.SimpleNamespace(id=2, name="Rule", groups=Companies([5]), domain_force="[]", perm_read=True, perm_create=False, perm_write=False, perm_unlink=False)]
class Users:
    def browse(self, id): return User(id)
class Model:
    def __init__(self, env, name, ids=None):
        self.env, self.name, self._name, self.ids = env, name, name, ids or []
    def __bool__(self): return bool(self.ids)
    def search_read(self, domain, fields, limit):
        if self.name == "ir.model": return [{"id": 9, "model": domain[0][2], "name": "Model", "state": "base"}]
        if self.name == "ir.model.data": return [{"module": "metadata_origin", "name": "model_missing", "res_id": 9}]
        if self.name == "ir.module.module": return [{"name": name, "state": "installed"} for name in domain[0][2]]
        raise ValueError("unexpected model")
    def with_user(self, uid): return Model(Env(uid), self.name, self.ids)
    def with_context(self, context, allowed_company_ids): return Model(Env(self.env.uid, allowed_company_ids), self.name, self.ids)
    def browse(self, ids): return Model(self.env, self.name, ids if isinstance(ids, list) else [ids])
    def exists(self): return Model(self.env, self.name, [i for i in self.ids if i == 11])
    def sudo(self): return self
    def filtered_domain(self, domain): return Model(self.env, self.name, [] if domain and domain[0][2] == "write" else self.ids)
    def fields_get(self, names, attributes): return {name: {"type": "char", "readonly": False} for name in names if name == "name"}
env = Env()
exec(sys.stdin.read())
env.cr.rollback()
'''


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / ".git").mkdir()
        (self.root / ".venv/bin").mkdir(parents=True)
        (self.root / ".venv/bin/python").symlink_to(sys.executable)
        (self.root / "odoo-bin").write_text(FAKE_DIAGNOSTIC_ODOO)
        (self.root / "odoo.conf").write_text("[options]\ndb_name = fixture_db\n")
        self.env = patch.dict(os.environ, {key: value for key, value in os.environ.items() if not key.startswith("ODOO_MCP_")}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def addon(self):
        module = self.root / "addons" / "sample"
        (module / "models").mkdir(parents=True)
        (module / "__manifest__.py").write_text("{'depends': ['base']}")
        (module / "__init__.py").write_text("from . import models\n")
        (module / "models/__init__.py").write_text("from . import model\n")
        (module / "models/model.py").write_text("class Model:\n    _name = 'missing.model'\n")
        return module

    def test_source_imports_and_registry_disagreement(self):
        self.addon()
        index = SourceIndex([self.root / "addons"])
        index.refresh()
        source = source_diagnostic(index, "missing.model")
        self.assertTrue(source["found"])
        self.assertEqual(source["items"][0]["manifest"]["depends"], ["base"])
        self.assertEqual(source["items"][0]["import_trace"]["status"], "statically_reachable")
        self.assertEqual(len(source["items"][0]["import_trace"]["chain"]), 2)
        runtime = diagnose_runtime(resolve_runtime(self.root), "missing.model", ["sample"])
        self.assertTrue(runtime["ok"])
        self.assertFalse(runtime["result"]["registry"]["present"])
        self.assertTrue(runtime["result"]["metadata"]["present"])
        self.assertEqual(runtime["result"]["modules"]["records"][0]["state"], "installed")
        self.assertEqual(runtime["result"]["modules"]["candidate_names"], ["metadata_origin", "sample"])
        self.assertEqual({item["kind"] for item in observations(source, runtime)}, {"metadata_without_registry", "source_without_registry"})
        self.assertEqual(observations(source, {"ok": False})[0]["kind"], "runtime_unknown")

    def test_import_break_and_conditional_import(self):
        module = self.addon()
        (module / "models/__init__.py").write_text("# not imported here\n")
        index = SourceIndex([self.root / "addons"])
        index.refresh()
        trace = source_diagnostic(index, "missing.model")["items"][0]["import_trace"]
        self.assertEqual(trace["status"], "no_static_path_found")
        self.assertTrue(trace["issues"])
        (module / "models/__init__.py").write_text("if False:\n    from . import model\n")
        index.refresh(force=True)
        trace = source_diagnostic(index, "missing.model")["items"][0]["import_trace"]
        self.assertEqual(trace["status"], "statically_reachable")
        self.assertIn("conditional", trace["note"])

    def test_access_user_company_acl_and_record_rules(self):
        result = inspect_access(resolve_runtime(self.root), "present.model", 7, [3, 2], [11, 12])
        self.assertTrue(result["ok"], result)
        result = result["result"]
        self.assertEqual(result["user_id"], 7)
        self.assertFalse(result["superuser_mode"])
        self.assertEqual(result["company_id"], 3)
        self.assertEqual(result["allowed_company_ids"], [3, 2])
        self.assertTrue(result["acl"]["read"])
        self.assertFalse(result["acl"]["write"])
        self.assertFalse(result["records"][0]["record_rules"]["write"])
        self.assertFalse(result["records"][1]["exists"])
        invalid = inspect_access(resolve_runtime(self.root), "present.model", 7, [99], [])
        self.assertFalse(invalid["ok"])
        self.assertIn("companies", invalid["error"]["message"])
        default = inspect_access(resolve_runtime(self.root), "present.model", 7, None, [])
        self.assertEqual(default["result"]["allowed_company_ids"], [2])
        superuser = inspect_access(resolve_runtime(self.root), "present.model", 1, None, [])
        self.assertTrue(superuser["result"]["superuser_mode"])

    def test_runtime_fields_context_and_missing(self):
        result = inspect_runtime_fields(resolve_runtime(self.root), "present.model", ["name", "missing"], 7, [3])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["result"]["database"], "fixture_db")
        self.assertEqual(result["result"]["user_id"], 7)
        self.assertEqual(result["result"]["company_id"], 3)
        self.assertEqual(result["result"]["not_returned"], ["missing"])

    def test_access_evidence_is_separate_from_checks(self):
        result = inspect_access(resolve_runtime(self.root), "present.model", 7, [3], [11], include_evidence=True)
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["result"]["acl"]["write"])
        self.assertEqual(result["result"]["evidence"]["rules"][0]["name"], "Rule")
        self.assertIn("do not establish why", result["result"]["evidence"]["note"])

    def test_config_partial_and_provenance(self):
        (self.root / ".odoo-mcp.toml").write_text('[runtime]\npython = "missing/bin/python"\ndatabase = "toml_db"\n')
        os.environ["ODOO_MCP_DATABASE"] = "env_db"
        report = inspect_runtime(self.root)
        self.assertFalse(report["ready"])
        self.assertEqual(report["database"], "env_db")
        self.assertEqual(report["sources"]["database"]["kind"], "environment")
        self.assertEqual(report["sources"]["python"]["path"], str(self.root / ".odoo-mcp.toml"))
        self.assertEqual(report["sources"]["executable"]["kind"], "discovery")
        self.assertIn(str(self.root / "missing/bin/python"), report["errors"][0]["message"])
        self.assertIn(str(self.root), report["errors"][0]["message"])

    def test_shell_error_detail_is_opt_in_and_redacted(self):
        (self.root / "odoo-bin").write_text('import sys\nsys.stderr.write("OperationalError password=secret123\\n")\nsys.exit(1)\n')
        runtime = resolve_runtime(self.root)
        hidden = execute(runtime, "env['x'].search_count([])")
        self.assertEqual(hidden["error"]["stage"], "database_connection")
        self.assertNotIn("stderr_excerpt", hidden["error"])
        shown = execute(runtime, "env['x'].search_count([])", include_stderr=True)
        self.assertIn("[REDACTED]", shown["error"]["stderr_excerpt"])
        self.assertNotIn("secret123", shown["error"]["stderr_excerpt"])


if __name__ == "__main__":
    unittest.main()
