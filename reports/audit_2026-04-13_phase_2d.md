---
name: Audit 2026-04-13 — Phase 2d cross-project /start search integration
description: Two rounds of GPT-5.4 codereview on build_start_context + _category_from_scope + /start rule prose; round 1 caught 1 HIGH (greedy walk + raw $(pwd)) + 2 MED (no normalization, init_db on read command) + 1 LOW (empty title); round 2 caught 1 MED (contract-vs-reality: sqlite3.connect creates empty file) + 1 LOW (os.getcwd crashes on deleted cwd); all 6 fixed in-session
type: audit
scope: global
preserve: true
---

# Audit 2026-04-13 — Phase 2d

## Context

Phase 2d wires the 22 consilium/audit rows that Phase 2a indexed into the
actual `/start` workflow. Before this change, `/start` used
`Glob("reports/consilium_*.md") + Read`, which sees only the current
project's reports and misses cross-project knowledge entirely — a horizon
IBKR audit was invisible from inside Claude_Booster, an AINEWS DNS split
consilium was invisible from inside Mikrotik, etc.

New pieces landed:

- `rolling_memory.py:_category_from_scope(scope)` — derive the
  `index_reports._project_category` mirror from a scope path via walk-up to
  the first ancestor that owns a `reports/` or `audits/` directory.
- `rolling_memory.py:build_start_context(scope, query, limit)` — category-
  biased SQL over consilium/audit rows, optional FTS5 join, distinct error
  messages for FTS5 syntax errors vs missing-table vs other failures. Uses
  a strictly read-only SQLite connection (`file:...?mode=ro` URI).
- `rolling_memory.py:get_readonly_connection()` — new helper that opens
  `DB_PATH` via URI mode=ro so missing DB files are not created as a side
  effect of the connect call.
- `rolling_memory.py` CLI: new `start-context --scope --query --limit`
  subcommand, exempted from the unconditional `init_db()` dispatch gate so
  the read-only contract holds end-to-end.
- `~/.claude/rules/commands.md` `/start` step 2 rewritten to call
  `rolling_memory.py start-context --scope "$(git rev-parse --show-toplevel
  2>/dev/null || pwd)"` with an optional `--query` for topic-driven search
  and a self-heal path to `index_reports.py` if the DB is empty.

Two GPT-5.4 codereview rounds followed. All 6 findings were fixed + re-
verified in-session. This report documents the round table, the fixes, and
the institutional lessons.

## Round 1 — GPT-5.4 findings

Model: `gpt-5.4`, `thinking_mode=high`, `review_type=full`.

| # | Severity | Location | Issue |
|---|---|---|---|
| R1-HIGH | HIGH | `commands.md` /start step 2 + `rolling_memory.py:_category_from_scope` | `/start` passed raw `$(pwd)` as scope, which produced wrong category bias when Claude was launched from a subdirectory. Worse, `_category_from_scope` used a greedy descent past `Projects/<top>/` and returned the deepest non-marker segment — so `~/Projects/horizon/src` resolved to `'src'` instead of `'horizon'`, completely breaking the bias that Phase 2d was supposed to provide. Also: raw `$(pwd)` breaks shell word-splitting on paths with spaces. |
| R1-MED | MED | `rolling_memory.py:_category_from_scope` | No path normalization or anchoring. For paths outside `~/Projects` the helper returned the basename, producing false positives if an indexed row happened to share the basename (e.g., `/var/foo` vs a hypothetical `~/Projects/foo/reports/`). |
| R1-MED | MED | `rolling_memory.py:_cli` dispatch | `start-context` rule prose promised "no DB writes" but the CLI dispatcher called `init_db()` unconditionally before entering any command branch, so a fresh schema-v1 DB would silently migrate to v3. Same contract-violation pattern as Phase 2a R2-MED-1 (`index_reports.py --dry-run`). |
| R1-LOW | LOW | `rolling_memory.py:build_start_context` | Title extraction returned an empty string for rows with blank content, producing ugly `  * [2026-04-12] audit/horizon — ` bullets with a dangling em-dash. |

### Round 1 fixes

**R1-HIGH — merged fix in two places.**

(A) `~/.claude/rules/commands.md` `/start` step 2 now invokes:

```bash
python ~/.claude/scripts/rolling_memory.py start-context --scope "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
```

Quoted to survive paths with spaces; git-toplevel resolution first so the
scope is the project root even when Claude is launched from a subdirectory;
`pwd` fallback for non-git projects.

(B) `rolling_memory.py:_category_from_scope` rewritten from greedy descent
to ancestor walk-up. Key properties:

- `expanduser().resolve()` normalizes the input (handles symlinks too).
- `relative_to(Path.home() / "Projects")` gate anchors the scope — out-of-
  Projects paths return `None` immediately.
- Loop walks upward from the scope to the first ancestor that owns a
  `reports/` or `audits/` directory. That ancestor's basename is the
  category (mirrors `index_reports._project_category`'s semantics).
- Two exit guards: `cur == projects_root` (reached `~/Projects` without a
  hit) and `parent == cur` (reached filesystem root via `Path.parent`
  fixpoint — defensive, invariant makes it unreachable).
- `OSError` on `is_dir()` is caught so a permission-denied ancestor does
  not abort the walk.

Traced behavior for 10 representative paths including the bug case
(`~/Projects/horizon/src → 'horizon'` ✓), nested layouts
(`~/Projects/AINEWS/tnews_webapp → 'tnews_webapp'`), umbrella roots
(`~/Projects/AINEWS → 'AINEWS'`), out-of-Projects (`→ None`), and edge
cases (empty string, `/`, exact `~/Projects`, non-existent subdirs, broken
symlinks). All agree with `index_reports._project_category` for matching
report paths, verified by running both functions side-by-side.

**R1-MED (no normalization) — folded into the R1-HIGH fix above.** The
`expanduser().resolve() + relative_to(~/Projects)` gate catches out-of-
Projects scopes and returns None, eliminating the false-positive path.

**R1-MED (init_db on read command) — dispatcher gate + richer error
handler.** CLI dispatcher:

```python
args = parser.parse_args()
if args.cmd != "start-context":
    init_db()
```

`build_start_context` error handler extended to distinguish three failure
modes:

```python
except sqlite3.OperationalError as exc:
    msg = str(exc); msg_lower = msg.lower()
    if query and "fts5" in msg_lower:
        return f"=== KNOWLEDGE BASE — invalid FTS5 query {query!r}: {msg} ==="
    if "no such table" in msg_lower:
        return (
            "=== KNOWLEDGE BASE — DB not initialized. "
            "Run `python ~/.claude/scripts/index_reports.py` once to bootstrap. ==="
        )
    logger.exception(...); return ""
```

(The FTS5-syntax branch was an in-flight fix applied between R1 step 1 and
R1 step 2, not a round-1 finding per se — round 1's own feedback about the
silent-swallow was pre-empted.)

**R1-LOW — title fallback.** After the `# <name>` extraction attempt:

```python
if not title:
    title = Path(src).name if src else "(untitled report)"
```

Verified on synthetic rows: blank content → source basename, non-hash-
prefix → first line as-is, blank content + blank source → `(untitled
report)`.

## Round 2 — GPT-5.4 findings

Same model + thinking mode. Fresh continuation thread (round 1's expired
at the 3-hour mark). Self-contained brief covering all 4 round-1 fixes +
asking explicitly for: (a) bugs introduced by the fixes, (b) anything
round 1 missed in the now-narrower state, (c) interaction between the
fixes. Included my own 6-edge-case pre-exercise so GPT could focus on
what I hadn't already ruled out.

| # | Severity | Location | Issue |
|---|---|---|---|
| R2-MED | MED | `rolling_memory.py:build_start_context` still opens `get_connection()` | The "No DB writes" contract from the docstring and rule prose was only true at the SQL layer. `sqlite3.connect(str(DB_PATH))` is a stdlib call that **creates an empty 0-byte file** when the target does not exist — so a `/start` invocation against a fresh machine or restored-from-partial-backup state would physically write a file to disk, then fail the SELECT with "no such table", then return the friendly bootstrap message. Contract-vs-reality drift: the helper is "read-only" at the SQL layer but not at the filesystem layer. |
| R2-LOW | LOW | `rolling_memory.py:_cli` start-context branch | `scope = args.scope or os.getcwd()` — `os.getcwd()` raises `FileNotFoundError` when the shell's working directory was deleted out from under it (e.g., user did `rm -rf $(pwd)` in another tab). CLI would crash with an unhelpful traceback instead of gracefully falling back to no-bias scope. |

### Round 2 fixes

**R2-MED — new `get_readonly_connection()` via URI mode=ro.**

```python
def get_readonly_connection() -> sqlite3.Connection:
    """Open a strictly read-only SQLite connection via URI mode=ro.

    Unlike get_connection(), this does NOT create the DB file or parent
    directory when missing — a missing DB raises sqlite3.OperationalError:
    unable to open database file. Used by build_start_context so the
    /start lookup honours its "no DB writes" contract literally, not just
    at the SQL layer.
    """
    uri = f"file:{DB_PATH}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn
```

`build_start_context` switched to this helper with a pre-query connect-
phase handler that maps "unable to open" / "no such file" to the same
"DB not initialized" friendly message as the "no such table" path:

```python
try:
    conn = get_readonly_connection()
except sqlite3.OperationalError as exc:
    msg = str(exc).lower()
    if "unable to open" in msg or "no such file" in msg:
        return "=== KNOWLEDGE BASE — DB not initialized. Run ... ==="
    logger.exception(...); return ""
```

Verified: on a fresh `/tmp/ro_test_$$.sqlite3` path, `build_start_context`
returns the friendly message AND **no file is created** (`tmpdb.exists()`
is `False` post-call). `PRAGMA journal_mode=WAL` and
`PRAGMA synchronous=NORMAL` are NOT run on the RO connection — both are
write-side settings that would be rejected or irrelevant.

Note on scope decision: only `start-context` uses the RO connection.
`search`, `list`, `stats`, `context`, `similar` — all older read-ish
commands — still use `get_connection()` because they have existed in that
form for months with no complaints from the user and no explicit read-only
contract in rule prose. Expanding the RO treatment to them is scope creep
for Phase 2d.

**R2-LOW — guarded `os.getcwd()` default.**

```python
elif args.cmd == "start-context":
    if args.scope is not None:
        scope = args.scope
    else:
        try:
            scope = os.getcwd()
        except OSError:
            # cwd was deleted out from under the shell. Fall back to no
            # scope so _category_from_scope returns None and we still
            # produce a useful (unbiased) result instead of crashing.
            scope = None
    out = build_start_context(scope=scope, ...)
    print(out if out else "(no consilium/audit reports indexed for this scope)")
```

Verified by running the CLI from a freshly deleted cwd: the command
produces the unbiased "KNOWLEDGE BASE" listing without crashing; DB
byte-identical.

## Verification after round 2 fixes

### End-to-end (read-only across the entire suite)

```
TEST 1  Claude_Booster scope (full root)                          PASS (both Claude_Booster audits marked *)
TEST 2  Claude_Booster/reports subdirectory                       PASS (walks up to Claude_Booster)
TEST 3  horizon + --query "ibkr commission"                       PASS (audit_ibkr_execution marked *)
TEST 4  FTS5 syntax error visible                                 PASS (=== invalid FTS5 query ... ===)
R2-MED  missing DB via monkey-patched DB_PATH                     PASS (friendly msg, no file created)
R2-MED  existing DB via RO URI                                    PASS (normal read path)
R2-LOW  deleted cwd                                               PASS (graceful None scope, no crash)
REGR    consolidate --type audit ValueError                       PASS
REGR    consolidate --type directive --dry-run                    PASS (byte-identical)
REGR    index_reports --dry-run                                   PASS (byte-identical)
REGR    stats (init_db path for write-capable commands)           PASS (total_active=60)

DB sha pre  = 63cfb8b27934f42a899644390c98b8af052e95a826b9827bef17882657896e76
DB sha post = 63cfb8b27934f42a899644390c98b8af052e95a826b9827bef17882657896e76
DB sha256 byte-identical across the entire suite.
```

### `_category_from_scope` trace (10 paths)

```
'/Users/dmitrijnazarov/Projects/horizon'              -> 'horizon'
'/Users/dmitrijnazarov/Projects/horizon/src'          -> 'horizon'         ← the R1-HIGH bug case
'/Users/dmitrijnazarov/Projects/AINEWS'               -> 'AINEWS'
'/Users/dmitrijnazarov/Projects/AINEWS/tnews_webapp'  -> 'tnews_webapp'    ← nested layout
'/Users/dmitrijnazarov/Projects/AINEWS/ainews-sre-agent' -> 'ainews-sre-agent'
'/Users/dmitrijnazarov/Projects/Claude_Booster'       -> 'Claude_Booster'
'/Users/dmitrijnazarov/Projects/Claude_Booster/reports' -> 'Claude_Booster'
'/var/tmp/something'                                  -> None              ← out-of-Projects
'/'                                                   -> None
'global' / None / ''                                  -> None
```

All match `index_reports._project_category` for the corresponding report
paths, verified by running both functions side-by-side.

## Files changed (Phase 2d total)

| File | Scope | LOC delta | Summary |
|---|---|---|---|
| `~/.claude/scripts/rolling_memory.py` | outside repo | +182 −5 net | New `get_readonly_connection`, `_category_from_scope` (walk-up), `build_start_context` (4 ordering branches + 3 error modes), CLI subcommand `start-context`, `if args.cmd != "start-context": init_db()` gate |
| `~/.claude/rules/commands.md` | outside repo | +7 −2 | `/start` step 2 rewritten to call `start-context` with quoted git-toplevel scope + self-heal note |
| `reports/audit_2026-04-13_phase_2d.md` | this file | new | this report |

Both script and rule files live OUTSIDE the `Claude_Booster` git repo (in
`~/.claude/`), so the only thing this commit touches is `reports/`.

## Decisions

- **Fix R1-HIGH in BOTH places, not just rule prose.** Even with the git-
  toplevel fix in `/start` step 2, a non-git project would fall back to
  raw `pwd` and hit the greedy-walk bug again. Defense-in-depth: the
  helper itself is now robust to subdirectory scopes via ancestor walk-up.
- **Keep init_db exemption narrow to `start-context`.** Other CLI read
  commands (`search`, `list`, `stats`, `context`, `similar`) do not claim
  a read-only contract and have not triggered complaints. Expanding the
  RO treatment to them is scope creep; the R2-MED lesson is specifically
  about commands that promise dryness in rule prose.
- **Use URI `mode=ro` instead of `unlink`-after-failure.** Alternative
  was to let `sqlite3.connect()` create the empty file, catch "no such
  table", then `unlink` the empty file. Rejected: adds a risky branch
  and loses the property that a subsequent `init_db()` call bootstraps
  cleanly on top of the empty-file leftover. RO URI is the strictly
  correct primitive.
- **Don't broaden `os.getcwd()` guard to other CLI branches.** Only
  `start-context` defaults to cwd; every other subcommand requires an
  explicit `--scope` or supplies its own default (e.g., `global`).
  Narrow guard is the right scope.
- **Ship without a unit test suite.** `rolling_memory.py` has never had
  tests; bootstrapping a test rig is out of scope for Phase 2d. Verified
  manually via 11 test cases + regression gates + DB byte-identity, same
  standard as Phase 2a.

## Rejected alternatives

- **Add `category` filtering to the existing `search()`.** Rejected
  because `search()` is a generic primitive and adding report-specific
  filtering would bloat its interface. Kept `build_start_context` as a
  dedicated helper with its own SQL.
- **Rewrite the indexer to use `scope=<project path>` instead of
  `scope=global`.** Rejected because it would re-invalidate all 22
  existing rows (`idempotency_key = "report:<abspath>"` → same key but
  different scope requires a manual migration) and would duplicate the
  project-derivation logic in both writer and reader. The category-bias
  approach keeps all indexed rows under a single scope namespace.
- **Soften the "No DB writes" contract text to match the empty-file
  reality.** Rejected in favour of the RO URI fix — a literally-true
  contract beats a documented fudge, and the fix is 12 LOC.
- **Drop the `parent == cur` guard in the walk-up loop.** Rejected
  because defensive code at a loop boundary is worth the one extra line;
  the `relative_to` precondition should make it unreachable, but the
  guard prevents a future refactor from accidentally introducing an
  infinite loop.
- **Round 3 GPT-5.4 audit.** Deferred. Two rounds already caught 6
  findings and the post-R2 surface is narrow. The Phase 2a precedent
  showed 2 rounds is sufficient for small diffs.

## Institutional lessons

1. **Read-only contracts must be enforced at the filesystem layer, not
   just at the SQL layer.** A function that claims "no DB writes" but
   opens its connection via `sqlite3.connect(path)` will silently create
   an empty file on missing paths — `sqlite3.connect()` is a stdlib
   write primitive on missing DBs. Use `file:{path}?mode=ro` URIs for
   truly read-only paths.
2. **CLI defaults that call `os.getcwd()` must handle `OSError`.** The
   shell's working directory can be deleted out from under a running
   process. On Linux/macOS, `os.getcwd()` raises `FileNotFoundError` in
   that state. Any CLI that defaults a `--scope` or similar to the
   current directory must wrap the call in try/except and fall back to
   a sentinel (`None` is usually right).
3. **Path-based heuristics: walk UP to a marker, don't walk DOWN
   greedy.** Given a scope path, deriving "which project is this" by
   greedily descending from `~/Projects/<top>/` breaks the moment the
   user is in a subdirectory (`horizon/src → 'src'`, not `'horizon'`).
   The correct primitive is to walk ancestors until finding a marker
   directory (here: `reports/` or `audits/`), which mirrors how
   `git rev-parse --show-toplevel` finds the enclosing repo. Defense-
   in-depth against the caller passing a nested path.
4. **Round 2 audit is mandatory, not optional.** Phase 2a taught this
   lesson; Phase 2d confirms it. Round 1 on Phase 2d returned 4
   findings including a HIGH. Round 2 on the post-R1 state returned 2
   more (1 MED + 1 LOW) that round 1 could not have seen because they
   required the narrower post-R1 diff to be visible. The pattern holds:
   **fix commits can introduce new bugs, and a second pass on the
   narrowed surface catches things the first pass couldn't.**
5. **`/start` rule prose must quote `$(...)` expansions.** The raw form
   `--scope $(pwd)` breaks on paths containing spaces (shell word-
   splitting). The quoted form `--scope "$(git rev-parse --show-toplevel
   2>/dev/null || pwd)"` survives both spaces and the rare non-git
   project fallback. Check every shell expansion in rules/ files for
   this.
6. **Category-bias column != scope column.** `index_reports.py` stores
   reports with `scope='global'` and `category=<project>`. A naive
   "search scope=<cwd>, include_global=True" will get all global rows
   without project bias. The `CASE WHEN category = ? THEN 0 ELSE 1 END`
   ordering trick is the right primitive for project-biased retrieval
   when the primary scope namespace is flat.

## Rollback plan

1. **Script rollback:** `cp ~/.claude/scripts/rolling_memory.py.bak_phase2d_20260412_203654 ~/.claude/scripts/rolling_memory.py` restores the pre-Phase-2d state (still includes all Phase 2a + round 1/2 fixes). Loses the new `_category_from_scope`, `build_start_context`, `get_readonly_connection`, and CLI subcommand.
2. **Rule rollback:** Restore the legacy `Glob("reports/consilium_*.md")` step 2 in `~/.claude/rules/commands.md`. No backup file exists for the rule; the legacy form is captured verbatim in session handovers prior to 2026-04-13 (e.g., `handover_2026-04-12_202610.md`).
3. **DB rollback:** Not required — Phase 2d is a pure read-path addition. No schema change, no DB-row change. DB sha256 is byte-identical across the entire Phase 2d implementation + audit loop.
4. **Report rollback:** `git revert <this-commit>` removes this audit report from `reports/`.

## Sign-off

- Claude (implementer + round 1/2 fixer): confidence `very_high`.
- GPT-5.4 (round 1, `thinking_mode=high`): returned 1 HIGH + 2 MED + 1 LOW. All fixed in-session.
- GPT-5.4 (round 2, `thinking_mode=high`, fresh continuation): returned 1 MED + 1 LOW. All fixed in-session.
- Verification: 11 end-to-end tests + 4 regression gates + DB byte-identity. PASS.

Phase 2d is ready to be used by `/start`. Phase 2b (error taxonomy) and Phase 2c (preserve column) remain on the roadmap as deferred.
