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
            if node.tag in {"record", "menuitem"} and node.attrs.get("id"):
                raw_id = node.attrs["id"]
                record = {**loc, "xml_id": qualified(module, raw_id), "kind": node.attrs.get("model", "ir.ui.menu" if node.tag == "menuitem" else ""), "fields": {}}
                if node.tag == "menuitem":
                    record["fields"] = dict(node.attrs)
                else:
                    for child in node.children:
                        if child.tag == "field" and child.attrs.get("name"):
                            value = child.attrs.get("ref") or child.text.strip()
                            record["fields"][child.attrs["name"]] = value
                self.records.append(record)
                if record["kind"] == "ir.ui.view":
                    model = record["fields"].get("model") or None
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
