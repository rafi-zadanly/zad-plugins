# Odoo Source Explorer

`odoo-source-mcp` is a local MCP server and skill for navigating Odoo addon source. Its source tools trace models, fields, menus, actions, views, and XML IDs without connecting to a database. An optional shell tool can also query a configured Odoo runtime and database.

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
| `odoo_runtime_status` | Resolve and validate the selected project's shell configuration without connecting to Odoo. |
| `execute_odoo_expression` | Evaluate an ORM expression in Odoo shell; read-only by default. |

Results include source paths and line numbers. Result lists have limits and report truncation; for `find_model`, the limit also applies to the fields and methods returned per class. The in-memory index rebuilds when file paths, sizes or modification times change, and checks the source tree on each query.

Trace tools follow only statically identifiable relationships. `trace_model` follows ancestors and reports direct descendants; `find_method` does not resolve inherited Python/Odoo method order. Delegation via `_inherits` exposes fields, not methods. `trace_view` reports XPath and top-level field or element selectors in inherited views and templates; it does not apply selectors or resolve view application order. `trace_action` reports direct menu references, indexed `ir.actions.act_window.view` records, and candidate views matching the action's model or explicit view references. Dynamic references, view records without XML IDs and action view lists may be missing. Inspect the cited source before relying on a trace.

## Odoo shell setup

The source tools need no runtime configuration. For shell access, create `.odoo-mcp.toml` at your Odoo project root; see the complete [example](.odoo-mcp.toml.example):

```toml
[runtime]
python = ".venv/bin/python"
executable = "odoo/odoo-bin"
config = "config/odoo.conf"
database = "project_a_dev"
# Optional: overrides addons_path in the Odoo config.
# addons_path = ["odoo/addons", "custom-addons"]
```

Choose the Python interpreter from the **Odoo project's** virtual environment, and the actual `odoo-bin` script. These are separate from the plugin's `uv` environment. Use a development database and a trusted local project. Paths in this file are relative to its directory; absolute paths are also supported. The `addons_path` override is an array of directories. If omitted, Odoo uses its own config. Keep Odoo database credentials in the Odoo config or another appropriate private mechanism, not this example file.

Each setting is resolved in this order: nonempty environment variable, nearest `.odoo-mcp.toml`, then bounded discovery. The environment variables are documented in [.env.example](.env.example): `ODOO_MCP_PYTHON`, `ODOO_MCP_EXECUTABLE`, `ODOO_MCP_CONFIG`, `ODOO_MCP_DATABASE` and optional `ODOO_MCP_ADDONS_PATH` (OS-path-separated). Relative environment paths use the TOML directory if found, otherwise the workspace. `.env` is **not** loaded automatically: export the variables in the process launching the client or provide them in the client configuration.

Discovery checks the workspace and its ancestors up to the nearest Git root, at most four directories, and never the home or filesystem root. It looks for `.venv/bin/python` or `venv/bin/python` (or Windows equivalents), `odoo-bin` or `odoo/odoo-bin`, and `odoo.conf` or `config/odoo.conf`. If database is not set explicitly, it reads a single `db_name` from `[options]` in the resolved Odoo config. Missing, ambiguous, or invalid values fail with an error; discovery never runs commands from configuration or `AGENTS.md`. Use `odoo_runtime_status` to review the selected interpreter, executable, config, database, and any addon override. This validates paths, not database connectivity or Python/Odoo compatibility.

Pass the absolute Odoo project path as `workspace`; otherwise the server uses `ODOO_SOURCE_ROOT` or exactly one MCP workspace root. The installed plugin directory is not the project workspace.

### Codex configuration

The marketplace plugin already registers the MCP server. Put `.odoo-mcp.toml` in each project; no additional Codex server registration is needed. To require confirmation for shell calls, set this in the user's or trusted project's `.codex/config.toml` ([Codex MCP configuration](https://developers.openai.com/codex/mcp)):

```toml
[plugins."odoo-source-mcp@zad-plugins".mcp_servers.odoo-source-mcp.tools.execute_odoo_expression]
approval_mode = "prompt"
```

Alternatively, for a direct checkout registration without the plugin, add this to `.codex/config.toml` using the absolute checkout path (do not register both copies unless you intend to):

```toml
[mcp_servers.odoo-source-mcp]
command = "uv"
args = ["run", "--locked", "--project", "/absolute/path/to/zad-plugins/plugins/odoo-source-mcp", "odoo-source-mcp"]

[mcp_servers.odoo-source-mcp.env]
ODOO_MCP_DATABASE = "project_a_dev"
```

For direct registration, set other `ODOO_MCP_*` variables in the same `env` table if needed. For the installed plugin, prefer the per-project TOML or provide variables in the launching environment; plugin-scoped Codex settings above control tool policy, not the launcher's environment. Restart the client after changing its configuration.

### Shell usage and safety

Example tool arguments:

```json
{"workspace": "/absolute/path/to/project", "expression": "env['res.partner'].search_read([], fields=['name'], limit=5)"}
```

Read-only mode accepts `env['model']` and chains of `browse`, `search`, `search_count`, `search_read`, `read`, `fields_get`, `name_get`, `exists`, and `mapped`, with literal arguments; `.id` and `.ids` are supported. Results are JSON, with recordsets represented as model and IDs. Other expressions require `allow_mutation: true`; this permits Python expressions using `env` without providing builtins, so only use it when explicitly requested. For example, a write that is rolled back after the call:

```json
{"workspace": "/absolute/path/to/project", "expression": "env['res.partner'].browse(42).write({'name': 'Example'})", "allow_mutation": true}
```

Add `"commit": true` to explicitly persist the transaction. `commit` requires `allow_mutation: true`. Calls time out after 30 seconds by default; `timeout` can be set from 1 to 120 seconds. Errors are returned as structured results, and oversized results are rejected. Startup diagnostics on stderr are not returned; configure Odoo's logfile if detailed startup logs are needed. If a committed call times out or the process fails after committing, its final database state may be unknown; inspect the database before retrying.

Odoo shell runs as the Odoo superuser, bypassing normal user access rules. Default read-only calls restrict expression syntax, set PostgreSQL's default transaction mode to read-only, and block cursor commits during expression evaluation; uncommitted changes are rolled back. Odoo's [shell documentation](https://www.odoo.com/documentation/16.0/developer/cli.html#shell) describes its transaction behavior. These safeguards reduce accidental changes, but **are not a security sandbox**: trusted addon code and explicitly enabled expressions can perform external side effects or use other connections. A read-only PostgreSQL role gives stronger database protection. Database values and error messages are returned to the MCP client; avoid sensitive data and use only trusted runtimes.

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
