"""Focused, bounded static inspections; no addon code is executed."""

import ast
from pathlib import Path

from .imports import trace_imports
from .index import call_name, literal, qualified


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


def _class_nodes(index, model, issues, selected=None):
    """Read only indexed class files for the requested model."""
    cache = {}
    for cls in (selected if selected is not None else [c for c in index.classes if model in c["models"]])[:100]:
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


def method_calls(index, model, method, limit=20, caller_offset=0, callee_offset=0, caller_module=None, caller_folder=None):
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
    scoped = []
    if caller_module or caller_folder:
        folder = None
        if caller_folder:
            folder = Path(caller_folder).expanduser()
            if not folder.is_absolute() or not folder.is_dir():
                raise ValueError("caller_folder must be an absolute path to an existing directory")
            folder = folder.resolve()
        scoped = [c for c in index.classes if model not in c["models"] and
                  (not caller_module or c["module"] == caller_module) and
                  (not folder or Path(c["path"]).resolve().is_relative_to(folder))]
        for cls, node in _class_nodes(index, model, issues, scoped):
            for stmt in node.body:
                if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for call in _method_body_calls(stmt.body):
                    if not isinstance(call.func, ast.Attribute) or call.func.attr != method:
                        continue
                    receiver = call.func.value
                    target = literal(receiver.slice) if isinstance(receiver, ast.Subscript) and call_name(receiver.value) == "self.env" else None
                    if target == model:
                        callers.append({"path": cls["path"], "line": call.lineno, "class": cls["class"],
                                        "caller": stmt.name, "caller_models": cls["models"], "method": method,
                                        "receiver": ast.unparse(receiver)[:160], "target_model": model,
                                        "relationship": "explicit_env_model_syntax"})
    def page(items, offset):
        return {"total": len(items), "items": items[offset:offset + limit], "truncated": len(items) > limit,
                "offset": offset, "limit": limit, "next_offset": offset + limit if offset + limit < len(items) else None}
    return {"model": model, "method": method, "callers": page(callers, caller_offset), "callees": page(callees, callee_offset),
            "classes_truncated": sum(model in c["models"] for c in index.classes) > 100,
            "scoped_classes_total": len(scoped), "scoped_classes_truncated": len(scoped) > 100,
            "inspection_issues": index.limit(issues, limit),
            "note": "Cross-model callers require a scope and only explicit self.env['model'].method() syntax is matched. Runtime dispatch, chained and dynamic receivers remain unresolved."}


def state_assignments(index, model, field="state", method=None, limit=20, offset=0):
    limit = max(1, min(limit, 50))
    if offset < 0:
        raise ValueError("offset must be non-negative")
    issues, items, unresolved_writes = [], [], []
    for cls, node in _class_nodes(index, model, issues):
        for fn in node.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or (method and fn.name != method):
                continue
            # A variable dictionary is resolved only when assigned once at top level and never otherwise referenced except write(var).
            candidates = {}
            for stmt in fn.body:
                if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name) and isinstance(stmt.value, ast.Dict):
                    name = stmt.targets[0].id
                    uses = [n for n in ast.walk(fn) if isinstance(n, ast.Name) and n.id == name]
                    if sum(isinstance(n.ctx, ast.Store) for n in uses) == 1 and sum(isinstance(n.ctx, ast.Load) for n in uses) == 1:
                        candidates[name] = (stmt.value, stmt.lineno)
            def emit(value, line, receiver, kind):
                sentinel = object()
                parsed = literal(value, sentinel)
                if not isinstance(parsed, (str, int, float, bool, type(None))):
                    parsed = sentinel
                items.append({"model": model, "method": fn.name, "field": field, "class": cls["class"], "path": cls["path"],
                              "line": line, "receiver": ast.unparse(receiver)[:160], "syntax": kind,
                              "receiver_relation": "self_syntax" if isinstance(receiver, ast.Name) and receiver.id == "self" else "unresolved_receiver",
                              "value": None if parsed is sentinel else parsed, "value_resolved": parsed is not sentinel,
                              "expression": ast.unparse(value)[:200] if parsed is sentinel else None})
            for stmt in _method_nodes(fn.body):
                if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                    targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                    for target in targets:
                        if isinstance(target, ast.Attribute) and target.attr == field and stmt.value is not None:
                            emit(stmt.value, stmt.lineno, target.value, "attribute_assignment")
                if isinstance(stmt, ast.Call) and isinstance(stmt.func, ast.Attribute) and stmt.func.attr == "write" and stmt.args:
                    arg = stmt.args[0]
                    dictionary = arg if isinstance(arg, ast.Dict) else None
                    if isinstance(arg, ast.Name) and arg.id in candidates and candidates[arg.id][1] < stmt.lineno:
                        dictionary = candidates[arg.id][0]
                    if dictionary is None or any(key is None or not isinstance(literal(key), str) for key in dictionary.keys):
                        unresolved_writes.append({"path": cls["path"], "line": stmt.lineno, "method": fn.name,
                                                  "receiver": ast.unparse(stmt.func.value)[:160], "argument": ast.unparse(arg)[:200],
                                                  "reason": "write keys unresolved; may or may not include the requested field"})
                    if dictionary:
                        for key, value in zip(dictionary.keys, dictionary.values):
                            if key is not None and literal(key) == field:
                                emit(value, stmt.lineno, stmt.func.value, "write_dict" if dictionary is arg else "write_variable_dict")
    return {"model": model, "field": field, "method": method, "total": len(items), "items": items[offset:offset + limit],
            "offset": offset, "limit": limit, "next_offset": offset + limit if offset + limit < len(items) else None,
            "classes_truncated": sum(model in c["models"] for c in index.classes) > 100, "inspection_issues": index.limit(issues, limit),
            "unresolved_writes": index.limit(unresolved_writes, limit),
            "note": "Syntax evidence only, not a runtime transition. Other receivers, dynamic dictionaries, aliases and control flow are unresolved or omitted."}


def _method_nodes(statements):
    stack = list(reversed(statements))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        yield node
        stack.extend(reversed(list(ast.iter_child_nodes(node))))


def object_buttons(index, model, method=None, limit=20, offset=0):
    limit = max(1, min(limit, 50))
    if offset < 0:
        raise ValueError("offset must be non-negative")
    views = {}
    for record in index.records:
        if record["kind"] == "ir.ui.view":
            views.setdefault(record["xml_id"], []).append(record)
    items, unresolved = [], []
    for button in index.buttons:
        if method and button["method"] != method:
            continue
        current = next((v for v in views.get(button["view"], []) if v["path"] == button["path"] and v["line"] == button["view_line"]), None)
        if not current:
            continue
        models, queue, seen, missing_parents = set(), [current], set(), set()
        while queue and len(seen) < 100:
            view = queue.pop(0)
            key = (view["path"], view["line"])
            if key in seen:
                continue
            seen.add(key)
            if view["fields"].get("model"):
                models.add(view["fields"]["model"])
            parent = qualified(view["module"], view["fields"].get("inherit_id"))
            if parent:
                if parent not in views:
                    missing_parents.add(parent)
                queue.extend(views.get(parent, []))
        if not models or button["nested_field"]:
            unresolved.append({**button, "reason": "nested relational view model unresolved" if button["nested_field"] else "view model unresolved"})
        if model not in models:
            continue
        definitions = index.find_method(model, button["method"], limit)["definitions"]
        items.append({**button, "model_candidates": sorted(models)[:limit], "model_candidates_truncated": len(models) > limit,
                      "model_relation": "ambiguous_nested_view" if button["nested_field"] else "ambiguous_models" if len(models) > 1 else "inferred_view_inheritance" if len(seen) > 1 else "declared_view_model",
                      "parent_traversal_truncated": bool(queue), "method_candidates": definitions})
        items[-1]["unresolved_parents"] = index.limit(sorted(missing_parents), limit)
    return {"model": model, "method": method, "total": len(items), "items": items[offset:offset + limit],
            "offset": offset, "limit": limit, "next_offset": offset + limit if offset + limit < len(items) else None,
            "unresolved_buttons": index.limit(unresolved, limit),
            "note": "Object button syntax and model/method candidates only; nested relational views, inheritance, dynamic rendering and runtime dispatch may differ."}
