"""MCP entry point for Odoo source navigation and optional shell access."""

import asyncio
import os
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from mcp.server.fastmcp import FastMCP
from mcp.types import ClientCapabilities, RootsCapability, ToolAnnotations

from .diagnostics import diagnose_runtime, inspect_access, observations, source_diagnostic
from .index import SourceIndex
from .inspection import field_behavior, inspect_module as inspect_module_source, method_calls, object_buttons, state_assignments
from .runtime import inspect_runtime, resolve_runtime
from .shell import execute
from .workspace import discover_roots

mcp = FastMCP("Odoo Source Explorer")
indexes = {}


def roots_from_uri(uris):
    paths = []
    for uri in uris:
        parsed = urlparse(str(uri))
        if parsed.scheme == "file" and parsed.netloc in ("", "localhost"):
            paths.append(Path(url2pathname(parsed.path)))
    return paths


async def runtime_workspace(workspace: str | None) -> Path:
    if workspace:
        path = Path(workspace).expanduser()
        if not path.is_absolute():
            raise ValueError("workspace must be an absolute path to the current Odoo project")
        return path
    if os.environ.get("ODOO_SOURCE_ROOT"):
        return Path(os.environ["ODOO_SOURCE_ROOT"]).expanduser()
    session = mcp.get_context().session
    paths = []
    if session.check_client_capability(ClientCapabilities(roots=RootsCapability())):
        paths = roots_from_uri([root.uri for root in (await session.list_roots()).roots])
    if len(paths) != 1:
        raise ValueError("Pass workspace as the absolute path of the current Odoo project; the MCP client did not provide exactly one local workspace root.")
    return paths[0]


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
async def odoo_runtime_status(workspace: str | None = None) -> dict:
    """Resolve configuration and its sources, including partial results, without connecting to the database."""
    return inspect_runtime(await runtime_workspace(workspace))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True))
async def execute_odoo_expression(expression: str, workspace: str | None = None, allow_mutation: bool = False,
                                  commit: bool = False, timeout: int = 30, include_stderr: bool = False) -> dict:
    """Execute an ORM expression in Odoo shell. Read-only by default; mutations and commits require explicit opt-in.

    Odoo shell runs as superuser. Only use a trusted project and runtime; this is not a sandbox.
    """
    runtime = resolve_runtime(await runtime_workspace(workspace))
    return await asyncio.to_thread(execute, runtime, expression, allow_mutation, commit, timeout, include_stderr)


def _validate_model(model: str) -> None:
    if not isinstance(model, str) or not model.strip() or len(model) > 200:
        raise ValueError("model must contain 1 to 200 characters")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
async def diagnose_model(model: str, workspace: str | None = None, limit: int = 50,
                         timeout: int = 30, include_stderr: bool = False) -> dict:
    """Compare static source candidates, imports, database metadata, module states and the shell registry."""
    _validate_model(model)
    project = await runtime_workspace(workspace)
    config = inspect_runtime(project)
    try:
        index = await get_index(str(project))
        index.refresh()
        source = index_result(index, source_diagnostic(index, model, limit))
        names = sorted({c["module"] for c in index.classes if model in c["models"]})
    except (ValueError, OSError) as exc:
        source = {"found": None, "items": [], "error": str(exc)}
        names = []
    if config["ready"]:
        runtime = await asyncio.to_thread(diagnose_runtime, resolve_runtime(project), model, names[:100], timeout, include_stderr)
    else:
        runtime = {"ok": False, "error": {"type": "ConfigurationError", "stage": "configuration", "message": "Odoo runtime configuration is incomplete or invalid."}}
    if not runtime.get("ok"):
        runtime["result"] = {"registry": {"present": None}, "metadata": {"present": None}, "modules": {"records": None}}
    return {"model": model, "configuration": config, "source": source, "runtime": runtime,
            "source_module_candidates": names[:100], "source_module_candidates_truncated": len(names) > 100,
            "findings": observations(source, runtime)}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
async def inspect_model_access(model: str, user_id: int, workspace: str | None = None,
                               company_ids: list[int] | None = None, record_ids: list[int] | None = None,
                               timeout: int = 30, include_stderr: bool = False) -> dict:
    """Inspect model ACL and selected record rules as a specific user and company context; never mutate records."""
    _validate_model(model)
    if isinstance(user_id, bool) or user_id < 1:
        raise ValueError("user_id must be a positive integer")
    for label, values in (("company_ids", company_ids), ("record_ids", record_ids)):
        if values is not None and (len(values) > 20 or any(isinstance(value, bool) or value < 1 for value in values)):
            raise ValueError(f"{label} must contain at most 20 positive integers")
    if company_ids == []:
        raise ValueError("company_ids must not be empty; omit it to use the user's default company")
    runtime = resolve_runtime(await runtime_workspace(workspace))
    return await asyncio.to_thread(inspect_access, runtime, model, user_id, company_ids, record_ids or [], timeout, include_stderr)


async def get_index(workspace: str | None):
    if workspace:
        path = Path(workspace).expanduser()
        if not path.is_absolute():
            raise ValueError("workspace must be an absolute path to the current Odoo project")
        roots = discover_roots(path)
    elif os.environ.get("ODOO_ADDONS_PATH"):
        roots = [Path(p).expanduser().resolve() for p in os.environ["ODOO_ADDONS_PATH"].split(os.pathsep) if p]
        invalid = [str(p) for p in roots if not p.is_dir()]
        if invalid:
            raise ValueError(f"ODOO_ADDONS_PATH contains paths that are not directories: {', '.join(invalid)}")
    elif os.environ.get("ODOO_SOURCE_ROOT"):
        roots = discover_roots(Path(os.environ["ODOO_SOURCE_ROOT"]))
    else:
        session = mcp.get_context().session
        paths = []
        if session.check_client_capability(ClientCapabilities(roots=RootsCapability())):
            paths = roots_from_uri([root.uri for root in (await session.list_roots()).roots])
        if len(paths) != 1:
            raise ValueError("Pass workspace as the absolute path of the current Odoo project; the MCP client did not provide exactly one local workspace root.")
        roots = discover_roots(paths[0])
    if not roots:
        raise ValueError("No Odoo addon directories found. Pass the Odoo project root as workspace, or configure ODOO_ADDONS_PATH.")
    key = tuple(str(p.resolve()) for p in roots)
    if key not in indexes:
        indexes[key] = SourceIndex(list(roots))
    return indexes[key]


def index_result(index: SourceIndex, result: dict) -> dict:
    """Expose index errors without suggesting they are specific to the requested result."""
    result["index_error_count"] = len(index.errors)
    if index.errors:
        result["index_error_note"] = "The index contains source errors; results may be incomplete. See source_status for details."
    return result


@mcp.tool()
async def source_status(workspace: str | None = None, rebuild: bool = False) -> dict:
    """Show addon roots, index counts and parse errors. Pass the absolute session workspace path."""
    index = await get_index(workspace)
    index.refresh(force=rebuild)
    return index.status()


@mcp.tool()
async def find_model(model: str, workspace: str | None = None, limit: int = 50, offset: int = 0,
                     field_offset: int = 0, method_offset: int = 0) -> dict:
    """Find model classes, fields and methods; page classes and each class's members using offsets."""
    index = await get_index(workspace)
    index.refresh()
    return index_result(index, index.model(model, limit, offset, field_offset, method_offset))


@mcp.tool()
async def discover_models(workspace: str | None = None, module: str | None = None, folder: str | None = None,
                          query: str | None = None, offset: int = 0, limit: int = 50) -> dict:
    """List static model class candidates in a module or absolute folder, with pagination."""
    index = await get_index(workspace)
    index.refresh()
    return index_result(index, index.discover_models(module, folder, query, offset, limit))


@mcp.tool()
async def find_models(models: list[str], workspace: str | None = None, limit: int = 10) -> dict:
    """Find up to 20 models in one request; limit each result and its fields and methods to 10."""
    if not 1 <= len(models) <= 20:
        raise ValueError("models must contain between 1 and 20 names")
    for model in models:
        _validate_model(model)
    index = await get_index(workspace)
    index.refresh()
    limit = max(1, min(limit, 10))
    return index_result(index, {"results": [{"model": model, **index.model(model, limit)} for model in models],
            "limit_per_model": limit,
            "note": "Static source candidates only; results do not establish installed modules or runtime behavior."})


@mcp.tool()
async def inspect_module(module: str, workspace: str | None = None, limit: int = 20, offset: int = 0) -> dict:
    """Inspect a module manifest and static import paths to indexed Python source files."""
    _validate_model(module)
    index = await get_index(workspace)
    index.refresh()
    return index_result(index, inspect_module_source(index, module, limit, offset))


@mcp.tool()
async def inspect_field_behavior(model: str, field: str, workspace: str | None = None, limit: int = 20) -> dict:
    """Find static field behavior declarations and candidate decorated methods."""
    _validate_model(model)
    _validate_model(field)
    index = await get_index(workspace)
    index.refresh()
    return index_result(index, field_behavior(index, model, field, limit))


@mcp.tool()
async def trace_method_calls(model: str, method: str, workspace: str | None = None, limit: int = 20,
                             caller_offset: int = 0, callee_offset: int = 0, caller_module: str | None = None,
                             caller_folder: str | None = None) -> dict:
    """Find and page candidate calls within indexed model classes; runtime dispatch is unresolved."""
    _validate_model(model)
    _validate_model(method)
    index = await get_index(workspace)
    index.refresh()
    return index_result(index, method_calls(index, model, method, limit, caller_offset, callee_offset, caller_module, caller_folder))


@mcp.tool()
async def find_state_assignments(model: str, workspace: str | None = None, field: str = "state",
                                 method: str | None = None, limit: int = 20, offset: int = 0) -> dict:
    """Find static assignments to a chosen state field in indexed model classes."""
    _validate_model(model)
    _validate_model(field)
    if method is not None:
        _validate_model(method)
    index = await get_index(workspace)
    index.refresh()
    return index_result(index, state_assignments(index, model, field, method, limit, offset))


@mcp.tool()
async def find_object_buttons(model: str, workspace: str | None = None, method: str | None = None,
                              limit: int = 20, offset: int = 0) -> dict:
    """Find object-button syntax and model/method candidates, including inherited views."""
    _validate_model(model)
    if method is not None:
        _validate_model(method)
    index = await get_index(workspace)
    index.refresh()
    return index_result(index, object_buttons(index, model, method, limit, offset))


@mcp.tool()
async def find_field(model: str, field: str, workspace: str | None = None, limit: int = 50) -> dict:
    """Find field definitions and candidate view references. Pass the absolute session workspace path."""
    index = await get_index(workspace)
    index.refresh()
    return index_result(index, index.field(model, field, limit))


@mcp.tool()
async def find_menu(query: str, workspace: str | None = None, limit: int = 20) -> dict:
    """Trace menu, parent, action, model and candidate views. Pass the absolute session workspace path."""
    index = await get_index(workspace)
    index.refresh()
    return index_result(index, index.menus(query, limit))


@mcp.tool()
async def find_xml_id(xml_id: str, workspace: str | None = None, limit: int = 50) -> dict:
    """Find XML ID definitions and common references. Pass the absolute session workspace path."""
    index = await get_index(workspace)
    index.refresh()
    return index_result(index, index.xml_id(xml_id, limit))


@mcp.tool()
async def trace_model(model: str, workspace: str | None = None, limit: int = 50) -> dict:
    """Trace static model inheritance, delegation and direct descendant candidates."""
    index = await get_index(workspace)
    index.refresh()
    return index_result(index, index.trace_model(model, limit))


@mcp.tool()
async def trace_view(xml_id: str, workspace: str | None = None, limit: int = 50) -> dict:
    """Trace static view/template inheritance and source selectors by XML ID."""
    index = await get_index(workspace)
    index.refresh()
    return index_result(index, index.trace_view(xml_id, limit))


@mcp.tool()
async def find_method(model: str, method: str, workspace: str | None = None, limit: int = 50) -> dict:
    """Find direct definitions and extension candidates for a method on a model."""
    index = await get_index(workspace)
    index.refresh()
    return index_result(index, index.find_method(model, method, limit))


@mcp.tool()
async def trace_action(xml_id: str, workspace: str | None = None, limit: int = 50) -> dict:
    """Trace an action XML ID to candidate menus, models and views."""
    index = await get_index(workspace)
    index.refresh()
    return index_result(index, index.trace_action(xml_id, limit))


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
