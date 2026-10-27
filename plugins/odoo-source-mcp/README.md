# Odoo Source Explorer

`odoo-source-mcp` is a local, read-only MCP server and skill for navigating Odoo addon source. It helps an agent trace models, fields, menus, actions, views, and XML IDs without running Odoo or connecting to a database.

## Installation

Install the plugin through the [Zad Plugins marketplace](../../README.md#install-in-codex). Codex and `uv` are required; Python and the locked dependencies can be provisioned automatically by `uv` on first launch. Start a new Codex session after installation.

## Usage

Open your Odoo project as the Codex workspace and ask the agent to investigate its source. For example:

- “Find the definitions and extensions of `sale.order`.”
- “Where is the `partner_id` field defined on `sale.order`, and which views reference it?”
- “Trace the menu for sales orders to its action and model.”
- “Find the definition and references of the XML ID `sale.action_orders`.”

The bundled `odoo-source-mcp` skill instructs the agent to pass the current workspace to the tools and verify results against the source. If your session has multiple workspace roots, specify which project to inspect.

## Tools

| Tool | Purpose |
| --- | --- |
| `source_status` | Show discovered addon roots, index counts and parse errors; optionally rebuild the index. |
| `find_model` | Find direct model definitions and extensions, inheritance, fields and methods. |
| `find_field` | Find direct field definitions, relational targets and candidate view references. |
| `find_menu` | Trace matching menus to parent, action, model and candidate views. |
| `find_xml_id` | Find XML ID definitions and common static references. |

Results include source paths and line numbers. Result lists have limits and report truncation; for `find_model`, the limit also applies to the fields and methods returned per class. The in-memory index rebuilds when file paths, sizes or modification times change, and checks the source tree on each query.

## Workspace discovery

The server looks at the workspace, its immediate children, `odoo/addons`, and ancestors up to the nearest Git root, or at most three ancestors without a Git root. It does not read Odoo configuration or search the whole filesystem. If no unambiguous workspace is available, it returns an error instead of silently scanning another project. Check `source_status` for selected roots and parse errors.

When no `workspace` argument is provided, the server uses `ODOO_ADDONS_PATH`, then `ODOO_SOURCE_ROOT`, then a single local MCP root supplied by the client. `ODOO_ADDONS_PATH` is an OS-path-separated list of addon **containers** (directories containing module folders); each entry must be an existing directory, though it can be empty. `ODOO_SOURCE_ROOT` is a project root to discover from. An explicit workspace takes precedence over these environment variables, so the bundled skill's workspace selection normally takes priority. For nonstandard layouts, ask the agent to select a suitable workspace, or configure the fallback for clients that omit it.

Discovery and indexing skip symlinked addon modules and manifests. A container that is itself a symlink can be selected explicitly as a workspace or through `ODOO_ADDONS_PATH`.

## Compatibility and limitations

The initial design targets common Odoo 16–18 addon patterns, including [ORM inheritance](https://www.odoo.com/documentation/16.0/developer/reference/backend/orm.html) and [XML data](https://www.odoo.com/documentation/18.0/developer/reference/backend/data.html). It supports field references in Odoo 17 `tree` and Odoo 18 `list` view roots but does not validate version-specific view syntax. Integration against actual Odoo 17 and 18 installations has not been verified.

Results are static candidates, not proof of installed modules, final registry or views, access rules, or runtime behavior. The index can include files not imported or listed in a manifest; duplicate module names are not resolved in Odoo load order. Dynamic declarations and some XML references may be missed. Fields from inherited models are not resolved automatically, and nested view fields may belong to another model. Tests, static assets and hidden directories are excluded. Inspect the original source and use Odoo runtime checks when needed.

Only use trusted addon roots: source metadata is sent to the connected MCP client.

## Development

From this directory:

```sh
uv sync --python 3.11
uv run python -m unittest discover -s tests -v
```

The plugin requires Python 3.11 or newer, independently of the Python version used by Odoo. `uv` can provision it automatically.
