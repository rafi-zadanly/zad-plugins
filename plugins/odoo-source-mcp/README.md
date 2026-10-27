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
| `discover_models` | List indexed model class candidates in a module or absolute folder, with optional model-name filtering and pagination. |
| `find_model` | Find direct model definitions and extensions, inheritance, fields and methods. |
| `find_models` | Look up up to 20 model names in one bounded request. |
| `inspect_module` | Summarize manifest dependencies and data, plus import paths to indexed model source files. |
| `inspect_field_behavior` | Inspect field behavior declarations and related decorator candidates. |
| `inspect_runtime_fields` | Inspect selected effective field metadata using the configured Odoo registry. |
| `trace_method_calls` | Find candidate callers and callees within a model's indexed classes. |
| `find_automation_triggers` | Find static cron and server action declarations without executing them. |
| `find_state_assignments` | Find static assignment and write syntax for a selected model field. |
| `find_object_buttons` | Find object buttons and candidate model methods, including inherited view candidates. |
| `find_field` | Find direct field definitions, relational targets and candidate view references. |
| `find_menu` | Trace matching menus to parent, action, model and candidate views. |
| `find_xml_id` | Find XML ID definitions and common static references. |
| `trace_model` | Follow model inheritance and delegation ancestors; show definitions and direct descendants. |
| `trace_view` | Trace view and template inheritance by XML ID in both directions and show source selectors. |
| `find_method` | Find direct method definitions and extension candidates on a model. |
| `trace_action` | Find an action by XML ID and its candidate menus, models and views. |
| `odoo_runtime_status` | Resolve and validate the selected project's shell configuration without connecting to Odoo. |
| `execute_odoo_expression` | Evaluate an ORM expression in Odoo shell; read-only by default. |
| `diagnose_model` | Compare source candidates and import paths with model metadata, module states, and the shell registry. |
| `inspect_model_access` | Inspect a specific user's model ACL and record rules in a selected company context. |

Results include source paths and line numbers. Result lists have limits and report truncation; for `find_model`, the limit also applies to the fields and methods returned per class. The in-memory index rebuilds when file paths, sizes or modification times change, and checks the source tree on each query.

Source lookup results include `index_error_count` for the entire selected index; a nonzero count means results may be incomplete, not that every error affects the query. Use `source_status` for error details. `inspect_field_behavior` and `trace_method_calls` also report bounded `inspection_issues` if a previously indexed class cannot be read or located during inspection.

For module-wide exploration, call `discover_models` with `workspace` and an exact `module` name or an absolute `folder`; both filters may be combined. Optional `query` is a case-insensitive substring of the model name. Each row represents one model/class pair, including extension classes; counts are declarations in that class, not runtime totals. Results are sorted by model, module, path and line, and paginated with `offset` and `limit` (default 50, maximum 100); use `next_offset` to fetch the next page. Pagination reflects the current index and may shift if source changes between calls. Only indexed addon source is included.

Use `find_models(models=["sale.order", "sale.order.line"], workspace="/absolute/project")` for up to 20 names, in input order. Its `limit` defaults to 10 and is capped at 10 for each model's classes, fields and methods; each result retains `find_model`'s totals and truncation indicators. `find_model` remains unchanged. All results are static candidates, not proof of installed modules or registry behavior.

For larger results, `find_model` accepts `offset` for classes and `field_offset` / `method_offset` for members within every returned class. Use `next_offset`, `fields_next_offset`, and `methods_next_offset` from the result; set `limit=1` and the class `offset` to page members of a particular class. `trace_method_calls` separately accepts `caller_offset` and `callee_offset`, with `next_offset` in each result list. These offsets refer to the current source index and may shift after edits. The batch tool remains bounded; follow up with `find_model` to page an individual model.

`inspect_module` examines manifest `depends` and `data` and traces imports to indexed Python files, excluding the manifest. Page through source files using `offset`, `limit`, and each module's `source_files_next_offset`; if multiple module directories share the same name, the same offset applies to each. Results can shift when source changes between calls. The index excludes test, static, hidden and symlinked paths. `no_static_path_found` means a possible unloaded source file, not proof: conditional and dynamic imports and runtime loading are unresolved. At most 50 source files per module and 200 import nodes per file are inspected. `inspect_field_behavior` matches literal field method names and literal `api.depends`, `api.onchange`, and `api.constrains` arguments; nonliteral field attributes and decorator arguments are marked unresolved, and aliased declarations may be missed. `trace_method_calls` scans at most 100 indexed classes of the requested model and, when scoped, additional callers. Nested function, lambda and class bodies are excluded; dynamic calls and runtime method resolution are not traced. Results are bounded static candidates, not verified execution paths.

Trace tools follow only statically identifiable relationships. `trace_model` follows ancestors and reports direct descendants; `find_method` does not resolve inherited Python/Odoo method order. Delegation via `_inherits` exposes fields, not methods. `trace_view` reports XPath and top-level field or element selectors in inherited views and templates; it does not apply selectors or resolve view application order. `trace_action` reports direct menu references, indexed `ir.actions.act_window.view` records, and candidate views matching the action's model or explicit view references. Dynamic references, view records without XML IDs and action view lists may be missing. Inspect the cited source before relying on a trace.

For workflow evidence, `find_state_assignments(model="sale.order", field="state", method="action_confirm", workspace="/absolute/project")` finds attribute assignments and literal `write` dictionaries, plus simple single-use local dictionaries. Nonliteral values have `value_resolved=false` and an expression; dynamic or modified write arguments appear under `unresolved_writes` and may not affect the requested field. Receivers other than `self` are unresolved. Results are syntax evidence, not verified execution, reachability, or runtime transitions; aliases, dynamic calls, subscript assignments, and complex data flow may be missed.

`find_object_buttons(model="sale.order", method="action_confirm", workspace="/absolute/project")` reports XML object buttons with view and method candidates. View model resolution follows indexed `inherit_id` parents; nested relational views and missing models are ambiguous and listed under `unresolved_buttons`. It does not apply XPath, infer relational subview models, or determine effective views. Both tools accept `limit` and `offset`; counts and `next_offset` describe returned evidence, and inspection is limited to 100 model classes for state assignments.

To search cross-model callers, pass `caller_module="my_addon"` or `caller_folder="/absolute/project/addons/my_addon"` to `trace_method_calls`. At most 100 additional indexed classes in that scope are scanned; `scoped_classes_truncated` signals omissions. Direct `self.env['target.model'].method()` is syntax evidence; `self.relation_field.method()` is inferred from indexed relational field declarations and inheritance candidates. Conflicting or dynamic declarations are marked ambiguous; declaration paths and lines are included. Variables, chained calls, delegation, aliases, and dynamic dispatch may be missed. Static matches do not establish runtime execution.

Use `find_automation_triggers` with a `model` or `module` filter to find indexed `ir.cron` and `ir.actions.server` records, their model references and method-call syntax. For example: `{"module":"my_addon", "limit":20}`. Calls are parsed as source and never executed. A generated `model_*` XML ID match is only a candidate, unresolved models remain unresolved, and dynamic action links or declarations without indexed XML records may be missed. Code line numbers are relative to the code field; the XML record path and line locate the declaration.

`find_object_buttons` includes declared UI condition attributes, and `trace_view` includes bounded condition evidence from indexed view XML. These attributes are raw source syntax; inheritance selectors, user groups, context and runtime modifiers can change the effective view. They do not prove visibility or access.

## Odoo shell setup

The source tools need no runtime configuration. For shell access, create `.odoo-mcp.toml` at your Odoo project root; see the complete [example](.odoo-mcp.toml.example):

```toml
[runtime]
python = ".venv/bin/python"
executable = "odoo/odoo-bin"
config = "config/odoo.conf"
database = "project_a_dev"
# Optional: Odoo shell's working directory. Defaults to the session workspace.
# working_directory = "."
# Optional: overrides addons_path in the Odoo config.
# addons_path = ["odoo/addons", "custom-addons"]
```

Choose the Python interpreter from the **Odoo project's** virtual environment, and the actual `odoo-bin` script. These are separate from the plugin's `uv` environment. Use a development database and a trusted local project. Paths in this file are relative to its directory; absolute paths are also supported. The `addons_path` override is an array of directories. If omitted, Odoo uses its own config. Keep Odoo database credentials in the Odoo config or another appropriate private mechanism, not this example file.

Each setting is resolved in this order: nonempty environment variable, nearest `.odoo-mcp.toml`, then bounded discovery. The environment variables are documented in [.env.example](.env.example): `ODOO_MCP_PYTHON`, `ODOO_MCP_EXECUTABLE`, `ODOO_MCP_CONFIG`, `ODOO_MCP_DATABASE`, `ODOO_MCP_WORKING_DIRECTORY` and optional `ODOO_MCP_ADDONS_PATH` (OS-path-separated). Relative environment paths use the TOML directory if found, otherwise the workspace. `.env` is **not** loaded automatically: export the variables in the process launching the client or provide them in the client configuration.

If Odoo normally starts from a directory other than the workspace, set `working_directory`. For example, when `.odoo-mcp.toml` is inside a project folder directly below the Odoo root:

```toml
[runtime]
python = "../.venv/bin/python"
executable = "../odoo-bin"
config = "../odoo.conf"
database = "mhg_dev"
working_directory = ".."
```

The working directory is resolved relative to `.odoo-mcp.toml`, just like the other runtime paths. When omitted, it remains the session workspace; the directory containing `odoo.conf` is not automatically selected. Odoo resolves relative paths in its own `addons_path` against this working directory. By contrast, MCP's optional `addons_path` override is resolved relative to the TOML file (or workspace when absent), then passed to Odoo as absolute paths.

Discovery checks the workspace and its ancestors up to the nearest Git root, at most four directories, and never the home or filesystem root. It looks for `.venv/bin/python` or `venv/bin/python` (or Windows equivalents), `odoo-bin` or `odoo/odoo-bin`, and `odoo.conf` or `config/odoo.conf`. For each setting it selects the first existing candidate, starting at the workspace and checking names in the order listed; it does not detect multiple candidates as ambiguous. If database is not set explicitly, it reads a single `db_name` from `[options]` in the resolved Odoo config. Missing or invalid values, including multiple database names, prevent execution; discovery never runs commands from configuration or `AGENTS.md`. `odoo_runtime_status` reports resolved values, the source of each value, the relative path base, validation errors, and `ready`. It returns partial results when configuration is invalid. This validates paths, not database connectivity or Python/Odoo compatibility.

`odoo_runtime_status` also reports the working directory and its source, plus `config_addons_path` with the raw config value, each resolved path, whether it exists, and whether MCP's addon override takes precedence. Missing config addon directories are warnings, not execution errors; warnings identify whether the config paths are effective or overridden. If effective config paths are relative and the working directory defaults to the workspace, status warns that another working directory may be needed. When the config omits `addons_path`, Odoo may supply its own default paths; these are not inferred by this status check.

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

Add `"commit": true` to explicitly persist the transaction. `commit` requires `allow_mutation: true`. Calls time out after 30 seconds by default; `timeout` can be set from 1 to 120 seconds. Errors are returned as structured results, and oversized results are rejected. Shell failures include a best-effort stage classification (`configuration`, `database_connection`, `odoo_startup`, or `expression`); startup classifications are heuristics, not proof of the cause. Startup stderr is withheld by default. Setting `include_stderr: true` returns a bounded excerpt with best-effort redaction, which **can still contain sensitive values** from addon logs. Configure Odoo's logfile for full diagnostics. If a committed call times out or the process fails after committing, its final database state may be unknown; inspect the database before retrying.

Odoo shell runs as the Odoo superuser, bypassing normal user access rules. Default read-only calls restrict expression syntax, set PostgreSQL's default transaction mode to read-only, and block cursor commits during expression evaluation; uncommitted changes are rolled back. Odoo's [shell documentation](https://www.odoo.com/documentation/16.0/developer/cli.html#shell) describes its transaction behavior. These safeguards reduce accidental changes, but **are not a security sandbox**: trusted addon code and explicitly enabled expressions can perform external side effects or use other connections. A read-only PostgreSQL role gives stronger database protection. Database values and error messages are returned to the MCP client; avoid sensitive data and use only trusted runtimes.

### Diagnose a model

```json
{"workspace": "/absolute/path/to/project", "model": "eproc.request"}
```

Call `diagnose_model` with these arguments to combine static model definitions, addon manifests and possible Python import chains with the selected shell's registry, `ir.model`, relevant metadata external IDs and module states. Module names come from source candidates and metadata external IDs; they are not a complete list of every addon that might affect the model. A missing import chain is a candidate for investigation, not proof: conditional and dynamic imports, duplicate addon names, and Odoo load order can change the result. Source and runtime failures are reported independently. When runtime inspection fails, registry and metadata remain unknown; discrepancies suggest possible causes without identifying a root cause.

### Inspect access for a user

```json
{"workspace": "/absolute/path/to/project", "model": "sale.order", "user_id": 7, "company_ids": [2], "record_ids": [42]}
```

Call `inspect_model_access` to inspect ACLs for `read`, `create`, `write`, and `unlink`, plus record rules for `read`, `write`, and `unlink` on up to 20 existing record IDs. `company_ids` is optional; if omitted, the user's default company is used. The first selected company is the current company, and every selected company must belong to the user. Output explicitly reports the user, superuser mode, current company and allowed companies. User 1 remains superuser under Odoo's `with_user` behavior. A missing record has no rule result; ACL and record-rule results are separate and both matter. The rule check uses Odoo's internal rule-domain computation and can vary with Odoo versions or custom overrides. Create rules, field-level permissions, contextual UI behavior and custom business logic are not simulated. See [Odoo ORM environments](https://www.odoo.com/documentation/16.0/developer/reference/backend/orm.html#altering-the-environment) and [security rules](https://www.odoo.com/documentation/16.0/developer/reference/backend/security.html).

Set `include_evidence: true` to list up to 50 active ACLs and record rules for the model, including IDs, operations, groups, domains and whether groups match the selected user. These are potentially relevant rules, not definitive explanations for a particular access decision. Evidence is read as superuser and may contain sensitive domains.

### Inspect effective fields

Use `inspect_runtime_fields` when source definitions differ from the registry, for example:

```json
{"workspace":"/absolute/path/to/project", "model":"sale.order", "fields":["state", "partner_id"], "user_id":7, "company_ids":[2], "include_source":true}
```

The tool reads up to 20 named fields via `fields_get`, using the selected user and company, and reports database, user, company and superuser context. Omit `user_id` to use the shell user (normally superuser); omit `company_ids` for that user's default company. The result requests common `fields_get` attributes; attributes absent from the runtime response are not inferred. `not_returned` can mean missing or inaccessible. Optional source definitions are static candidates, limited per field; source indexing failures are reported separately. Runtime checks use the same read-only safeguards and limitations as shell diagnostics.

## Source workspace discovery

For source indexing, the server looks at the workspace, its immediate children, `odoo/addons`, and ancestors up to the nearest Git root, or at most three ancestors without a Git root. Source discovery does not read Odoo configuration or search the whole filesystem. If no unambiguous workspace is available, it returns an error instead of silently scanning another project. Check `source_status` for selected roots and parse errors. Shell runtime discovery is described in [Odoo shell setup](#odoo-shell-setup).

When no `workspace` argument is provided, the server uses `ODOO_ADDONS_PATH`, then `ODOO_SOURCE_ROOT`, then a single local MCP root supplied by the client. `ODOO_ADDONS_PATH` is an OS-path-separated list of addon **containers** (directories containing module folders); each entry must be an existing directory, though it can be empty. `ODOO_SOURCE_ROOT` is a project root to discover from. An explicit workspace takes precedence over these environment variables, so the bundled skill's workspace selection normally takes priority. For nonstandard layouts, ask the agent to select a suitable workspace, or configure the fallback for clients that omit it.

Discovery and indexing skip symlinked addon modules and manifests. A container that is itself a symlink can be selected explicitly as a workspace or through `ODOO_ADDONS_PATH`.

## Compatibility and limitations

The source index targets common Odoo 16–18 addon patterns, including [ORM inheritance](https://www.odoo.com/documentation/16.0/developer/reference/backend/orm.html), [view inheritance](https://www.odoo.com/documentation/16.0/developer/reference/backend/views.html), [actions](https://www.odoo.com/documentation/16.0/developer/reference/backend/actions.html), and [XML data](https://www.odoo.com/documentation/16.0/developer/reference/backend/data.html). It supports field references in Odoo 17 `tree` and Odoo 18 `list` view roots but does not validate version-specific view syntax. Runtime shell, model diagnosis, and access inspection have been tested with a simulated shell, not against actual Odoo 16, 17, or 18 databases.

Source index results are static candidates, not proof of installed modules, final registry or views, access rules, or runtime behavior. The index can include files not imported or listed in a manifest; duplicate module names are not resolved in Odoo load order. Dynamic declarations and some XML references may be missed. Fields from inherited models are not resolved automatically, and nested view fields may belong to another model. Tests, static assets and hidden directories are excluded. Inspect the original source and use Odoo runtime checks when needed.

Only use trusted addon roots: source metadata is sent to the connected MCP client.

## Development

From this directory:

```sh
uv sync --python 3.11
uv run python -m unittest discover -s tests -v
```

The plugin requires Python 3.11 or newer, independently of the Python version used by Odoo. `uv` can provision it automatically.
