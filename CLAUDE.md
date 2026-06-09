# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An MCP server (`server.py`) that exposes an Obsidian vault of notes documenting
**Nexus Dashboard (ND) API bugs** — deviations, undocumented behavior, and
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

Everything lives in `server.py` (~380 lines). Five FastMCP tools, one data
model, and an mtime-based cache:

- **`Note` dataclass** — wraps one `.md` file. Its `endpoints` and `tags`
  properties normalize frontmatter that may be a string *or* a list, and accept
  either `endpoint`/`endpoints` keys. All endpoint/tag matching is lowercased.
  `bug_id` (the stable `id` slug) and `guidance` (a one-line takeaway) return
  `None` when the frontmatter key is absent or empty.
- **`_load_note` / `_all_notes` + `_cache`** — notes are cached by `mtime`. A
  changed file is re-parsed; a deleted/renamed file (common after a sync) is
  evicted by diffing the live file set against `_cache` keys on each
  `_all_notes()` call. This is the mechanism that makes synced edits appear
  without a restart.
- **`_is_relevant`** — the single gatekeeper for which files surface: `.md` only,
  excluding `.obsidian`/`.trash`/`.git`/`Templates` dirs, Obsidian Sync's
  `*.sync-conflict-*.md` files, and project-tooling filenames in
  `EXCLUDED_FILENAMES` (`CLAUDE.md`/`README.md`/`MEMORY.md`/`AGENTS.md`, matched
  case-insensitively) — these guide agents working in the vault but are never bug
  notes, and would otherwise show up as fieldless entries in `list_bugs`. Any new
  tool must funnel file selection through this (or `_all_notes`), not raw `rglob`.
- **Version logic** — `_parse_version` turns `major.minor.patch` strings into
  comparable int tuples (`None` if empty/unparseable); `_vcmp` zero-pads before
  comparing so `4.3` == `4.3.0`. `Note.affects_version(target)` decides whether a
  bug is present in a release: true when `found is None` (unknown origin) or
  `target >= found`, **and** `fixed is None`/empty (never fixed) or `target < fixed`.

The six tools: `list_bugs` (inventory), `search_bugs` (per-word ranked
full-text — see *Search scoring* below), `find_bugs_for_endpoint` (frontmatter
`endpoints` match first, body fallback — note the *forgiving bidirectional
containment* match `ep in e or e in ep` for path fragments — plus an optional
`version` arg that filters to bugs present in that release),
`find_bugs_for_version` (every bug present in a given ND release), `get_bug`
(full content by vault-relative name without extension, e.g. `infra/syslog-bug`),
and `get_bug_by_id` (full content by the stable `id` slug — scans `_all_notes`
for a case-insensitive match; use it to resolve `# workaround: <id>` back-links
that survive file renames). The two version-aware paths raise `ValueError` on a
malformed version and flag empty-`found` bugs with `origin: "unknown"`. Every
list/search result now carries `id` and `guidance` so the actionable takeaway
rides along without a follow-up `get_bug`.

### Search scoring

`search_bugs` lowercases the query and splits it on whitespace into terms.
Each term is scored independently and the scores are **summed** (OR semantics —
a note matches if *any* term hits), with per-field weights **name ×5, tags ×3,
body ×1** counting every occurrence. So `"ghost groups"` matches a note
mentioning either word, not only the contiguous phrase. For multi-word queries,
a **contiguous-phrase bonus** re-applies those same weights to the full query
string, so an exact-phrase hit outranks scattered single-word hits. A
single-word query reduces to one term with no bonus — identical to the original
behavior. Notes with a zero total score are dropped; results sort by score
descending and truncate to `max_results`. Snippets anchor on the first term that
actually appears in the body (`_first_term_in`), not the full phrase.

## Note frontmatter convention

Frontmatter is optional, but this convention unlocks the tooling
(`find_bugs_for_endpoint` ranking, version filtering, status/severity in
listings):

```yaml
---
id: syslog-server-validation   # stable kebab-case slug; key for get_bug_by_id and back-links
endpoints:
  - /api/v1/infra/...
tags: [deviation, bug]
status: open          # open | workaround | fixed
found: 4.2.1          # ND release the bug was found in; always major.minor.patch
fixed: 4.3.0          # release it was fixed in; leave empty if still present
severity: high
guidance: "..."       # one-line actionable takeaway, echoed in every list/search result
---
```

`found`/`fixed` drive the version-aware tools (see Architecture). `id` and
`guidance` are surfaced in every listing and `id` backs `get_bug_by_id`; both are
optional and absent/empty values read as `None`. `fixed` is
commonly an **empty string** — that means "not yet fixed", not "unknown", and is
distinct from an empty `found` (unknown origin).

## Deployment

Runs as a macOS LaunchAgent (`com.nd-bugs-mcp.plist`) bound to `0.0.0.0:8001`
at path `/mcp` over streamable-HTTP, so Claude Code on other machines can reach
it by hostname. The plist hardcodes absolute paths to `.venv/bin/uv`,
`server.py`, and `OBSIDIAN_VAULT_PATH` — these must be edited per-host. See
`README.md` for the full install + client-config steps.
