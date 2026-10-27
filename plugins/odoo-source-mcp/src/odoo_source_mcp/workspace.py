"""Discover addon containers near the requested workspace without scanning unrelated trees."""

from pathlib import Path


def contains_addon(path: Path) -> bool:
    try:
        return any(not child.is_symlink() and child.is_dir() and
                   not (child / "__manifest__.py").is_symlink() and
                   (child / "__manifest__.py").is_file() for child in path.iterdir())
    except OSError:
        return False


def discover_roots(workspace: Path) -> list[Path]:
    workspace = workspace.expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError(f"Workspace is not a directory: {workspace}")
    # Stay within the nearest Git checkout when present. For standalone addon trees,
    # inspect at most three ancestors; never inspect a filesystem or home root.
    ancestors = [workspace, *workspace.parents]
    git_root = next((p for p in ancestors if (p / ".git").exists()), None)
    upper = ancestors.index(git_root) + 1 if git_root else min(len(ancestors), 4)
    candidates = []
    for base in ancestors[:upper]:
        if base == Path.home() or base == base.parent:
            break
        candidates.append(base)
        try:
            children = sorted(p for p in base.iterdir() if p.is_dir() and not p.is_symlink() and not p.name.startswith("."))
        except OSError:
            children = []
        candidates.extend(children)
        # Odoo itself keeps built-in addons under odoo/addons.
        if not (base / "odoo").is_symlink() and not (base / "odoo" / "addons").is_symlink() and (base / "odoo" / "addons").is_dir():
            candidates.append(base / "odoo" / "addons")
    roots = []
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate not in roots and contains_addon(candidate):
            roots.append(candidate)
    return roots
