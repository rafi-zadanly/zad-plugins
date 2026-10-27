import tempfile
import unittest
from pathlib import Path

from odoo_source_mcp.workspace import discover_roots


class WorkspaceTests(unittest.TestCase):
    def test_nested_addon_finds_checkout_containers_without_crossing_git_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            project = base / "project"
            (project / ".git").mkdir(parents=True)
            addon = project / "custom_addons" / "hospital"
            (addon / "models").mkdir(parents=True)
            (addon / "__manifest__.py").write_text("{}")
            core = project / "odoo" / "addons" / "base"
            core.mkdir(parents=True)
            (core / "__manifest__.py").write_text("{}")
            unrelated = base / "unrelated" / "addon"
            unrelated.mkdir(parents=True)
            (unrelated / "__manifest__.py").write_text("{}")
            roots = discover_roots(addon / "models")
            self.assertIn(addon.parent.resolve(), roots)
            self.assertIn(core.parent.resolve(), roots)
            self.assertNotIn(unrelated.parent.resolve(), roots)

    def test_no_addons_and_missing_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "project"
            (workspace / ".git").mkdir(parents=True)
            self.assertEqual(discover_roots(workspace), [])
            with self.assertRaises(ValueError):
                discover_roots(workspace / "missing")

    def test_discovery_does_not_follow_symlinked_addons(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            project = base / "project"
            (project / ".git").mkdir(parents=True)
            external = base / "external"
            (external / "base").mkdir(parents=True)
            (external / "base" / "__manifest__.py").write_text("{}")
            (external / "addons" / "core").mkdir(parents=True)
            (external / "addons" / "core" / "__manifest__.py").write_text("{}")
            (project / "odoo").symlink_to(external, target_is_directory=True)
            (project / "addons").mkdir()
            (project / "addons" / "base").symlink_to(external / "base", target_is_directory=True)
            self.assertEqual(discover_roots(project), [])


if __name__ == "__main__":
    unittest.main()
