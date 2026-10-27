"""Focused, bounded static inspections; no addon code is executed."""

import ast
from pathlib import Path

from .imports import trace_imports
from .index import call_name, literal


def inspect_module(index, module, limit=20, offset=0):
    limit = max(1, min(limit, 50))
    if offset < 0:
        raise ValueError("offset must be non-negative")
    matches = [m for m in index.modules if m["name"] == module]
    results = []
    for entry in matches[:limit]:
        manifest = Path(entry["manifest"])
        try:
            tree = ast.parse(manifest.read_bytes(), filename=str(manifest))
            data = literal(tree.body[0].value, {})
            data = data if isinstance(data, dict) else {}
        except (OSError, SyntaxError, ValueError, IndexError, AttributeError):
            data = {}
        files = sorted({path for name, path in index.source_files if name == module
                        and Path(path).is_relative_to(manifest.parent) and Path(path) != manifest})
        traces = [trace_imports(manifest.parent, Path(path), max_files=200) for path in files[offset:offset + limit]]
        for trace in traces:
            trace["chain_truncated"] = len(trace["chain"]) > limit
            trace["issues_truncated"] = len(trace["issues"]) > limit
            trace["chain"] = trace["chain"][:limit]
            trace["issues"] = trace["issues"][:limit]
        depends = entry["depends"] if isinstance(entry["depends"], list) else []
        results.append({"manifest": str(manifest), "line": 1, "depends": depends[:limit],
                        "depends_total": len(depends),
                        "data": data.get("data", [])[:limit] if isinstance(data.get("data"), list) else [],
                        "data_total": len(data.get("data", [])) if isinstance(data.get("data"), list) else 0,
                        "source_files_total": len(files), "source_files_truncated": len(files) > limit,
                        "source_files_offset": offset, "source_files_limit": limit,
                        "source_files_next_offset": offset + limit if offset + limit < len(files) else None,
                        "import_candidates": traces})
    return {"module": module, "total": len(matches), "items": results, "truncated": len(matches) > limit,
            "note": "Indexed Python files only; excluded directories, conditional/dynamic imports and actual loading are not verified."}


def _class_nodes(index, model, issues):
    """Read only indexed class files for the requested model."""
    cache = {}
    for cls in [c for c in index.classes if model in c["models"]][:100]:
        path = cls["path"]
        if path not in cache:
            try:
                cache[path] = ast.parse(Path(path).read_bytes(), filename=path)
            except (OSError, SyntaxError, ValueError) as exc:
                issues.append({"path": path, "reason": type(exc).__name__})
                cache[path] = None
        tree = cache[path]
        if tree is not None:
            node = next((n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)
                         and n.lineno == cls["line"] and n.name == cls["class"]), None)
            if node:
                yield cls, node
            else:
                issues.append({"path": path, "line": cls["line"], "class": cls["class"], "reason": "indexed class no longer found"})


def field_behavior(index, model, field, limit=20):
    limit = max(1, min(limit, 50))
    definitions, methods, named = [], [], set()
    issues = []
    classes = list(_class_nodes(index, model, issues))
    for cls, node in classes:
        for stmt in node.body:
            if not isinstance(stmt, (ast.Assign, ast.AnnAssign)) or not isinstance(stmt.value, ast.Call):
                continue
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            if not any(isinstance(t, ast.Name) and t.id == field for t in targets) or not call_name(stmt.value.func).startswith("fields."):
                continue
            attrs, unresolved = {}, []
            for kw in stmt.value.keywords:
                if kw.arg in ("compute", "inverse", "search", "related"):
                    value = literal(kw.value)
                    attrs[kw.arg] = value if isinstance(value, str) else None
                    if not isinstance(value, str):
                        unresolved.append(kw.arg)
                    if kw.arg != "related" and isinstance(value, str):
                        named.add(value)
            definitions.append({"path": cls["path"], "line": stmt.lineno, "class": cls["class"], "attributes": attrs,
                                "unresolved_attributes": unresolved})
    for cls, node in classes:
        for stmt in node.body:
            if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decorators = []
            for dec in stmt.decorator_list:
                if isinstance(dec, ast.Call) and call_name(dec.func) in ("api.depends", "api.onchange", "api.constrains"):
                    args = [literal(arg) for arg in dec.args]
                    if stmt.name in named or any(isinstance(arg, str) and (arg == field or arg.startswith(field + ".")) for arg in args):
                        fields = [a for a in args if isinstance(a, str)]
                        decorators.append({"name": call_name(dec.func), "line": dec.lineno, "fields": fields[:limit],
                                           "fields_truncated": len(fields) > limit,
                                           "matches_field": any(a == field or a.startswith(field + ".") for a in fields),
                                           "dynamic_arguments": bool(dec.keywords) or any(not isinstance(a, str) for a in args)})
            if stmt.name in named or decorators:
                methods.append({"path": cls["path"], "line": stmt.lineno, "class": cls["class"], "method": stmt.name,
                                "field_method_name_match": stmt.name in named, "decorators": decorators[:limit],
                                "decorators_truncated": len(decorators) > limit})
    return {"model": model, "field": field, "definitions": index.limit(definitions, limit), "methods": index.limit(methods, limit),
            "classes_truncated": sum(model in c["models"] for c in index.classes) > 100,
            "inspection_issues": index.limit(issues, limit),
            "note": "Static syntax and method-name candidates only; inherited resolution, dynamic arguments and actual runtime behavior are not established."}


def _method_body_calls(statements):
    """Walk a method body without attributing nested functions or classes to it."""
    stack = list(reversed(statements))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Call):
            yield node
        stack.extend(reversed(list(ast.iter_child_nodes(node))))


def method_calls(index, model, method, limit=20, caller_offset=0, callee_offset=0):
    limit = max(1, min(limit, 50))
    if min(caller_offset, callee_offset) < 0:
        raise ValueError("offsets must be non-negative")
    callers, callees, issues = [], [], []
    for cls, node in _class_nodes(index, model, issues):
        for stmt in node.body:
            if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in _method_body_calls(stmt.body):
                func = call.func
                if not isinstance(func, ast.Attribute):
                    continue
                receiver = func.value
                target_model = None
                kind = "unresolved_receiver"
                if isinstance(receiver, ast.Name) and receiver.id == "self":
                    target_model, kind = model, "self_syntax"
                elif isinstance(receiver, ast.Subscript) and call_name(receiver.value) == "self.env":
                    value = literal(receiver.slice)
                    if isinstance(value, str):
                        target_model, kind = value, "explicit_env_model_syntax"
                item = {"path": cls["path"], "line": call.lineno, "class": cls["class"], "caller": stmt.name,
                        "method": func.attr, "receiver": ast.unparse(receiver)[:160], "target_model": target_model,
                        "relationship": kind}
                if stmt.name == method:
                    callees.append(item)
                if func.attr == method and target_model == model:
                    callers.append(item)
    def page(items, offset):
        return {"total": len(items), "items": items[offset:offset + limit], "truncated": len(items) > limit,
                "offset": offset, "limit": limit, "next_offset": offset + limit if offset + limit < len(items) else None}
    return {"model": model, "method": method, "callers": page(callers, caller_offset), "callees": page(callees, callee_offset),
            "classes_truncated": sum(model in c["models"] for c in index.classes) > 100,
            "inspection_issues": index.limit(issues, limit),
            "note": "Only methods in indexed classes for the requested model are scanned. Syntax matches do not resolve runtime dispatch; other receivers and dynamic calls are unresolved."}
