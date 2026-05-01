---
name: consilium_2026-05-02_update_command
description: "Consilium: /update command for mid-session auto-update from GitHub"
type: consilium
date: 2026-05-02
preserve: true
---

# Consilium — /update Command Architecture

## Task Context

Dmitry wants a `/update` slash command that auto-updates Claude Booster from GitHub without leaving the current session: check version → git pull → install.py --yes → hot-reload. Key questions: feasibility, security, UX, implementation approach.

## Verified Facts Brief

- `check_booster_update.py` SessionStart hook already does: git fetch (5s timeout), count commits behind, optional auto-install via `CLAUDE_BOOSTER_AUTO_UPDATE=1`
- `install.py` requires local `templates/` directory — can't run from remote URL
- Manifest at `~/.claude/.booster-manifest.json` stores `repo_path`, `git_sha`, `git_remote`
- Hot-reload of `rules/*.md` and `commands/*.md` works on every prompt — no restart needed
- Scripts and hooks may need session restart to fully take effect

## Agent Positions

| Agent | Position | Key Insight | KPI |
|-------|----------|-------------|-----|
| Architect | A+B fallback, BUILD | Local clone default, shallow-clone fallback if missing. Reject tarball (provenance, diffing). Smoke-check post-install | 30s completion |
| Security | REJECT without conditions | Blast radius = every prompt. Need commit signing, explicit confirmation, session restart. Rollback is crash-recovery, not trust boundary | 100% confirmed updates |
| DevOps | BUILD, reuse existing infra | Wrap `check_booster_update.py --auto-install`. Dirty tree = hard abort. Print backup path. Warn if hooks changed | 30s round-trip |
| Product | BUILD rules-only cut | No confirmation (51% rule — user typed /update = approval). Gate `--full` behind flag. Rules-only mode skips scripts | <5min commit→active |
| GPT-5.5 | BUILD with sharp safety boundary | Rules-only auto OK if mechanically classified safe. Full = gated by --full + confirmation. ff-only merge, validate remote URL, classify changed files | Timing telemetry |

## Convergence

- **5/5:** dirty tree = hard abort, no merge attempts
- **5/5:** Option A (local clone git pull) is correct, tarball is wrong
- **4/5:** rules-only partial update is the right first cut
- **4/5:** no confirmation for safe rule changes (51% rule)
- **5/5:** backup path must always be printed
- **4/5:** warn (don't block) if hooks changed

## Dissent

Security agent wants commit signing + explicit confirmation + session restart before any approval. GPT mediates: "commit signing pragmatic to skip short-term for single-user; require for full unattended updates medium-term."

## Decision Made

**BUILD as command file** — `commands/update.md` instructs Claude to execute:

1. Read `repo_path` from manifest (`~/.claude/.booster-manifest.json`)
2. `git -C <repo_path> status --porcelain` → dirty = abort with "stash or commit first"
3. `git -C <repo_path> fetch origin main`
4. Compare local vs remote commit count
5. `git -C <repo_path> pull --ff-only` → non-ff = abort
6. `python3 <repo_path>/install.py --yes`
7. Print: old→new version, files changed, backup path
8. Warn if scripts/hooks changed → "restart Claude Code for full effect"
9. Rules/commands hot-reload automatically on next prompt

**Not a new Python script** — reuses existing install.py infrastructure. The command file provides the orchestration instructions.

## Rejected Alternatives

| Alternative | Reason for rejection |
|-------------|---------------------|
| GitHub tarball download | No provenance, no diffing, stale git_sha in manifest, two-source-of-truth |
| New Python script | Unnecessary — install.py + git pull cover everything |
| Commit signing (v1) | Overkill for single-user personal tool; re-evaluate if distribution grows |
| Session restart requirement | Conflicts with core goal (update without leaving); warn instead |
| Rules-only / --full split | Premature — install.py already handles atomically; add if needed later |

## Risks

1. **Rules can still affect behavior** — even "safe" rule updates change every future prompt
2. **Installer self-update** — after git pull, the NEW install.py runs (not the old one)
3. **Remote URL drift** — local clone could be repointed to malicious remote; validate against manifest
4. **Partial install failure** — mitigated by install.py's atomic writes + rollback

## Implementation Recommendations

1. Create `templates/commands/update.md` + `~/.claude/commands/update.md`
2. Command file instructs Claude to run the git+install sequence
3. No new dependencies, no new scripts
4. Version bump not needed (command-only addition)
5. Test: modify a rule in the repo, push, run `/update` in a session, verify hot-reload
