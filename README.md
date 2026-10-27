# Zad Plugins

This repository is a Codex plugin marketplace.

## Plugins

| Plugin | Description |
| --- | --- |
| [odoo-source-mcp](plugins/odoo-source-mcp/README.md) | Navigate Odoo addon source, including models, fields, menus, and XML IDs. |

## Repository layout

```text
.agents/plugins/marketplace.json  Marketplace catalog
plugins/
  odoo-source-mcp/               Odoo Source Explorer plugin
    README.md                    Plugin documentation
    plugin.json                  Plugin metadata
    mcp.json                     MCP server configuration
    skills/odoo-source-mcp/      Bundled skill
    src/odoo_source_mcp/         MCP server implementation
    tests/                       Plugin tests
    pyproject.toml               Python package configuration
    uv.lock                      Locked dependencies
```

Add each new plugin under `plugins/<plugin-name>/` and register it in the marketplace catalog. Put skills belonging to a plugin in that plugin's `skills/` directory.

## Install in Codex

Once the repository has been pushed to GitHub, add the marketplace and install the plugin. You need [uv](https://docs.astral.sh/uv/) on `PATH` and access to the GitHub repository:

```sh
codex plugin marketplace add rafi-zadanly/zad-plugins
codex plugin add odoo-source-mcp@zad-plugins
```

Start a new Codex session after installation. The marketplace adds the plugin package; the `uv run` launcher automatically creates its Python environment and installs the locked dependencies on first use. No separate `uv sync`, MCP registration, database connection, or Odoo credentials are needed. The first launch needs network access to fetch dependencies and, if necessary, Python 3.11; subsequent launches can use the local uv cache and environment. `uv` must already be installed and discoverable in the Codex process's `PATH`. Availability of plugin installation depends on the Codex client and its configuration. Alternatively select the marketplace and install the plugin in the ChatGPT desktop app's Plugins Directory.

To use a local checkout instead of the GitHub source, add the repository root as the marketplace:

```sh
codex plugin marketplace add /absolute/path/to/zad-plugins
codex plugin add odoo-source-mcp@zad-plugins
```

This repository is a Git marketplace, not the public plugin directory. See [OpenAI's plugin packaging guide](https://developers.openai.com/plugins/build/plugins).
