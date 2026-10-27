"""MCP entry point. All tools inspect local source without executing Odoo code."""

import os
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from mcp.server.fastmcp import FastMCP
from mcp.types import ClientCapabilities, RootsCapability

from .index import SourceIndex
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


@mcp.tool()
async def source_status(workspace: str | None = None, rebuild: bool = False) -> dict:
    """Show addon roots, index counts and parse errors. Pass the absolute session workspace path."""
    index = await get_index(workspace)
    index.refresh(force=rebuild)
    return index.status()


@mcp.tool()
async def find_model(model: str, workspace: str | None = None, limit: int = 50) -> dict:
    """Find model definitions/extensions, fields and methods. Pass the absolute session workspace path."""
    index = await get_index(workspace)
    index.refresh()
    return index.model(model, limit)


@mcp.tool()
async def find_field(model: str, field: str, workspace: str | None = None, limit: int = 50) -> dict:
    """Find field definitions and candidate view references. Pass the absolute session workspace path."""
    index = await get_index(workspace)
    index.refresh()
    return index.field(model, field, limit)


@mcp.tool()
async def find_menu(query: str, workspace: str | None = None, limit: int = 20) -> dict:
    """Trace menu, parent, action, model and candidate views. Pass the absolute session workspace path."""
    index = await get_index(workspace)
    index.refresh()
    return index.menus(query, limit)


@mcp.tool()
async def find_xml_id(xml_id: str, workspace: str | None = None, limit: int = 50) -> dict:
    """Find XML ID definitions and common references. Pass the absolute session workspace path."""
    index = await get_index(workspace)
    index.refresh()
    return index.xml_id(xml_id, limit)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
