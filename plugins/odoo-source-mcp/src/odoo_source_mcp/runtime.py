"""Resolve a project's Odoo shell runtime without interpreting shell commands."""

from __future__ import annotations

import configparser
import os
import re
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
    working_directory: Path


def shell_environment(python: Path | None) -> dict[str, str]:
    """Build the environment shared by runtime inspection and the Odoo process."""
    environment = os.environ.copy()
    for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "PYTHONSTARTUP"):
        environment.pop(key, None)
    if python:
        environment["PATH"] = str(python.parent) + os.pathsep + environment.get("PATH", "")
    return environment


def _config_addon_path(value: str, cwd: Path, environment: dict[str, str]) -> tuple[Path, bool]:
    # Odoo 16 normalizes config entries using expandvars, expanduser, abspath,
    # realpath and normcase relative to its process cwd. Never run these values.
    def replace(match):
        name = match.group(1) or match.group(2)
        return environment.get(name, match.group(0))

    value = re.sub(r"\$([A-Za-z0-9_]+)|\$\{([^}]*)\}", replace, value.strip())
    if os.name == "nt":
        value = re.sub(r"%([^%]+)%", lambda m: environment.get(m.group(1), m.group(0)), value)
    value = os.path.expanduser(value)
    relative = not os.path.isabs(value)
    # A subprocess chdir follows symlinks; resolve cwd before applying '..'.
    physical_cwd = Path(os.path.realpath(cwd))
    return Path(os.path.normcase(os.path.realpath(os.path.abspath(physical_cwd / value)))), relative


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
    # Preserve a venv's python symlink so Python recognizes its environment.
    return Path(os.path.abspath(base / path))


def inspect_runtime(workspace: Path) -> dict:
    """Return partial results and provenance even when settings are invalid."""
    workspace = workspace.expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError(f"Workspace is not a directory: {workspace}")
    bases = _bases(workspace)
    project_file = next((base / ".odoo-mcp.toml" for base in bases if (base / ".odoo-mcp.toml").is_file()), None)
    config_base = project_file.parent if project_file else workspace
    report = {"workspace": str(workspace), "project_file": str(project_file) if project_file else None,
              "relative_base": str(config_base), "python": None, "executable": None, "config": None,
              "database": None, "working_directory": None, "addons_path": [],
              "config_addons_path": {"raw": None, "entries": [], "overridden": False},
              "sources": {}, "errors": [], "warnings": [], "ready": False}
    project = {}
    if project_file:
        try:
            with project_file.open("rb") as stream:
                document = tomllib.load(stream)
            project = document.get("runtime", {})
            if not isinstance(project, dict):
                raise ValueError("[runtime] must be a table")
            unknown = set(project) - {"python", "executable", "config", "database", "addons_path", "working_directory"}
            if unknown:
                raise ValueError(f"Unknown runtime settings: {', '.join(sorted(unknown))}")
        except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
            report["errors"].append({"field": "project_file", "message": f"Invalid {project_file}: {exc}"})
            project = {}

    def setting(key, variable):
        value = os.environ.get(variable)
        if value:
            source = {"kind": "environment", "variable": variable}
        elif key in project:
            value = project[key]
            source = {"kind": "project_file", "path": str(project_file), "key": key}
        else:
            return None
        report["sources"][key] = source
        if not isinstance(value, str) or not value.strip():
            report["errors"].append({"field": key, "message": f"{variable} / runtime.{key} must be a non-empty string"})
            return None
        return value

    for key, variable, names in (
        ("python", "ODOO_MCP_PYTHON", (".venv/bin/python", "venv/bin/python", ".venv/Scripts/python.exe", "venv/Scripts/python.exe")),
        ("executable", "ODOO_MCP_EXECUTABLE", ("odoo-bin", "odoo/odoo-bin")),
        ("config", "ODOO_MCP_CONFIG", ("odoo.conf", "config/odoo.conf")),
    ):
        value = setting(key, variable)
        path = _path(value, config_base) if value else None
        if path is None and key not in report["sources"]:
            path = next((base / name for base in bases for name in names if (base / name).is_file()), None)
            if path:
                report["sources"][key] = {"kind": "discovery", "base": str(next(base for base in bases if path.is_relative_to(base)))}
        if path:
            report[key] = str(path)
            report["sources"][key]["relative_base"] = str(config_base) if value else None
            if not path.is_file():
                report["errors"].append({"field": key, "message": f"Odoo {key} is not a file: {path} (relative base: {config_base})"})
            elif key == "python" and not os.access(path, os.X_OK):
                report["errors"].append({"field": key, "message": f"Odoo python is not executable: {path}"})
        elif key not in report["sources"]:
            report["errors"].append({"field": key, "message": f"Odoo runtime is incomplete: {key}. Configure .odoo-mcp.toml or ODOO_MCP_* environment variables."})

    cwd_value = setting("working_directory", "ODOO_MCP_WORKING_DIRECTORY")
    if cwd_value:
        cwd = _path(cwd_value, config_base)
        report["sources"]["working_directory"]["relative_base"] = str(config_base)
    elif "working_directory" not in report["sources"]:
        cwd = workspace
        report["sources"]["working_directory"] = {"kind": "workspace_default"}
    else:
        cwd = None
    if cwd:
        report["working_directory"] = str(cwd)
        if not cwd.is_dir():
            report["errors"].append({"field": "working_directory", "message": f"Odoo working_directory is not a directory: {cwd} (relative base: {config_base})"})

    database = setting("database", "ODOO_MCP_DATABASE")
    parser = None
    config_error = None
    if report["config"] and Path(report["config"]).is_file():
        try:
            parser = configparser.ConfigParser(interpolation=None)
            with open(report["config"], encoding="utf-8") as stream:
                parser.read_file(stream)
        except (OSError, configparser.Error, UnicodeError) as exc:
            parser = None
            config_error = f"Cannot read Odoo config {report['config']}: {type(exc).__name__}"
    if not database and "database" not in report["sources"]:
        if parser:
            database = parser.get("options", "db_name", fallback="").strip()
            if database:
                report["sources"]["database"] = {"kind": "odoo_config", "path": report["config"], "key": "db_name"}
        elif config_error:
            report["errors"].append({"field": "database", "message": config_error})
    if config_error:
        report["warnings"].append({"field": "config_addons_path", "message": config_error})
    if database:
        report["database"] = database
    if not database or database.lower() in {"false", "none"} or "," in database or database.startswith("-"):
        report["errors"].append({"field": "database", "message": "Configure exactly one database in runtime.database, ODOO_MCP_DATABASE, or the Odoo config db_name."})

    addons_value = os.environ.get("ODOO_MCP_ADDONS_PATH")
    if addons_value:
        addons = addons_value.split(os.pathsep)
        report["sources"]["addons_path"] = {"kind": "environment", "variable": "ODOO_MCP_ADDONS_PATH", "relative_base": str(config_base)}
    elif "addons_path" in project:
        addons = project["addons_path"]
        report["sources"]["addons_path"] = {"kind": "project_file", "path": str(project_file), "key": "addons_path", "relative_base": str(config_base)}
    else:
        addons = []
        report["sources"]["addons_path"] = {"kind": "odoo_config_default"}
    if not isinstance(addons, list) or any(not isinstance(p, str) or not p.strip() for p in addons):
        report["errors"].append({"field": "addons_path", "message": "runtime.addons_path must be an array of non-empty directory paths"})
    else:
        report["addons_path"] = [str(_path(p, config_base)) for p in addons]
        for path in report["addons_path"]:
            if not Path(path).is_dir() or "," in path:
                report["errors"].append({"field": "addons_path", "message": f"Addons path must be an existing directory without commas: {path} (relative base: {config_base})"})
    overridden = bool(report["addons_path"])
    report["config_addons_path"]["overridden"] = overridden
    if parser:
        raw = parser.get("options", "addons_path", fallback=None)
        report["config_addons_path"]["raw"] = raw
        if raw and raw.strip() != "None":
            environment = shell_environment(Path(report["python"]) if report["python"] else None)
            for item in (part.strip() for part in raw.split(",") if part.strip()):
                if cwd:
                    path, relative = _config_addon_path(item, cwd, environment)
                    entry = {"raw": item, "resolved": str(path), "relative": relative, "exists": path.is_dir()}
                    if not entry["exists"]:
                        report["warnings"].append({"field": "config_addons_path", "path": str(path), "effective": not overridden,
                                                   "message": f"Odoo config addon directory does not exist: {path}"})
                    if relative and report["sources"]["working_directory"]["kind"] == "workspace_default" and not overridden:
                        report["warnings"].append({"field": "working_directory", "path": str(path), "effective": True,
                                                   "message": "Relative addons_path uses the workspace as working directory; set working_directory if Odoo normally runs elsewhere."})
                else:
                    entry = {"raw": item, "resolved": None, "relative": None, "exists": None}
                report["config_addons_path"]["entries"].append(entry)
    report["ready"] = not report["errors"]
    return report


def resolve_runtime(workspace: Path) -> Runtime:
    report = inspect_runtime(workspace)
    if not report["ready"]:
        raise ValueError("; ".join(item["message"] for item in report["errors"]))
    return Runtime(Path(report["workspace"]), Path(report["python"]), Path(report["executable"]),
                   Path(report["config"]), report["database"], tuple(Path(p) for p in report["addons_path"]),
                   Path(report["working_directory"]))
