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


if __name__ == "__main__":
    unittest.main()
