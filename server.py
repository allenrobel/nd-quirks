"""
server.py — an MCP server exposing Obsidian notes about bugs and deviations.

Original use case: track ND API bugs and workarounds in an Obsidian vault, then query them from
Claude Code when writing prompts or debugging issues. But the pattern is general: if
you have a collection of markdown notes with structured frontmatter, this server can expose
them as an MCP tool for querying and retrieval.

The vault is read directly from disk (no Local REST API plugin needed). Obsidian's
only job on the host is to run Obsidian Sync, which keeps the folder current. Files
are cached by mtime, so edits synced from your other devices are picked up
automatically without a restart.

Frontmatter is optional. If you add it, this convention unlocks the best tooling:

    ---
    endpoints:
      - /api/v1/infra/...
      - /sedgeapi/v1/...
    tags: [deviation, bug]
    status: open          # open | workaround | fixed
    found: 4.2.1
    fixed: 4.3.0
    severity: high
    ---

Run:
    pip install fastmcp python-frontmatter
    export OBSIDIAN_VAULT_PATH="/Users/allen/ObsidianVaults/Bugs"
    python server.py
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter
from fastmcp import FastMCP

# --- Configuration ----------------------------------------------------------

VAULT_PATH = Path(
    os.environ.get("OBSIDIAN_VAULT_PATH", "~/Obsidian/ND")
).expanduser()

# Directories and files we never want to surface in results.
# "Templates" holds Obsidian note templates (e.g. FrontMatterTemplate),
# not actual bug notes.
EXCLUDED_DIRS = {".obsidian", ".trash", ".git", "Templates"}
# Obsidian Sync creates "*.sync-conflict-*.md" files; keep them out of results.
EXCLUDED_SUFFIXES = (".sync-conflict",)

mcp = FastMCP("bug-tracker-mcp")


# --- Note model + mtime cache ----------------------------------------------


@dataclass
class Note:
    path: Path
    name: str  # vault-relative path without extension, e.g. "infra/syslog-bug"
    meta: dict
    body: str
    mtime: float = field(repr=False, default=0.0)

    @property
    def endpoints(self) -> list[str]:
        """Normalized endpoints declared in frontmatter (endpoint or endpoints)."""
        raw = self.meta.get("endpoints") or self.meta.get("endpoint") or []
        if isinstance(raw, str):
            raw = [raw]
        return [str(e).strip().lower() for e in raw if str(e).strip()]

    @property
    def tags(self) -> list[str]:
        raw = self.meta.get("tags") or []
        if isinstance(raw, str):
            raw = [raw]
        return [str(t).strip().lower() for t in raw]

    @property
    def found_version(self) -> tuple[int, ...] | None:
        """Parsed `found` version, or None if absent/unparseable (unknown origin)."""
        return _parse_version(self.meta.get("found"))

    @property
    def fixed_version(self) -> tuple[int, ...] | None:
        """Parsed `fixed` version, or None if absent/empty (never fixed)."""
        return _parse_version(self.meta.get("fixed"))

    def affects_version(self, target: tuple[int, ...]) -> bool:
        """Whether this bug is present in the given ND version.

        Affects `target` when it was found at or before `target` (or its origin
        is unknown) and has not yet been fixed as of `target`.
        """
        found = self.found_version
        fixed = self.fixed_version
        if found is not None and _vcmp(target, found) < 0:
            return False  # bug first appeared after the target version
        if fixed is not None and _vcmp(target, fixed) >= 0:
            return False  # already fixed by the target version
        return True


_cache: dict[Path, Note] = {}


def _is_relevant(p: Path) -> bool:
    if p.suffix != ".md":
        return False
    if any(part in EXCLUDED_DIRS for part in p.parts):
        return False
    if any(suf in p.stem for suf in EXCLUDED_SUFFIXES):
        return False
    return True


def _load_note(p: Path) -> Note:
    """Load a note, reusing the cache when the file is unchanged."""
    mtime = p.stat().st_mtime
    cached = _cache.get(p)
    if cached and cached.mtime == mtime:
        return cached

    post = frontmatter.load(p)
    note = Note(
        path=p,
        name=str(p.relative_to(VAULT_PATH).with_suffix("")),
        meta=dict(post.metadata),
        body=post.content,
        mtime=mtime,
    )
    _cache[p] = note
    return note


def _all_notes() -> list[Note]:
    if not VAULT_PATH.is_dir():
        raise FileNotFoundError(
            f"Vault not found at {VAULT_PATH!s}. "
            "Set OBSIDIAN_VAULT_PATH to your vault's folder."
        )
    notes = [_load_note(p) for p in VAULT_PATH.rglob("*.md") if _is_relevant(p)]
    # Drop cache entries for files that were deleted/renamed in a sync.
    live = {n.path for n in notes}
    for stale in set(_cache) - live:
        del _cache[stale]
    return notes


def _snippet(body: str, needle: str, width: int = 200) -> str:
    """Return a short context window around the first match of `needle`."""
    idx = body.lower().find(needle.lower())
    if idx == -1:
        return body[:width].strip()
    start = max(0, idx - width // 2)
    end = min(len(body), idx + len(needle) + width // 2)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(body) else ""
    return f"{prefix}{body[start:end].strip()}{suffix}"


def _first_term_in(body: str, terms: list[str]) -> str:
    """The first query term that appears in `body`, for anchoring a snippet.

    Falls back to the first term so a no-hit note still yields a leading excerpt.
    """
    low = body.lower()
    for t in terms:
        if t in low:
            return t
    return terms[0]


# --- Version comparison -----------------------------------------------------


def _parse_version(value) -> tuple[int, ...] | None:
    """Parse a 'major.minor.patch' string into a comparable tuple.

    Returns None for empty/missing values (e.g. an unfixed bug's `fixed:`) or
    anything non-numeric. Tolerant of 2- or 4-part strings even though the
    convention is 3-part.
    """
    s = str(value or "").strip()
    if not s:
        return None
    try:
        return tuple(int(p) for p in s.split("."))
    except ValueError:
        return None


def _vcmp(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    """Compare two parsed versions, returning -1, 0, or 1.

    Pads the shorter tuple with zeros so 4.3 and 4.3.0 compare equal.
    """
    n = max(len(a), len(b))
    a += (0,) * (n - len(a))
    b += (0,) * (n - len(b))
    return (a > b) - (a < b)


# --- Tools ------------------------------------------------------------------


@mcp.tool()
def list_bugs() -> list[dict]:
    """List every bug note in the vault with its metadata.

    Use this to see what ND API issues have been documented before diving in.
    """
    notes = sorted(_all_notes(), key=lambda n: n.name)
    return [
        {
            "name": n.name,
            "endpoints": n.endpoints,
            "tags": n.tags,
            "status": n.meta.get("status"),
            "severity": n.meta.get("severity"),
            "found": n.meta.get("found"),
            "fixed": n.meta.get("fixed"),
        }
        for n in notes
    ]


@mcp.tool()
def search_bugs(query: str, max_results: int = 10) -> list[dict]:
    """Full-text search across bug notes (title, tags, and body).

    The query is split into words and each is scored independently (name×5,
    tags×3, body×1), so "ghost groups" matches a note mentioning either word. A
    note containing the full phrase contiguously gets an extra boost, keeping
    exact-phrase matches ranked highest. Returns matches ranked by relevance,
    each with a short snippet. Use this for free-text questions like "syslog
    server validation" or "pagination off-by-one".
    """
    q = query.strip().lower()
    terms = q.split()
    if not terms:
        return []

    scored: list[tuple[int, Note]] = []
    for n in _all_notes():
        name = n.name.lower()
        body = n.body.lower()
        score = 0
        for term in terms:
            score += name.count(term) * 5
            score += sum(t.count(term) for t in n.tags) * 3
            score += body.count(term)
        # Phrase bonus: reward a contiguous match of the full multi-word query
        # so exact-phrase hits still outrank scattered single-word hits.
        if len(terms) > 1:
            score += name.count(q) * 5
            score += sum(t.count(q) for t in n.tags) * 3
            score += body.count(q)
        if score:
            scored.append((score, n))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [
        {
            "name": n.name,
            "score": score,
            "status": n.meta.get("status"),
            "endpoints": n.endpoints,
            "found": n.meta.get("found"),
            "fixed": n.meta.get("fixed"),
            "snippet": _snippet(n.body, _first_term_in(n.body, terms)),
        }
        for score, n in scored[:max_results]
    ]


@mcp.tool()
def find_bugs_for_endpoint(endpoint: str, version: str | None = None) -> list[dict]:
    """Find bug notes relevant to a specific ND API endpoint or path.

    Pair this with the ND schema server: after resolving an endpoint there, call
    this to surface any documented deviations or bugs. Matches against the
    `endpoints` frontmatter first, then falls back to a body search.

    Pass `version` (e.g. "4.2.1") to keep only bugs present in that ND release
    — found at or before it and not yet fixed. Bugs with no recorded `found`
    are kept and flagged `origin: "unknown"`.
    """
    ep = endpoint.strip().lower()
    if not ep:
        return []

    target = _parse_version(version) if version else None
    if version and target is None:
        raise ValueError(
            f"Could not parse version {version!r}; expected major.minor.patch, e.g. 4.2.1."
        )

    results = []
    for n in _all_notes():
        if target is not None and not n.affects_version(target):
            continue
        # Forgiving containment match in both directions for path fragments.
        meta_hit = any(ep in e or e in ep for e in n.endpoints)
        body_hit = ep in n.body.lower()
        if meta_hit or body_hit:
            results.append(
                {
                    "name": n.name,
                    "matched_on": "frontmatter" if meta_hit else "body",
                    "status": n.meta.get("status"),
                    "severity": n.meta.get("severity"),
                    "found": n.meta.get("found"),
                    "fixed": n.meta.get("fixed"),
                    "origin": "unknown" if n.found_version is None else "known",
                    "snippet": _snippet(n.body, endpoint),
                }
            )
    # Frontmatter matches are more trustworthy than incidental body mentions.
    results.sort(key=lambda r: r["matched_on"] != "frontmatter")
    return results


@mcp.tool()
def find_bugs_for_version(version: str) -> list[dict]:
    """List every bug present in a given ND version (e.g. "4.2.1").

    Answers "what known issues apply to the release I'm running?" A bug is
    included when it was found at or before `version` and has not yet been fixed
    as of `version`. Bugs with no recorded `found` are included and flagged
    `origin: "unknown"`.
    """
    target = _parse_version(version)
    if target is None:
        raise ValueError(
            f"Could not parse version {version!r}; expected major.minor.patch, e.g. 4.2.1."
        )

    notes = sorted(_all_notes(), key=lambda n: n.name)
    return [
        {
            "name": n.name,
            "endpoints": n.endpoints,
            "status": n.meta.get("status"),
            "severity": n.meta.get("severity"),
            "found": n.meta.get("found"),
            "fixed": n.meta.get("fixed"),
            "origin": "unknown" if n.found_version is None else "known",
        }
        for n in notes
        if n.affects_version(target)
    ]


@mcp.tool()
def get_bug(name: str) -> dict:
    """Return the full content and metadata of a single bug note by name.

    `name` is the vault-relative path without extension, e.g. "infra/syslog-bug"
    (as returned by list_bugs or search_bugs).
    """
    target = (VAULT_PATH / name).with_suffix(".md")
    if not target.is_file() or not _is_relevant(target):
        raise FileNotFoundError(f"No bug note named {name!r}.")
    n = _load_note(target)
    return {"name": n.name, "meta": n.meta, "content": n.body}


if __name__ == "__main__":
    # Bind to all interfaces so Claude Code on other machines can reach it via
    # the mm1e hostname, matching your context7 / nd-openapi servers. The /mcp
    # path mirrors those entries' URLs.
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8001, path="/mcp")

