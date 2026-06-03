# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An MCP server (`server.py`) that exposes an Obsidian vault of notes documenting
**Nexus Dashboard (ND) API quirks** — deviations, undocumented behavior, and
unfixed bugs. It is designed to run *alongside* the `nd-openapi` schema MCP
server: a client resolves an endpoint against the schema server, then asks this
server for any known deviations on that path.

The vault is read **directly from disk** — there is no Obsidian Local REST API
plugin involved. Obsidian's only role on the host is to run Obsidian Sync, which
keeps the vault folder current. Edits synced from other devices are picked up
automatically (see caching below); no server restart needed.

## Commands

```bash
# Run the server locally (reads OBSIDIAN_VAULT_PATH, defaults to ~/Obsidian/ND)
uv run server.py

# Type check (mypy is configured via .mypy_cache presence; run explicitly)
uv run mypy server.py

# Install/sync dependencies
uv sync
```

Requires Python >= 3.14. There is no test suite.

## Architecture

Everything lives in `server.py` (~250 lines). Four FastMCP tools, one data
model, and an mtime-based cache:

- **`Note` dataclass** — wraps one `.md` file. Its `endpoints` and `tags`
  properties normalize frontmatter that may be a string *or* a list, and accept
  either `endpoint`/`endpoints` keys. All endpoint/tag matching is lowercased.
- **`_load_note` / `_all_notes` + `_cache`** — notes are cached by `mtime`. A
  changed file is re-parsed; a deleted/renamed file (common after a sync) is
  evicted by diffing the live file set against `_cache` keys on each
  `_all_notes()` call. This is the mechanism that makes synced edits appear
  without a restart.
- **`_is_relevant`** — the single gatekeeper for which files surface: `.md` only,
  excluding `.obsidian`/`.trash`/`.git` dirs and Obsidian Sync's
  `*.sync-conflict-*.md` files. Any new tool must funnel file selection through
  this (or `_all_notes`), not raw `rglob`.

The four tools: `list_quirks` (inventory), `search_quirks` (weighted full-text:
name×5, tags×3, body×1), `find_quirks_for_endpoint` (frontmatter `endpoints`
match first, body fallback — note the *forgiving bidirectional containment*
match `ep in e or e in ep` for path fragments), and `get_quirk` (full content by
vault-relative name without extension, e.g. `infra/syslog-quirk`).

## Note frontmatter convention

Frontmatter is optional, but this convention unlocks the tooling
(`find_quirks_for_endpoint` ranking, status/severity in listings):

```yaml
---
endpoints:
  - /api/v1/infra/...
tags: [deviation, bug]
status: open          # open | workaround | fixed
found: 4.2.1
fixed: 4.3.0
severity: high
---
```

## Deployment

Runs as a macOS LaunchAgent (`com.nd-quirks-mcp.plist`) bound to `0.0.0.0:8001`
at path `/mcp` over streamable-HTTP, so Claude Code on other machines can reach
it by hostname. The plist hardcodes absolute paths to `.venv/bin/uv`,
`server.py`, and `OBSIDIAN_VAULT_PATH` — these must be edited per-host. See
`README.md` for the full install + client-config steps.
