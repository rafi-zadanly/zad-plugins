# Odoo Source Explorer

`odoo-source-mcp` is a local, read-only MCP server and skill for navigating Odoo addon source. It helps an agent trace models, fields, menus, actions, views, and XML IDs without running Odoo or connecting to a database.

## Installation

Install the plugin through the Zad Plugins marketplace in [Codex](../../README.md#install-in-codex) or [Claude Code](../../README.md#install-in-claude-code). `uv` is required; Python and the locked dependencies can be provisioned automatically by `uv` on first launch. Start a new session after installation.

## Usage

Open your Odoo project as the Codex or Claude Code workspace and ask the agent to investigate its source. For example:

- “Find the definitions and extensions of `sale.order`.”
- “Where is the `partner_id` field defined on `sale.order`, and which views reference it?”
- “Trace the menu for sales orders to its action and model.”
- “Find the definition and references of the XML ID `sale.action_orders`.”
- “Trace the parent models and direct descendants of `sale.order`.”
- “Trace this view's inheritance and XPath selectors.”
- “Find definitions and overrides of `action_confirm` on `sale.order`.”
- “Trace the action XML ID to its menus, model and candidate views.”

The bundled `odoo-source-mcp` skill instructs the agent to pass the current workspace to the tools and verify results against the source. If your session has multiple workspace roots, specify which project to inspect.

## Tools

| Tool | Purpose |
| --- | --- |
| `source_status` | Show discovered addon roots, index counts and parse errors; optionally rebuild the index. |
| `find_model` | Find direct model definitions and extensions, inheritance, fields and methods. |
| `find_field` | Find direct field definitions, relational targets and candidate view references. |
| `find_menu` | Trace matching menus to parent, action, model and candidate views. |
| `find_xml_id` | Find XML ID definitions and common static references. |
| `trace_model` | Follow model inheritance and delegation ancestors; show definitions and direct descendants. |
| `trace_view` | Trace view and template inheritance by XML ID in both directions and show source selectors. |
| `find_method` | Find direct method definitions and extension candidates on a model. |
| `trace_action` | Find an action by XML ID and its candidate menus, models and views. |

Results include source paths and line numbers. Result lists have limits and report truncation; for `find_model`, the limit also applies to the fields and methods returned per class. The in-memory index rebuilds when file paths, sizes or modification times change, and checks the source tree on each query.

Trace tools follow only statically identifiable relationships. `trace_model` follows ancestors and reports direct descendants; `find_method` does not resolve inherited Python/Odoo method order. Delegation via `_inherits` exposes fields, not methods. `trace_view` reports XPath and top-level field or element selectors in inherited views and templates; it does not apply selectors or resolve view application order. `trace_action` reports direct menu references, indexed `ir.actions.act_window.view` records, and candidate views matching the action's model or explicit view references. Dynamic references, view records without XML IDs and action view lists may be missing. Inspect the cited source before relying on a trace.

## Workspace discovery

The server looks at the workspace, its immediate children, `odoo/addons`, and ancestors up to the nearest Git root, or at most three ancestors without a Git root. It does not read Odoo configuration or search the whole filesystem. If no unambiguous workspace is available, it returns an error instead of silently scanning another project. Check `source_status` for selected roots and parse errors.

When no `workspace` argument is provided, the server uses `ODOO_ADDONS_PATH`, then `ODOO_SOURCE_ROOT`, then a single local MCP root supplied by the client. `ODOO_ADDONS_PATH` is an OS-path-separated list of addon **containers** (directories containing module folders); each entry must be an existing directory, though it can be empty. `ODOO_SOURCE_ROOT` is a project root to discover from. An explicit workspace takes precedence over these environment variables, so the bundled skill's workspace selection normally takes priority. For nonstandard layouts, ask the agent to select a suitable workspace, or configure the fallback for clients that omit it.

Discovery and indexing skip symlinked addon modules and manifests. A container that is itself a symlink can be selected explicitly as a workspace or through `ODOO_ADDONS_PATH`.

## Compatibility and limitations

The initial design targets common Odoo 16–18 addon patterns, including [ORM inheritance](https://www.odoo.com/documentation/16.0/developer/reference/backend/orm.html), [view inheritance](https://www.odoo.com/documentation/16.0/developer/reference/backend/views.html), [actions](https://www.odoo.com/documentation/16.0/developer/reference/backend/actions.html), and [XML data](https://www.odoo.com/documentation/16.0/developer/reference/backend/data.html). It supports field references in Odoo 17 `tree` and Odoo 18 `list` view roots but does not validate version-specific view syntax. Integration against actual Odoo 17 and 18 installations has not been verified.

Results are static candidates, not proof of installed modules, final registry or views, access rules, or runtime behavior. The index can include files not imported or listed in a manifest; duplicate module names are not resolved in Odoo load order. Dynamic declarations and some XML references may be missed. Fields from inherited models are not resolved automatically, and nested view fields may belong to another model. Tests, static assets and hidden directories are excluded. Inspect the original source and use Odoo runtime checks when needed.

Only use trusted addon roots: source metadata is sent to the connected MCP client.

## Development

From this directory:

```sh
uv sync --python 3.11
uv run python -m unittest discover -s tests -v
```

The plugin requires Python 3.11 or newer, independently of the Python version used by Odoo. `uv` can provision it automatically.
