# Zad Plugins

This repository provides plugin marketplace catalogs for Codex, Claude Code, and Cursor. Its MCP servers can also be configured directly in other MCP clients.

## Plugins

| Plugin | Description |
| --- | --- |
| [odoo-source-mcp](plugins/odoo-source-mcp/README.md) | Navigate Odoo addon source and optionally query a configured Odoo runtime. |

## Repository layout

```text
.agents/plugins/marketplace.json  Codex marketplace catalog
.claude-plugin/marketplace.json   Claude Code marketplace catalog
.cursor-plugin/marketplace.json   Cursor marketplace catalog
plugins/
  odoo-source-mcp/               Odoo Source Explorer plugin
    README.md                    Plugin documentation
    plugin.json                  Codex plugin metadata
    mcp.json                     Codex MCP server configuration
    .claude-plugin/plugin.json   Claude Code plugin metadata and MCP server
    .cursor-plugin/plugin.json   Cursor plugin metadata and MCP server
    .kimi-plugin/plugin.json     Kimi Code plugin metadata and MCP server
    skills/odoo-source-mcp/      Bundled skill
    src/odoo_source_mcp/         MCP server implementation
    tests/                       Plugin tests
    pyproject.toml               Python package configuration
    uv.lock                      Locked dependencies
```

Add each new plugin under `plugins/<plugin-name>/` and register it in the marketplace catalogs for clients it supports. Put skills belonging to a plugin in that plugin's `skills/` directory.

## Client support

| Client | Installation | Bundled skill |
| --- | --- | --- |
| Codex | Marketplace plugin | Included in plugin |
| Claude Code | Marketplace plugin | Included in plugin |
| Cursor | Cursor marketplace catalog; direct MCP configuration also available | Included in plugin; direct MCP registration does not install it |
| Kimi Code | Local plugin installation | Declared in plugin |
| Kiro | Portable Agent Plugins power | Included in package |
| Cline | Portable Agent Plugins on supported Hub clients, or direct MCP configuration | Included in portable package; direct registration does not install it |
| OMP | Claude-compatible marketplace fallback, or direct MCP configuration | Included in portable package |
| VS Code / GitHub Copilot | Configure the MCP server directly | Not installed by MCP registration |

See the [Odoo plugin's client setup](plugins/odoo-source-mcp/README.md#other-mcp-clients) for configuration paths, prerequisites, and examples. Client support for MCP does not imply support for this repository's marketplace or plugin skill. Direct configurations require a local checkout and `uv`; they do not install the server from the marketplace.

Cursor's catalog and plugin manifest follow its [plugin reference](https://cursor.com/docs/reference/plugins); Cursor uses `CURSOR_PLUGIN_ROOT` rather than the portable manifest's `PLUGIN_ROOT`. Local or team marketplace availability depends on the Cursor account and settings; this package has not been submitted to the public Cursor Marketplace or tested in Cursor.

## Install in Codex

Once the repository has been pushed to GitHub, add the marketplace and install the plugin. You need [uv](https://docs.astral.sh/uv/) on `PATH` and access to the GitHub repository:

```sh
codex plugin marketplace add rafi-zadanly/zad-plugins
codex plugin add odoo-source-mcp@zad-plugins
```

Start a new Codex session after installation. The marketplace adds the plugin package; the `uv run` launcher automatically creates its Python environment and installs the locked dependencies on first use. Source navigation needs no separate `uv sync`, MCP registration, database connection, or Odoo credentials. Optional Odoo shell access requires the [project runtime setup](plugins/odoo-source-mcp/README.md#odoo-shell-setup). The first launch needs network access to fetch dependencies and, if necessary, Python 3.11; subsequent launches can use the local uv cache and environment. `uv` must already be installed and discoverable in the Codex process's `PATH`. Availability of plugin installation depends on the Codex client and its configuration. Alternatively select the marketplace and install the plugin in the ChatGPT desktop app's Plugins Directory.

To use a local checkout instead of the GitHub source, add the repository root as the marketplace:

```sh
codex plugin marketplace add /absolute/path/to/zad-plugins
codex plugin add odoo-source-mcp@zad-plugins
```

## Install in Claude Code

You need [uv](https://docs.astral.sh/uv/) on `PATH`. In Claude Code:

```text
/plugin marketplace add rafi-zadanly/zad-plugins
/plugin install odoo-source-mcp@zad-plugins
```

Or from a shell:

```sh
claude plugin marketplace add rafi-zadanly/zad-plugins
claude plugin install odoo-source-mcp@zad-plugins
```

Restart Claude Code after installation. As with Codex, `uv run` provisions Python and the locked dependencies on first launch. For a local checkout, pass the absolute repository path to `marketplace add` instead.

This repository is a Git marketplace, not the public plugin directory. See [OpenAI's plugin packaging guide](https://developers.openai.com/plugins/build/plugins).
