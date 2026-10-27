import tempfile
import unittest
from pathlib import Path

from odoo_source_mcp.index import SourceIndex


class SourceIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.addons = root / "addons"
        self.module = self.addons / "demo"
        self.module.mkdir(parents=True)
        (self.module / "__manifest__.py").write_text("{'depends': ['base'], 'data': ['views.xml']}\n")
        (self.module / "models.py").write_text('''from odoo import models, fields
class Thing(models.Model):
    _name = "demo.thing"
    partner_id = fields.Many2one("res.partner")
    line_ids = fields.One2many(comodel_name="demo.line", inverse_name="thing_id")
    def action_open(self): pass
    def action_close(self): pass
class Extended(models.Model):
    _inherit = "demo.thing"
    extra = fields.Char()
class Derived(models.Model):
    _name = "demo.derived"
    _inherit = "demo.thing"
''')
        (self.module / "views.xml").write_text('''<odoo>
  <menuitem id="root" name="Root"/>
  <menuitem id="things" name="Things" parent="root" action="thing_action"/>
  <record id="thing_action" model="ir.actions.act_window">
    <field name="res_model">demo.thing</field>
  </record>
  <record id="thing_form" model="ir.ui.view">
    <field name="model">demo.thing</field>
    <field name="arch" type="xml"><form><field name="partner_id"/></form></field>
  </record>
</odoo>
''')
        self.index = SourceIndex([self.addons])
        self.index.refresh()

    def tearDown(self):
        self.temp.cleanup()

    def test_model_inheritance_and_field_locations(self):
        models = self.index.model("demo.thing")["items"]
        self.assertEqual([c["class"] for c in models], ["Thing", "Extended"])
        self.assertEqual(self.index.model("demo.derived")["items"][0]["inherit"], ["demo.thing"])
        field = self.index.field("demo.thing", "partner_id")
        self.assertEqual(field["definitions"]["items"][0]["comodel"], "res.partner")
        self.assertEqual(field["definitions"]["items"][0]["line"], 4)
        self.assertEqual(field["view_candidates"]["items"][0]["line"], 9)
        lines = self.index.field("demo.thing", "line_ids")["definitions"]["items"][0]
        self.assertEqual((lines["comodel"], lines["inverse_name"]), ("demo.line", "thing_id"))

    def test_menu_action_xml_refs_and_refresh(self):
        result = self.index.menus("Things")["items"][0]
        self.assertEqual(result["parent_id"], "demo.root")
        self.assertEqual(result["model_candidates"], ["demo.thing"])
        self.assertEqual(result["view_candidates"][0]["xml_id"], "demo.thing_form")
        self.assertEqual(self.index.xml_id("demo.thing_action")["references"]["items"][0]["line"], 3)
        self.assertFalse(self.index.refresh())
        (self.module / "new.xml").write_text('<odoo><menuitem id="new" name="New"/></odoo>')
        self.assertTrue(self.index.refresh())
        self.assertEqual(self.index.menus("New")["total"], 1)

    def test_parse_errors_and_limits(self):
        (self.module / "broken.py").write_text("def broken(:\n")
        self.index.refresh()
        self.assertEqual(self.index.status()["parse_error_count"], 1)
        self.assertTrue(self.index.menus("", 1)["truncated"])

    def test_model_limits_fields_and_methods_without_changing_index(self):
        model = self.index.model("demo.thing", 1)["items"][0]
        self.assertEqual(len(model["fields"]), 1)
        self.assertEqual(model["fields_total"], 2)
        self.assertTrue(model["fields_truncated"])
        self.assertEqual(len(model["methods"]), 1)
        self.assertEqual(model["methods_total"], 2)
        self.assertTrue(model["methods_truncated"])
        self.assertEqual(self.index.field("demo.thing", "line_ids")["definitions"]["total"], 1)

    def test_model_paginates_classes_and_members_independently(self):
        first = self.index.model("demo.thing", 1)
        self.assertEqual(first["next_offset"], 1)
        self.assertEqual(first["items"][0]["fields_next_offset"], 1)
        self.assertEqual(first["items"][0]["methods_next_offset"], 1)
        members = self.index.model("demo.thing", 1, field_offset=1, method_offset=1)
        self.assertEqual(members["items"][0]["fields"][0]["name"], "line_ids")
        self.assertEqual(members["items"][0]["methods"][0]["name"], "action_close")
        self.assertIsNone(members["items"][0]["fields_next_offset"])
        second = self.index.model("demo.thing", 1, offset=1)
        self.assertEqual(second["items"][0]["class"], "Extended")
        self.assertIsNone(second["next_offset"])
        with self.assertRaises(ValueError):
            self.index.model("demo.thing", field_offset=-1)

    def test_discover_models_filter_pagination_and_class_counts(self):
        nested = self.module / "sub"
        nested.mkdir()
        (nested / "extra.py").write_text('class Extra:\n    _inherit = ["demo.thing", "other.model"]\n    def run(self): pass\n')
        self.index.refresh()
        all_results = self.index.discover_models(module="demo", limit=2)
        self.assertEqual(all_results["total"], 5)
        self.assertEqual(all_results["next_offset"], 2)
        second = self.index.discover_models(module="demo", offset=2, limit=2)
        third = self.index.discover_models(module="demo", offset=4, limit=2)
        rows = all_results["items"] + second["items"] + third["items"]
        self.assertEqual([r["model"] for r in rows], sorted(r["model"] for r in rows))
        self.assertIsNone(third["next_offset"])
        thing = next(r for r in rows if r["class"] == "Thing")
        self.assertEqual((thing["fields_count"], thing["methods_count"]), (2, 2))
        self.assertEqual(self.index.discover_models(folder=str(nested), query="THING")["total"], 1)
        self.assertEqual(self.index.discover_models(module="missing", folder=str(nested))["total"], 0)
        self.assertEqual(self.index.discover_models(module="demo", offset=100)["items"], [])
        self.assertEqual(self.index.discover_models(module="demo", limit=1000)["limit"], 100)
        with self.assertRaises(ValueError):
            self.index.discover_models()
        with self.assertRaises(ValueError):
            self.index.discover_models(folder="relative")
        with self.assertRaises(ValueError):
            self.index.discover_models(module="demo", offset=-1)

    def test_skips_symlinked_modules_and_manifests(self):
        external = Path(self.temp.name) / "external"
        external.mkdir()
        (external / "__manifest__.py").write_text("{}")
        (external / "model.py").write_text('class External:\n    _name = "external.model"\n')
        (self.addons / "linked").symlink_to(external, target_is_directory=True)
        manifest_link = self.addons / "manifest_link"
        manifest_link.mkdir()
        (manifest_link / "__manifest__.py").symlink_to(external / "__manifest__.py")
        (manifest_link / "model.py").write_text('class Linked:\n    _name = "linked.model"\n')
        self.index.refresh()
        self.assertEqual(self.index.model("external.model")["total"], 0)
        self.assertEqual(self.index.model("linked.model")["total"], 0)
        self.assertEqual(self.index.status()["modules"], 1)

    def test_trace_model_inheritance_delegation_and_cycles(self):
        (self.module / "lineage.py").write_text('''
class Delegated:
    _name = "demo.delegated"
    _inherits = {"demo.thing": "thing_id"}
class Grandchild:
    _name = "demo.grandchild"
    _inherit = "demo.derived"
class CycleA:
    _name = "demo.a"
    _inherit = "demo.b"
class CycleB:
    _name = "demo.b"
    _inherit = "demo.a"
''')
        self.index.refresh()
        trace = self.index.trace_model("demo.grandchild")
        self.assertEqual([n["model"] for n in trace["nodes"]["items"]], ["demo.grandchild", "demo.derived", "demo.thing"])
        self.assertEqual([n["class"] for n in trace["nodes"]["items"][-1]["definitions"]["items"]], ["Thing", "Extended"])
        descendants = self.index.trace_model("demo.thing")["direct_descendants"]["items"]
        self.assertIn(("demo.delegated", "delegation", "thing_id"), [(d["model"], d["kind"], d["field"]) for d in descendants])
        limited = self.index.trace_model("demo.grandchild", 1)
        self.assertTrue(limited["traversal_truncated"])
        cycle = self.index.trace_model("demo.a")
        self.assertEqual(len(cycle["nodes"]["items"]), 2)
        self.assertFalse(cycle["traversal_truncated"])

    def test_find_method_direct_overrides_and_limits(self):
        (self.module / "overrides.py").write_text('''
class Extension:
    _inherit = "demo.thing"
    def action_open(self): pass
class Unrelated:
    _name = "other.model"
    def action_open(self): pass
''')
        self.index.refresh()
        result = self.index.find_method("demo.thing", "action_open", 1)["definitions"]
        self.assertEqual(result["total"], 2)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["items"][0]["class"], "Thing")
        self.assertEqual(self.index.find_method("demo.thing", "missing")["definitions"]["total"], 0)

    def test_trace_view_inheritance_xpath_and_unresolved_parent(self):
        (self.module / "inherited.xml").write_text('''<odoo>
<record id="extension" model="ir.ui.view">
  <field name="model">demo.thing</field>
  <field name="inherit_id" ref="thing_form"/>
  <field name="arch" type="xml"><xpath expr="//field[@name='partner_id']" position="after"><field name="line_ids"/></xpath></field>
</record>
<record id="child" model="ir.ui.view"><field name="inherit_id" ref="extension"/></record>
<record id="orphan" model="ir.ui.view"><field name="inherit_id" ref="missing.view"/></record>
</odoo>''')
        self.index.refresh()
        trace = self.index.trace_view("demo.extension")
        self.assertEqual({v["xml_id"] for v in trace["views"]["items"]}, {"demo.thing_form", "demo.extension", "demo.child"})
        extension = next(v for v in trace["views"]["items"] if v["xml_id"] == "demo.extension")
        self.assertEqual(extension["xpaths"]["items"][0], {"line": 5, "expr": "//field[@name='partner_id']", "position": "after"})
        self.assertEqual(self.index.trace_view("demo.orphan")["unresolved"]["items"], ["missing.view"])
        self.assertTrue(self.index.trace_view("demo.extension", 1)["traversal_truncated"])
        self.assertEqual(self.index.trace_view("unknown")["matches"]["total"], 0)

    def test_trace_action_links_model_menu_and_candidate_views(self):
        trace = self.index.trace_action("demo.thing_action")
        self.assertEqual(trace["actions"]["total"], 1)
        self.assertEqual(trace["model_candidates"]["items"], ["demo.thing"])
        self.assertEqual([m["xml_id"] for m in trace["menus"]["items"]], ["demo.things"])
        self.assertEqual([v["xml_id"] for v in trace["view_candidates"]["items"]], ["demo.thing_form"])
        self.assertEqual(self.index.trace_action("thing_action")["actions"]["total"], 1)
        self.assertEqual(self.index.trace_action("missing")["actions"]["total"], 0)

    def test_trace_view_includes_field_element_and_template_selectors(self):
        (self.module / "selectors.xml").write_text('''<odoo>
<record id="selectors" model="ir.ui.view">
  <field name="inherit_id" ref="thing_form"/>
  <field name="priority" eval="32"/>
  <field name="arch" type="xml"><data>
    <field name="partner_id" position="after"><field name="line_ids"/></field>
    <div name="container" position="replace"><div/></div>
  </data></field>
</record>
<template id="template_extension" inherit_id="thing_form">
  <xpath expr="//div" position="inside"><span/></xpath>
</template>
</odoo>''')
        self.index.refresh()
        trace = self.index.trace_view("demo.thing_form")
        views = {v["xml_id"]: v for v in trace["views"]["items"]}
        selectors = views["demo.selectors"]["selectors"]["items"]
        self.assertEqual([(s["tag"], s["attributes"].get("position")) for s in selectors], [("field", "after"), ("div", "replace")])
        self.assertEqual(views["demo.selectors"]["fields"]["priority"], "32")
        self.assertEqual(views["demo.template_extension"]["selectors"]["items"][0]["attributes"]["expr"], "//div")
        self.assertEqual(self.index.trace_view("demo.selectors", 1)["views"]["items"][0]["selectors"]["total"], 2)
        self.assertTrue(self.index.trace_view("demo.selectors", 1)["views"]["items"][0]["selectors"]["truncated"])

    def test_trace_action_uses_window_action_view_records(self):
        (self.module / "action_views.xml").write_text('''<odoo>
<record id="other_view" model="ir.ui.view"><field name="model">other.model</field></record>
<record id="action_form_link" model="ir.actions.act_window.view">
  <field name="sequence" eval="2"/>
  <field name="view_mode">form</field>
  <field name="view_id" ref="other_view"/>
  <field name="act_window_id" ref="thing_action"/>
</record>
</odoo>''')
        self.index.refresh()
        trace = self.index.trace_action("demo.thing_action")
        self.assertEqual(trace["action_view_links"]["items"][0]["fields"]["sequence"], "2")
        self.assertEqual({v["xml_id"] for v in trace["view_candidates"]["items"]}, {"demo.thing_form", "demo.other_view"})
        self.assertEqual(self.index.trace_action("demo.action_form_link")["actions"]["total"], 0)


if __name__ == "__main__":
    unittest.main()
