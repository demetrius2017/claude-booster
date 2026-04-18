# Audit — Secrets / PII Leak Scan

Last-line-of-defense scan before any public push. Scope: entire working tree
(excluding `reports/`, now untracked) plus git history for shipped paths.

## Findings — after scrub applied

### Shipped surfaces (install.py, README, templates/, .gitignore, docs/)

**Clean** on all of:

- API key patterns (OpenAI, GitHub tokens, Slack, AWS access keys, Anthropic keys)
- SSH key markers (PEM header, public key lines)
- Inline passwords (common tool patterns, environment assignments)
- VPN / Reality / VLESS configuration URLs, UUIDs, pubkey material
- VPN IPs (public Hetzner/Yandex ranges, RFC1918 private ranges outside test paths)
- Router / Mikrotik-style credentials
- Trading account numbers, portfolio identifiers
- The author's Unix username, first name, or any of their specific project names
  — scrubbed from all shipped files (see table below for line-by-line changes)

**Residual (acceptable, public-by-design):**

- Git commit authorship uses `<author> <author@users.noreply.github.com>`,
  where GitHub's `noreply.github.com` namespace is intended to be public.
  No harm.

### Scrubs applied (table without repeating the matched strings)

| File | Before (class) | After (class) |
|------|---------------|---------------|
| `templates/scripts/add_frontmatter.py` | Hardcoded `users-<author>-*` path-prefix constants | Prefix derived dynamically from `getpass.getuser()` |
| `templates/scripts/index_reports.py` (3 spots) | Author's specific project names embedded in comments and docstrings | Generic placeholders (`umbrella/subproject`, `<topic>`) |
| `templates/scripts/rolling_memory.py` (3 spots) | Author's project name in docstring examples; "author's first name" in comment | Generic placeholder (`foo`, `example`); "the user" |
| `reports/` (28 design-history files) | Personal host paths, author names, specific project names, operational-intelligence about a live trading system | **Untracked from git** via `git rm --cached -r reports/` — files remain on disk locally but are no longer in the working tree for `git add` purposes |

### .gitignore hardening

Defense-in-depth exclusions added:

- SSH key filenames (`id_rsa*`, `id_ed25519*`, `id_ecdsa*`)
- Certificate / key extensions (`*.pem`, `*.key`, `*.p12`, `*.pfx`)
- Secret-shaped filenames (`*_secret.*`, `*.env`, `.env.local`, `.env.*.local`)
- Per-project Claude Code state (`.claude/settings.local.json`,
  `.claude/CLAUDE.md`, `.claude/sessions/`, `.claude/plans/`,
  `.claude/paste-cache/`) — so a user accidentally copying `~/.claude/` over
  the repo does not leak
- `reports/` — internal design history; remove this line only if a user
  consciously decides to ship their own audit/consilium history

## Git history

**Clean on shipped surfaces.** Probed history for common key prefixes and
sensitive substrings across `install.py`, `templates/`, `README.md` — zero
hits.

**Historical exposure in `reports/`**: prior commits that touched `reports/`
carry the personal material the scrub now excludes. Two options for public
push:

1. **Fresh repo init** — create a new public repo with a single squash commit
   containing only the sanitized working tree. No history is carried over.
2. **History rewrite** via `git filter-repo --path reports/ --invert-paths`.
   Destructive to local history; back up a tarball first.

## ADDITIONS-only scan

The staged diff was scanned — only lines that are being ADDED, not deletions
from `reports/` — against 13 patterns spanning credentials, PII, and
project-identifying strings. **Result: clean.** (Deletion lines still show
personal material because `git rm --cached -r reports/` removes tracked
files; this is expected and intentional.)

## Verdict

**GO for push** — provided one of the two history-cleanup options above is
taken before pushing to any public remote. The working tree is clean. The
ADDITIONS-only scan on the staged diff passed.

**Single residual risk**: `reports/` is in `.gitignore` going forward but may
still appear in existing git history. For a zero-risk public push, use option
1 (fresh repo init).

## Recommended push procedure

Option 1 (safest — no history rewrite):

```bash
cd /tmp
mkdir claude-booster-public && cd claude-booster-public
git init
cp -r <this-repo>/install.py .
cp -r <this-repo>/README.md .
cp -r <this-repo>/requirements.txt .
cp -r <this-repo>/.gitignore .
cp -r <this-repo>/templates .
cp -r <this-repo>/docs .
git add .
git commit -m "Claude Booster v1.0.1 — initial public release"
# create the public repo on GitHub, then:
git remote add origin git@github.com:<you>/claude-booster.git
git push -u origin main
```

Option 2 (rewrite this repo's history):

```bash
# Requires: pip install git-filter-repo
cd <this-repo>
git filter-repo --path reports/ --invert-paths --force
git remote add public git@github.com:<you>/claude-booster.git
git push -u public main
```
