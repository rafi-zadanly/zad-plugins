import tempfile
import unittest
from pathlib import Path

from odoo_source_mcp.index import SourceIndex
from odoo_source_mcp.inspection import field_behavior, inspect_module, method_calls


class InspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addons = Path(self.temp.name) / "addons"
        self.module = self.addons / "demo"
        self.module.mkdir(parents=True)
        (self.module / "__manifest__.py").write_text("{'depends': ['base'], 'data': ['views.xml']}\n")
        (self.module / "__init__.py").write_text('from . import models\n')
        (self.module / "models.py").write_text('''class Thing:
    _name = "demo.thing"
    amount = fields.Float(compute="_compute_amount", inverse="_inverse_amount", search="_search_amount", related="other.amount")
    @api.depends("other.amount", "amount")
    def _compute_amount(self):
        self.action_confirm()
        self.env["other.model"].process()
        self.partner_id.process()
    @api.onchange("amount")
    def _onchange_amount(self): pass
    @api.constrains("amount")
    def _check_amount(self): pass
    def action_confirm(self): pass
''')
        (self.module / "unloaded.py").write_text('class Unloaded:\n    _name = "demo.unloaded"\n')
        (self.module / "helper.py").write_text('def helper(): pass\n')
        self.index = SourceIndex([self.addons])
        self.index.refresh()

    def tearDown(self):
        self.temp.cleanup()

    def test_module_manifest_import_paths_and_limit(self):
        result = inspect_module(self.index, "demo")["items"][0]
        self.assertEqual(result["depends"], ["base"])
        self.assertEqual(result["data"], ["views.xml"])
        paths = {Path(t["target"]).name: t for t in result["import_candidates"]}
        self.assertEqual(paths["models.py"]["status"], "statically_reachable")
        self.assertEqual(paths["unloaded.py"]["status"], "no_static_path_found")
        self.assertEqual(paths["helper.py"]["status"], "no_static_path_found")
        self.assertTrue(inspect_module(self.index, "demo", 1)["items"][0]["source_files_truncated"])
        self.assertEqual(inspect_module(self.index, "missing")["total"], 0)
        first = inspect_module(self.index, "demo", limit=2)["items"][0]
        second = inspect_module(self.index, "demo", limit=2, offset=first["source_files_next_offset"])["items"][0]
        self.assertEqual(len(first["import_candidates"]) + len(second["import_candidates"]), first["source_files_total"])
        self.assertIsNone(second["source_files_next_offset"])
        with self.assertRaises(ValueError):
            inspect_module(self.index, "demo", offset=-1)

    def test_changed_or_unreadable_indexed_class_reports_issue(self):
        (self.module / "models.py").write_text("def broken(:\n")
        for result in (field_behavior(self.index, "demo.thing", "amount"), method_calls(self.index, "demo.thing", "action_confirm")):
            self.assertEqual(result["inspection_issues"]["total"], 1)
            self.assertEqual(result["inspection_issues"]["items"][0]["reason"], "SyntaxError")
        (self.module / "models.py").write_text("class Different: pass\n")
        result = field_behavior(self.index, "demo.thing", "amount")
        self.assertEqual(result["inspection_issues"]["items"][0]["reason"], "indexed class no longer found")

    def test_field_declarations_and_decorators(self):
        result = field_behavior(self.index, "demo.thing", "amount")
        attrs = result["definitions"]["items"][0]["attributes"]
        self.assertEqual(attrs["inverse"], "_inverse_amount")
        self.assertEqual(attrs["related"], "other.amount")
        names = {m["method"] for m in result["methods"]["items"]}
        self.assertEqual(names, {"_compute_amount", "_onchange_amount", "_check_amount"})
        self.assertEqual(field_behavior(self.index, "demo.thing", "missing")["definitions"]["total"], 0)

    def test_dynamic_field_arguments_and_decorator_match_are_explicit(self):
        (self.module / "dynamic.py").write_text('''class Extension:
    _inherit = "demo.thing"
    total = fields.Float(compute=compute_method, related=dynamic_path)
    @api.depends("amount", dynamic_dependency)
    def _compute_amount(self): pass
''')
        self.index.refresh()
        result = field_behavior(self.index, "demo.thing", "total")
        self.assertEqual(result["definitions"]["items"][0]["unresolved_attributes"], ["compute", "related"])
        amount = field_behavior(self.index, "demo.thing", "amount")
        decorator = next(m for m in amount["methods"]["items"] if m["path"].endswith("dynamic.py"))["decorators"][0]
        self.assertTrue(decorator["dynamic_arguments"])
        self.assertTrue(decorator["matches_field"])

    def test_calls_mark_receiver_confidence_and_scope(self):
        result = method_calls(self.index, "demo.thing", "_compute_amount")
        calls = result["callees"]["items"]
        self.assertEqual([c["relationship"] for c in calls], ["self_syntax", "explicit_env_model_syntax", "unresolved_receiver"])
        self.assertEqual(calls[1]["target_model"], "other.model")
        self.assertEqual(method_calls(self.index, "demo.thing", "action_confirm")["callers"]["total"], 1)
        self.assertTrue(method_calls(self.index, "demo.thing", "_compute_amount", 1)["callees"]["truncated"])
        first = method_calls(self.index, "demo.thing", "_compute_amount", 1)["callees"]
        second = method_calls(self.index, "demo.thing", "_compute_amount", 1, callee_offset=first["next_offset"])["callees"]
        self.assertEqual(first["items"][0]["method"], "action_confirm")
        self.assertEqual(second["items"][0]["target_model"], "other.model")
        with self.assertRaises(ValueError):
            method_calls(self.index, "demo.thing", "action_confirm", caller_offset=-1)

    def test_nested_scopes_are_not_attributed_to_outer_method(self):
        (self.module / "nested.py").write_text('''class Extension:
    _inherit = "demo.thing"
    def outer(self):
        self.actual()
        def nested():
            self.hidden()
        callback = lambda: self.also_hidden()
        class Inner:
            def inner(self): self.other()
''')
        self.index.refresh()
        result = method_calls(self.index, "demo.thing", "outer")
        self.assertEqual([call["method"] for call in result["callees"]["items"]], ["actual"])


if __name__ == "__main__":
    unittest.main()
