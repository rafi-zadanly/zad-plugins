"""Conservative static index of common Odoo addon source patterns."""

from __future__ import annotations

import ast
import os
import re
import xml.sax
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.handler import ContentHandler, feature_external_ges


def literal(node, default=None):
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        return default


def qualified(module: str, value: str | None) -> str | None:
    if not value:
        return None
    return value if "." in value else f"{module}.{value}"


@dataclass
class Node:
    tag: str
    attrs: dict
    line: int
    children: list = field(default_factory=list)
    text: str = ""


class XMLHandler(ContentHandler):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.roots = []

    def setDocumentLocator(self, locator):
        self.locator = locator

    def startElement(self, name, attrs):
        node = Node(name, dict(attrs), self.locator.getLineNumber())
        (self.stack[-1].children if self.stack else self.roots).append(node)
        self.stack.append(node)

    def endElement(self, name):
        self.stack.pop()

    def characters(self, content):
        if self.stack:
            self.stack[-1].text += content


def descendants(node):
    for child in node.children:
        yield child
        yield from descendants(child)


def view_selectors(nodes):
    """Capture top-level inheritance locators, including the optional data wrapper."""
    for node in nodes:
        if node.tag == "data":
            yield from view_selectors(node.children)
        else:
            yield {"line": node.line, "tag": node.tag, "attributes": dict(node.attrs)}


def call_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


class SourceIndex:
    def __init__(self, roots: list[Path]):
        self.roots = [p.expanduser().resolve() for p in roots]
        self.fingerprint = None
        self.classes = []
        self.records = []
        self.references = []
        self.usages = []
        self.view_xpaths = {}
        self.view_specs = {}
        self.errors = []
        self.modules = []

    def refresh(self, force=False):
        files = []
        modules = []
        for root in self.roots:
            if not root.is_dir():
                continue
            for module in sorted(root.iterdir()):
                manifest = module / "__manifest__.py"
                if module.is_symlink() or not module.is_dir() or manifest.is_symlink() or not manifest.is_file():
                    continue
                modules.append((module.name, manifest))
                for base, dirs, names in os.walk(module, followlinks=False):
                    dirs[:] = sorted(d for d in dirs if d not in {".git", ".venv", "__pycache__", "node_modules", "static", "tests", "test"} and not d.startswith("."))
                    for name in sorted(names):
                        path = Path(base) / name
                        if path.suffix in {".py", ".xml"} and not path.is_symlink():
                            try:
                                stat = path.stat()
                                files.append((module.name, path, stat.st_mtime_ns, stat.st_size))
                            except OSError:
                                continue
        fingerprint = tuple((str(p), mtime, size) for _, p, mtime, size in files)
        if not force and fingerprint == self.fingerprint:
            return False
        self.classes, self.records, self.references, self.usages, self.errors = [], [], [], [], []
        self.view_xpaths = {}
        self.view_specs = {}
        self.modules = []
        for module, manifest in modules:
            try:
                data = literal(ast.parse(manifest.read_text(encoding="utf-8")).body[0].value, {})
                self.modules.append({"name": module, "manifest": str(manifest), "depends": data.get("depends", []) if isinstance(data, dict) else []})
            except (OSError, UnicodeError, SyntaxError, IndexError, AttributeError) as exc:
                self.errors.append({"path": str(manifest), "error": str(exc)})
        for module, path, _, _ in files:
            try:
                if path.suffix == ".py":
                    self._python(module, path)
                else:
                    self._xml(module, path)
            except (OSError, UnicodeError, SyntaxError, xml.sax.SAXException, ValueError) as exc:
                self.errors.append({"path": str(path), "error": str(exc)})
        self.fingerprint = fingerprint
        return True

    def _python(self, module, path):
        tree = ast.parse(path.read_bytes(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            assignments = {}
            for item in node.body:
                if isinstance(item, (ast.Assign, ast.AnnAssign)):
                    targets = item.targets if isinstance(item, ast.Assign) else [item.target]
                    for target in targets:
                        if isinstance(target, ast.Name):
                            assignments[target.id] = item.value
            name = literal(assignments.get("_name"))
            inherit = literal(assignments.get("_inherit"))
            inherits = literal(assignments.get("_inherits"), {})
            if not isinstance(name, str):
                name = None
            parents = [inherit] if isinstance(inherit, str) else inherit if isinstance(inherit, (tuple, list)) else []
            parents = [p for p in parents if isinstance(p, str)]
            if not name and not parents:
                continue
            # _name plus _inherit creates a new model; it is not an in-place extension of each parent.
            models = [name] if name else parents
            fields, methods = [], []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append({"name": item.name, "line": item.lineno})
                if not isinstance(item, (ast.Assign, ast.AnnAssign)) or not isinstance(item.value, ast.Call):
                    continue
                func = call_name(item.value.func)
                if not func.startswith("fields."):
                    continue
                targets = item.targets if isinstance(item, ast.Assign) else [item.target]
                kwargs = {kw.arg: literal(kw.value) for kw in item.value.keywords if kw.arg}
                args = [literal(arg) for arg in item.value.args]
                for target in targets:
                    if isinstance(target, ast.Name):
                        typ = func.split(".")[-1]
                        fields.append({"name": target.id, "type": typ, "line": item.lineno,
                                       "comodel": kwargs.get("comodel_name") or (args[0] if args and typ in {"Many2one", "One2many", "Many2many"} else None),
                                       "inverse_name": kwargs.get("inverse_name") or (args[1] if len(args) > 1 and typ == "One2many" else None),
                                       "related": kwargs.get("related"), "compute": kwargs.get("compute")})
            self.classes.append({"module": module, "path": str(path), "line": node.lineno, "class": node.name,
                                 "models": models, "inherit": parents, "delegates": inherits if isinstance(inherits, dict) else {},
                                 "fields": fields, "methods": methods})

    def _xml(self, module, path):
        parser = xml.sax.make_parser()
        parser.setFeature(feature_external_ges, False)
        handler = XMLHandler()
        parser.setContentHandler(handler)
        with path.open("rb") as stream:
            parser.parse(stream)
        nodes = [n for root in handler.roots for n in [root, *descendants(root)]]
        for node in nodes:
            loc = {"module": module, "path": str(path), "line": node.line}
            if node.tag in {"record", "menuitem", "template"} and node.attrs.get("id"):
                raw_id = node.attrs["id"]
                kind = "ir.ui.menu" if node.tag == "menuitem" else "ir.ui.view" if node.tag == "template" else node.attrs.get("model", "")
                record = {**loc, "xml_id": qualified(module, raw_id), "kind": kind, "fields": {}}
                if node.tag in {"menuitem", "template"}:
                    record["fields"] = dict(node.attrs)
                else:
                    for child in node.children:
                        if child.tag == "field" and child.attrs.get("name"):
                            value = child.attrs.get("ref") or child.text.strip()
                            if not value and child.attrs["name"] in {"priority", "sequence"} and child.attrs.get("eval"):
                                try:
                                    number = literal(ast.parse(child.attrs["eval"], mode="eval").body)
                                    if isinstance(number, int) and not isinstance(number, bool):
                                        value = str(number)
                                except SyntaxError:
                                    pass
                            record["fields"][child.attrs["name"]] = value
                self.records.append(record)
                if record["kind"] == "ir.ui.view":
                    model = record["fields"].get("model") or None
                    self.view_xpaths[(str(path), node.line)] = [
                        {"line": child.line, "expr": child.attrs.get("expr"), "position": child.attrs.get("position")}
                        for child in descendants(node) if child.tag == "xpath"]
                    arches = [child for child in node.children if child.tag == "field" and child.attrs.get("name") == "arch"]
                    selector_nodes = node.children if node.tag == "template" else [child for arch in arches for child in arch.children]
                    if record["fields"].get("inherit_id"):
                        self.view_specs[(str(path), node.line)] = list(view_selectors(selector_nodes))
                    for child in descendants(node):
                        if child.tag == "field" and child.attrs.get("name") and child not in node.children:
                            self.usages.append({**loc, "line": child.line, "view": record["xml_id"], "model": model, "field": child.attrs["name"],
                                                "note": "View context may contain nested relational models or inheritance selectors; verify source."})
            for attr in ("ref", "parent", "action", "inherit_id"):
                if node.attrs.get(attr):
                    self.references.append({**loc, "target": qualified(module, node.attrs[attr]), "attribute": attr})
            # Common Odoo references in eval attributes and string action placeholders.
            for value in node.attrs.values():
                for ref in re.findall(r"(?:ref\(['\"]([^'\"]+)['\"]\)|%\(([^)]+)\)d)", value):
                    self.references.append({**loc, "target": qualified(module, ref[0] or ref[1]), "attribute": "expression"})

    def status(self):
        return {"roots": [str(p) for p in self.roots], "modules": len(self.modules), "classes": len(self.classes),
                "records": len(self.records), "parse_error_count": len(self.errors), "parse_errors": self.errors[:25],
                "note": "Static source only; not a registry or proof that modules/data are installed. Tests and static assets are excluded."}

    @staticmethod
    def limit(items, limit):
        limit = max(1, min(limit, 100))
        return {"total": len(items), "items": items[:limit], "truncated": len(items) > limit}

    def model(self, name, limit=50):
        result = self.limit([c for c in self.classes if name in c["models"]], limit)
        for i, cls in enumerate(result["items"]):
            fields = self.limit(cls["fields"], limit)
            methods = self.limit(cls["methods"], limit)
            result["items"][i] = {**cls, "fields": fields["items"], "fields_total": fields["total"],
                                  "fields_truncated": fields["truncated"], "methods": methods["items"],
                                  "methods_total": methods["total"], "methods_truncated": methods["truncated"]}
        return result

    def trace_model(self, name, limit=50):
        limit = max(1, min(limit, 100))
        queue, seen, nodes, edges = [name], set(), [], []
        while queue and len(seen) < limit:
            model = queue.pop(0)
            if model in seen:
                continue
            seen.add(model)
            classes = [c for c in self.classes if model in c["models"]]
            definitions = [{k: c[k] for k in ("module", "path", "line", "class", "models", "inherit", "delegates")}
                           for c in classes]
            nodes.append({"model": model, "definitions": self.limit(definitions, limit)})
            for cls in classes:
                relations = [(parent, "inherit", None) for parent in cls["inherit"] if parent != model]
                relations += [(parent, "delegation", field) for parent, field in cls["delegates"].items()
                              if isinstance(parent, str)]
                for parent, kind, field in relations:
                    edges.append({"model": model, "parent": parent, "kind": kind, "field": field,
                                  "module": cls["module"], "path": cls["path"], "line": cls["line"]})
                    if parent not in seen:
                        queue.append(parent)
        derived = [{"model": model, "parent": name, "kind": kind, "field": field,
                    "module": c["module"], "path": c["path"], "line": c["line"]}
                   for c in self.classes for model in c["models"] if model != name
                   for parent, kind, field in ([(p, "inherit", None) for p in c["inherit"]] +
                                                [(p, "delegation", f) for p, f in c["delegates"].items()])
                   if parent == name]
        return {"model": name, "nodes": self.limit(nodes, limit), "relations": self.limit(edges, limit),
                "direct_descendants": self.limit(derived, limit), "traversal_truncated": any(n not in seen for n in queue),
                "note": "Static candidates only. Ancestors are followed; descendants are direct only. Cycles, missing source, dynamic declarations and Odoo load order require inspection."}

    def find_method(self, model, name, limit=50):
        items = [{**{k: c[k] for k in ("module", "path", "class", "models", "inherit", "delegates")}, **method}
                 for c in self.classes if model in c["models"] for method in c["methods"] if method["name"] == name]
        return {"model": model, "method": name, "definitions": self.limit(items, limit),
                "note": "Direct definitions and extension candidates only; Python/Odoo method resolution and installed modules are not resolved."}

    def trace_view(self, identifier, limit=50):
        limit = max(1, min(limit, 100))
        views = [r for r in self.records if r["kind"] == "ir.ui.view"]
        matches = [r for r in views if r["xml_id"] == identifier or
                   ("." not in identifier and r["xml_id"].rsplit(".", 1)[-1] == identifier)]
        by_id = {}
        children = {}
        for view in views:
            by_id.setdefault(view["xml_id"], []).append(view)
            parent = qualified(view["module"], view["fields"].get("inherit_id"))
            if parent:
                children.setdefault(parent, []).append(view["xml_id"])
        queue = list(dict.fromkeys(r["xml_id"] for r in matches))
        seen, records, edges, unresolved = set(), [], [], []
        while queue and len(seen) < limit:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)
            candidates = by_id.get(current, [])
            if not candidates:
                unresolved.append(current)
            for view in candidates:
                xpaths = self.limit(self.view_xpaths.get((view["path"], view["line"]), []), limit)
                selectors = self.limit(self.view_specs.get((view["path"], view["line"]), []), limit)
                records.append({**view, "xpaths": xpaths, "selectors": selectors})
                parent = qualified(view["module"], view["fields"].get("inherit_id"))
                if parent:
                    edges.append({"view": current, "parent": parent, "module": view["module"],
                                  "path": view["path"], "line": view["line"]})
                    if parent not in seen:
                        queue.append(parent)
            queue.extend(child for child in children.get(current, []) if child not in seen)
        return {"query": identifier, "matches": self.limit([r["xml_id"] for r in matches], limit),
                "views": self.limit(records, limit), "relations": self.limit(edges, limit),
                "unresolved": self.limit(unresolved, limit), "traversal_truncated": any(n not in seen for n in queue),
                "note": "Static inheritance candidates and source locators; dynamic references and effective Odoo view order are not resolved."}

    def trace_action(self, identifier, limit=50):
        def matches(xml_id):
            return xml_id == identifier or ("." not in identifier and xml_id.rsplit(".", 1)[-1] == identifier)

        actions = [r for r in self.records if r["kind"].startswith("ir.actions.") and
                   r["kind"] != "ir.actions.act_window.view" and matches(r["xml_id"])]
        action_ids = {r["xml_id"] for r in actions}
        models = {r["fields"].get("res_model") for r in actions if r["fields"].get("res_model")}
        menus = [r for r in self.records if r["kind"] == "ir.ui.menu" and
                 qualified(r["module"], r["fields"].get("action")) in action_ids]
        view_links = [r for r in self.records if r["kind"] == "ir.actions.act_window.view" and
                      qualified(r["module"], r["fields"].get("act_window_id")) in action_ids]
        view_ids = {qualified(r["module"], r["fields"].get(key)) for r in actions for key in ("view_id", "search_view_id")}
        view_ids.update(qualified(r["module"], r["fields"].get("view_id")) for r in view_links)
        views = [r for r in self.records if r["kind"] == "ir.ui.view" and
                 (r["xml_id"] in view_ids or r["fields"].get("model") in models)]
        return {"query": identifier, "actions": self.limit(actions, limit), "menus": self.limit(menus, limit),
                "action_view_links": self.limit(view_links, limit),
                "model_candidates": self.limit(sorted(models), limit), "view_candidates": self.limit(views, limit),
                "note": "Static action-view records, direct references and model-matched views are candidates; dynamic action view lists and effective views may differ."}

    def field(self, model, name, limit=50):
        definitions = [{**{k: c[k] for k in ("module", "path", "class", "models")}, **f} for c in self.classes if model in c["models"] for f in c["fields"] if f["name"] == name]
        usages = [u for u in self.usages if u["field"] == name and (u["model"] == model or u["model"] is None)]
        return {"definitions": self.limit(definitions, limit), "view_candidates": self.limit(usages, limit),
                "note": "Definitions are direct only; inspect inherited/delegated models separately. View candidates can include nested fields."}

    def xml_id(self, identifier, limit=50):
        exact = "." in identifier
        matches = lambda x: x == identifier if exact else x.rsplit(".", 1)[-1] == identifier
        return {"records": self.limit([r for r in self.records if matches(r["xml_id"])], limit),
                "references": self.limit([r for r in self.references if matches(r["target"])], limit),
                "note": "References are static candidates, not an exhaustive search of dynamic expressions."}

    def menus(self, query, limit=20):
        menus = [r for r in self.records if r["kind"] == "ir.ui.menu" and (query.lower() in r["xml_id"].lower() or query.lower() in r["fields"].get("name", "").lower())]
        by_id = {}
        for record in self.records:
            by_id.setdefault(record["xml_id"], []).append(record)
        result = []
        for menu in menus:
            fields = menu["fields"]
            parent = qualified(menu["module"], fields.get("parent") or fields.get("parent_id"))
            action = qualified(menu["module"], fields.get("action"))
            actions = by_id.get(action, []) if action else []
            models = {a["fields"].get("res_model") for a in actions if a["fields"].get("res_model")}
            views = [r for r in self.records if r["kind"] == "ir.ui.view" and r["fields"].get("model") in models]
            result.append({"menu": menu, "parent_id": parent, "parent_candidates": by_id.get(parent, []) if parent else [],
                           "action_id": action, "action_candidates": actions, "model_candidates": sorted(models),
                           "view_candidates": views[:20], "view_candidates_total": len(views),
                           "note": "Views are model matches, not necessarily those selected by the action. Unresolved or dynamic references require source inspection."})
        return self.limit(result, limit)
