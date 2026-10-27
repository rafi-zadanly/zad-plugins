"""Resolve a project's Odoo shell runtime without interpreting shell commands."""

from __future__ import annotations

import configparser
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Runtime:
    workspace: Path
    python: Path
    executable: Path
    config: Path
    database: str
    addons: tuple[Path, ...]


def _bases(workspace: Path) -> list[Path]:
    bases = []
    for path in (workspace, *workspace.parents):
        if path == Path.home() or path == path.parent:
            break
        bases.append(path)
        if (path / ".git").exists() or len(bases) == 4:
            break
    return bases


def _path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    # Do not resolve symlinks: a venv's python symlink must be invoked via its venv path.
    return Path(os.path.abspath(base / path))


def resolve_runtime(workspace: Path) -> Runtime:
    workspace = workspace.expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError(f"Workspace is not a directory: {workspace}")
    bases = _bases(workspace)
    project_file = next((base / ".odoo-mcp.toml" for base in bases if (base / ".odoo-mcp.toml").is_file()), None)
    project = {}
    config_base = project_file.parent if project_file else workspace
    if project_file:
        try:
            with project_file.open("rb") as stream:
                document = tomllib.load(stream)
            project = document.get("runtime", {})
            if not isinstance(project, dict):
                raise ValueError("[runtime] must be a table")
            unknown = set(project) - {"python", "executable", "config", "database", "addons_path"}
            if unknown:
                raise ValueError(f"Unknown runtime settings: {', '.join(sorted(unknown))}")
        except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
            raise ValueError(f"Invalid {project_file}: {exc}") from exc

    def setting(key: str, variable: str):
        value = os.environ.get(variable) or project.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{variable} / runtime.{key} must be a non-empty string")
        return value

    def candidate(key: str, variable: str, names: tuple[str, ...]) -> Path | None:
        value = setting(key, variable)
        if value:
            return _path(value, config_base)
        return next((base / name for base in bases for name in names if (base / name).is_file()), None)

    python = candidate("python", "ODOO_MCP_PYTHON", (".venv/bin/python", "venv/bin/python", ".venv/Scripts/python.exe", "venv/Scripts/python.exe"))
    executable = candidate("executable", "ODOO_MCP_EXECUTABLE", ("odoo-bin", "odoo/odoo-bin"))
    config = candidate("config", "ODOO_MCP_CONFIG", ("odoo.conf", "config/odoo.conf"))
    missing = [name for name, value in (("python", python), ("executable", executable), ("config", config)) if not value]
    if missing:
        raise ValueError(f"Odoo runtime is incomplete: {', '.join(missing)}. Configure .odoo-mcp.toml or ODOO_MCP_* environment variables.")
    for label, path in (("python", python), ("executable", executable), ("config", config)):
        if not path.is_file():
            raise ValueError(f"Odoo {label} is not a file: {path}")
    if not os.access(python, os.X_OK):
        raise ValueError(f"Odoo python is not executable: {python}")

    database = setting("database", "ODOO_MCP_DATABASE")
    if not database:
        parser = configparser.ConfigParser(interpolation=None)
        try:
            with config.open(encoding="utf-8") as stream:
                parser.read_file(stream)
            database = parser.get("options", "db_name", fallback="").strip()
        except (OSError, configparser.Error) as exc:
            raise ValueError(f"Cannot read Odoo config {config}: {exc}") from exc
    if not database or database.lower() in {"false", "none"} or "," in database or database.startswith("-"):
        raise ValueError("Configure exactly one database in runtime.database, ODOO_MCP_DATABASE, or the Odoo config db_name.")

    addons_value = os.environ.get("ODOO_MCP_ADDONS_PATH")
    if addons_value:
        addons = addons_value.split(os.pathsep)
    else:
        addons = project.get("addons_path", [])
        if not isinstance(addons, list):
            raise ValueError("runtime.addons_path must be an array of directory paths")
    if any(not isinstance(p, str) or not p.strip() for p in addons):
        raise ValueError("Each addons path must be a non-empty string")
    addon_paths = tuple(_path(p, config_base) for p in addons)
    if any(not p.is_dir() or "," in str(p) for p in addon_paths):
        raise ValueError("Each addons path must be an existing directory without a comma in its name")
    return Runtime(workspace, python, executable, config, database, addon_paths)
