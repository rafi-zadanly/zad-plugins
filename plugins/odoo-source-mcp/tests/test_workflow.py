import tempfile
import unittest
from pathlib import Path

from odoo_source_mcp.index import SourceIndex
from odoo_source_mcp.inspection import automation_triggers, method_calls, object_buttons, state_assignments


class WorkflowEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "addons"
        self.module = self.root / "demo"
        self.module.mkdir(parents=True)
        (self.module / "__manifest__.py").write_text("{}")
        (self.module / "model.py").write_text('''class Request:
    _name = "demo.request"
    def approve(self):
        self.state = "approved"
        self.write({"state": next_state})
        vals = {"state": "done"}
        self.write(vals)
        changed = {"state": "wrong"}
        changed["state"] = "other"
        self.write(changed)
        self.status = "confirmed"
    def caller(self): self.approve()
class Other:
    _name = "demo.other"
    def trigger(self):
        self.env["demo.request"].approve()
        self.other.approve()
''')
        (self.module / "views.xml").write_text('''<odoo>
<record id="base" model="ir.ui.view"><field name="model">demo.request</field><field name="arch" type="xml"><form><button name="approve" type="object"/></form></field></record>
<record id="inherited" model="ir.ui.view"><field name="inherit_id" ref="base"/><field name="arch" type="xml"><xpath expr="//form" position="inside"><button name="approve" type="object"/><field name="line_ids"><tree><button name="approve" type="object"/></tree></field></xpath></field></record>
<record id="missing" model="ir.ui.view"><field name="inherit_id" ref="unknown"/><field name="arch" type="xml"><xpath expr="//form" position="inside"><button name="approve" type="object"/></xpath></field></record>
</odoo>''')
        self.index = SourceIndex([self.root])
        self.index.refresh()

    def tearDown(self):
        self.tmp.cleanup()

    def test_state_syntax_dynamic_value_and_variable_safety(self):
        result = state_assignments(self.index, "demo.request")
        self.assertEqual([x["value"] for x in result["items"]], ["approved", None, "done"])
        self.assertEqual(result["items"][1]["expression"], "next_state")
        self.assertFalse(result["items"][1]["value_resolved"])
        self.assertEqual(result["items"][2]["syntax"], "write_variable_dict")
        self.assertEqual(result["unresolved_writes"]["items"][0]["argument"], "changed")
        self.assertEqual(state_assignments(self.index, "demo.request", field="status")["items"][0]["value"], "confirmed")
        self.assertEqual(state_assignments(self.index, "demo.request", method="caller")["total"], 0)
        self.assertEqual(state_assignments(self.index, "demo.request", limit=1)["next_offset"], 1)

    def test_object_buttons_and_inherited_ambiguity(self):
        result = object_buttons(self.index, "demo.request", "approve")
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["items"][0]["method_candidates"]["total"], 1)
        self.assertEqual(result["items"][1]["model_relation"], "inferred_view_inheritance")
        self.assertEqual(result["items"][2]["model_relation"], "ambiguous_nested_view")
        self.assertEqual(result["unresolved_buttons"]["total"], 2)

    def test_cross_model_is_scoped_and_explicit(self):
        local = method_calls(self.index, "demo.request", "approve")
        self.assertEqual(local["callers"]["total"], 1)
        scoped = method_calls(self.index, "demo.request", "approve", caller_module="demo")
        self.assertEqual(scoped["callers"]["total"], 2)
        self.assertEqual(scoped["callers"]["items"][1]["caller_models"], ["demo.other"])
        self.assertEqual(method_calls(self.index, "demo.request", "approve", caller_module="absent")["callers"]["total"], 1)
        with self.assertRaises(ValueError):
            method_calls(self.index, "demo.request", "approve", caller_folder="relative")

    def test_inherited_button_override_same_name_and_helper_evidence(self):
        (self.module / "extension.py").write_text('''class RequestExtension:
    _inherit = "demo.request"
    def approve(self):
        self._finish_request()
        return super().approve()
    def _finish_request(self):
        self.write({"state": "done"})
class OtherExtension:
    _name = "demo.unrelated"
    def approve(self):
        self.state = "wrong"
    def trigger(self):
        self.approve()
        self.env["demo.unrelated"].approve()
        self.env["demo.request"].approve()
''')
        self.index.refresh()
        buttons = object_buttons(self.index, "demo.request", "approve")
        inherited = next(b for b in buttons["items"] if b["view"] == "demo.inherited" and not b["nested_field"])
        defs = inherited["method_candidates"]["items"]
        self.assertEqual({item["class"] for item in defs}, {"Request", "RequestExtension"})
        self.assertTrue(all("demo.request" in item["models"] for item in defs))
        calls = method_calls(self.index, "demo.request", "approve", caller_module="demo")
        scoped = [item for item in calls["callers"]["items"] if item.get("caller_models") == ["demo.unrelated"]]
        self.assertEqual(len(scoped), 1)
        self.assertEqual(scoped[0]["target_model"], "demo.request")
        helper = method_calls(self.index, "demo.request", "_finish_request")
        self.assertEqual([(c["caller"], c["method"]) for c in helper["callers"]["items"]], [("approve", "_finish_request")])
        states = state_assignments(self.index, "demo.request")
        evidence = [item for item in states["items"] if item["method"] == "_finish_request"]
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["value"], "done")
        self.assertNotIn("button", evidence[0])
        self.assertIn("not a runtime transition", states["note"])
        self.assertFalse(any(item["value"] == "wrong" for item in states["items"]))

    def test_relations_inheritance_ambiguity_and_unrelated_calls(self):
        (self.module / "relations.py").write_text('''class Base:
    _name = "demo.base"
    request_id = fields.Many2one("demo.request")
class Child:
    _name = "demo.child"
    _inherit = "demo.base"
    def go(self): self.request_id.approve()
class Other:
    _name = "demo.other2"
    request_id = fields.Many2one("demo.unrelated")
    def go(self): self.request_id.approve()
class Extension:
    _inherit = "demo.base"
    request_id = fields.Many2one("demo.alternative")
''')
        self.index.refresh()
        calls = method_calls(self.index, "demo.request", "approve", caller_module="demo")
        child = next(c for c in calls["callers"]["items"] if c.get("caller_models") == ["demo.child"])
        self.assertEqual(child["relationship"], "ambiguous_relational_field")
        self.assertEqual(set(child["target_candidates"]), {"demo.request", "demo.alternative"})
        self.assertEqual(child["field_declarations"]["total"], 2)
        self.assertFalse(any(c.get("caller_models") == ["demo.other2"] for c in calls["callers"]["items"]))

    def test_automation_and_ui_conditions(self):
        (self.module / "automation.xml").write_text('''<odoo>
<record id="cron" model="ir.cron"><field name="model_id" ref="model_demo_request"/><field name="code">model.approve()</field></record>
<record id="server_action" model="ir.actions.server"><field name="model_id" ref="model_demo_request"/><field name="code">
    if records:
        records.approve()
</field></record>
<record id="view_conditions" model="ir.ui.view"><field name="model">demo.request</field><field name="arch" type="xml"><form><button name="approve" type="object" groups="base.group_user" states="draft"/></form></field></record>
</odoo>''')
        self.index.refresh()
        result = automation_triggers(self.index, model="demo.request")
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["items"][0]["method_calls"]["items"][0]["method"], "approve")
        self.assertEqual(result["items"][0]["model_relationship"], "unresolved_model_reference")
        self.assertEqual(result["items"][1]["method_calls"]["items"][0]["method"], "approve")
        button = next(b for b in object_buttons(self.index, "demo.request")["items"] if b["view"] == "demo.view_conditions")
        self.assertEqual(button["conditions"]["states"], "draft")
        view = self.index.trace_view("demo.view_conditions")["views"]["items"][0]
        self.assertEqual(view["conditions"]["items"][0]["conditions"]["groups"], "base.group_user")


if __name__ == "__main__":
    unittest.main()
