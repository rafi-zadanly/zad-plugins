"""Trace possible static Python imports inside one addon, without executing it."""

import ast
import importlib.util
from collections import deque
from pathlib import Path


def trace_imports(module_dir: Path, target: Path, max_files: int = 500) -> dict:
    module_dir = module_dir.resolve()
    target = target.resolve()
    entry = module_dir / "__init__.py"
    result = {"manifest": str(module_dir / "__manifest__.py"), "entrypoint": str(entry),
              "target": str(target), "status": "unknown", "chain": [], "issues": []}
    if not entry.is_file() or entry.is_symlink():
        result["issues"].append({"path": str(entry), "reason": "missing addon entrypoint"})
        return result
    if not target.is_relative_to(module_dir):
        result["issues"].append({"path": str(target), "reason": "target is outside the addon"})
        return result
    prefix = module_dir.name

    def candidates(name):
        if name.startswith("odoo.addons."):
            name = name[len("odoo.addons."):]
        if name != prefix and not name.startswith(prefix + "."):
            return []
        parts = name.split(".")[1:]
        found = []
        for end in range(1, len(parts) + 1):
            path = module_dir.joinpath(*parts[:end])
            if any(module_dir.joinpath(*parts[:depth]).is_symlink() for depth in range(1, end + 1)):
                break
            for candidate in (path / "__init__.py", path.with_suffix(".py")):
                if candidate.is_file() and not candidate.is_symlink():
                    found.append(candidate)
        return found

    def imports(path):
        relative = path.relative_to(module_dir)
        package_parts = relative.parts[:-1]
        package = ".".join((prefix, *package_parts))
        tree = ast.parse(path.read_bytes(), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom):
                try:
                    base = importlib.util.resolve_name("." * node.level + (node.module or ""), package) if node.level else (node.module or "")
                except (ImportError, ValueError):
                    continue
                names.append(base)
                names.extend(base + "." + alias.name for alias in node.names if alias.name != "*")
            elif isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            for name in names:
                for dest in candidates(name):
                    yield dest, node.lineno

    queue = deque([entry])
    previous = {entry: None}
    while queue and len(previous) <= max_files and target not in previous:
        current = queue.popleft()
        try:
            for dest, line in imports(current):
                if dest not in previous:
                    previous[dest] = {"from": current, "line": line}
                    queue.append(dest)
                    if len(previous) > max_files:
                        break
        except (OSError, UnicodeError, SyntaxError, ValueError) as exc:
            result["issues"].append({"path": str(current), "reason": str(exc)})
    if target in previous:
        chain = []
        current = target
        while previous[current] is not None:
            edge = previous[current]
            chain.append({"from": str(edge["from"]), "line": edge["line"], "to": str(current)})
            current = edge["from"]
        result["status"] = "statically_reachable"
        result["chain"] = list(reversed(chain))
    else:
        if queue or len(previous) > max_files:
            result["issues"].append({"reason": f"import traversal limited to {max_files} files"})
        result["status"] = "unknown" if result["issues"] else "no_static_path_found"
        # Show likely breakpoints, but do not claim an unexecuted import is broken.
        relative = target.relative_to(module_dir)
        for depth in range(len(relative.parts) - 1):
            init = module_dir.joinpath(*relative.parts[:depth + 1]) / "__init__.py"
            if init not in previous:
                result["issues"].append({"path": str(init), "reason": "package entrypoint not statically reached" if init.is_file() else "package entrypoint missing"})
        if not result["issues"]:
            result["issues"].append({"path": str(target), "reason": "no static import path found from addon entrypoint"})
    result["note"] = "Static import candidates only; conditional and dynamic imports, errors, and Odoo load order can change actual loading."
    return result
