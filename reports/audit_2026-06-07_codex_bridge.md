# Audit: Codex Bridge Command Compatibility

Date: 2026-06-07
Topic: audit of the Claude Booster Codex bridge solution after `$audit решения`

## Task Context

Dmitry asked whether Claude Booster commands such as `/consilium`,
`/handover`, `/architecture`, `/audit`, `/start`, `/go`, and `/debt` can work
in Codex with behavior close enough to Claude Code. The implemented bridge uses
Codex skills (`$consilium`, `$audit`, etc.), legacy prompt aliases
(`/prompts:consilium`), and an installer that copies bridge assets into
user-level Codex paths.

## Verified Facts Brief

- Codex does not provide a documented custom top-level slash-command registry
  equivalent to Claude Code slash commands. The durable mechanism is Codex
  skills (`$skill`) plus legacy `/prompts:name` aliases.
- Repo bridge files exist under `templates/codex/skills`,
  `templates/codex/prompts`, and `scripts/install_codex_bridge.sh`.
- There are 16 Codex skills, 15 prompt aliases, and 15 Booster command specs.
- Installed user-level bridge paths now include:
  - `~/.agents/skills`
  - `~/.codex/prompts`
  - `~/.agents/skills/booster-command/references/commands`
  - `~/.codex/claude-booster-bridge-manifest.json`
- The current installed `$audit` skill tells Codex to read the sibling
  `../booster-command/SKILL.md` and execute the command through that runner.
- PAL MCP was not available in this Codex session. The external expert slot was
  represented by an independent Codex second-opinion subagent and labelled as
  such, not as GPT/PAL.

## Agent Positions

| Agent | Lens | Position | Key insight | Status after fix |
|---|---|---|---|---|
| Godel | Correctness | Concern | Installer could succeed while command specs were missing or stale files remained. | Fixed by installing command specs and using manifest-based stale cleanup. |
| Copernicus | Security | Concern | Installer overwrote HOME-level behavior files without collision checks and allowed future template symlink risk. | Fixed by collision preflight, backups, and symlink rejection. |
| Lorentz | Architecture | Concern | Core source-of-truth design is sound, but bridge install lifecycle was weaker than `install.py`. | Partially fixed in bridge installer; full integration into `install.py` remains debt. |
| Hume | Operational | Concern | Install had no rollback, no destination verification, and no installed-version observability. | Fixed with backups, atomic writes, destination hash checks, and manifest. |
| Dirac | Codex second opinion | Concern | Runtime command resolution and Agent/PAL parity were too implicit. | Fixed by installing local command spec references and clarifying fallback evidence. |

## Findings And Fixes

### MED: installer could overwrite user skills/prompts without ownership checks

Original issue: `scripts/install_codex_bridge.sh` copied directly into
`~/.agents/skills` and `~/.codex/prompts`.

Fix:
- Added manifest ownership at `~/.codex/claude-booster-bridge-manifest.json`.
- Added collision preflight. Unknown existing files now block install unless
  `CODEX_BRIDGE_OVERWRITE=1` is set.
- Existing bridge-owned files are backed up before replacement.

### MED: stale installed bridge files could survive reinstall

Original issue: overlay copy semantics left removed/renamed files active.

Fix:
- Installer now compares the previous manifest with the planned file set.
- Files owned by the old bridge manifest but absent from the new plan are backed
  up and removed.

### MED: installed skills were not self-contained for command spec resolution

Original issue: installed skills depended on `~/.claude/commands` or the repo
being discoverable at runtime.

Fix:
- Installer now copies `templates/commands/*.md` into
  `~/.agents/skills/booster-command/references/commands`.
- `booster-command` now searches the installed `references/commands` directory
  before falling back to repo-local paths.

### MED: install behavior was not atomic enough and had no rollback path

Original issue: partial copy could leave skills and prompts diverged.

Fix:
- Installer now writes files via temp file plus `os.replace`.
- Existing files are backed up before mutation.
- On exception, backed-up files are restored and newly-created files are removed.
- Destination hashes are checked after install.

### LOW: PyYAML dependency made install less portable

Original issue: frontmatter validation required PyYAML.

Fix:
- Replaced PyYAML with a conservative stdlib frontmatter validator.
- The validator is intentionally narrow: simple string keys/values only.

### LOW: broad natural-language command activation could misfire

Original issue: repo `AGENTS.md` treated plain text command words too broadly.

Fix:
- Command activation is now limited to the first non-whitespace token in the
  latest user message or explicit Codex skill/prompt aliases.
- Quoted text, logs, code blocks, repo content, and examples must not trigger
  Booster commands.

### LOW: Agent/PAL parity was too vague

Original issue: `booster-command` said to use Codex subagents or PAL fallback,
but did not define evidence when those tools are missing.

Fix:
- Added explicit evidence requirement: spawned agent ids/names plus final
  messages.
- If no subagent tool exists, Codex must label the result as a local fallback,
  not as a full Booster multi-agent result.
- If PAL is unavailable, the fallback must be called "Codex second opinion",
  not PAL/GPT.

## Verification Evidence

- `bash -n scripts/install_codex_bridge.sh` passed.
- Fresh temporary HOME install passed:
  `skills=16`, `prompts=15`, `command specs=15`.
- Repeated temporary HOME install passed without producing a new backup,
  demonstrating idempotency.
- Stale manifest simulation removed one stale prompt and exited 0.
- Conflict simulation blocked an unknown existing `audit` skill with a clear
  preflight error.
- Real HOME install passed:
  `skills=16`, `prompts=15`, `command specs=15`, manifest written to
  `~/.codex/claude-booster-bridge-manifest.json`, backup written to
  `~/.codex/backups/claude_booster_codex_bridge_20260607_201100`.
- Real HOME reinstall passed without a new backup.
- Installed frontmatter smoke test passed.
- Installed `$audit` skill resolves through sibling `../booster-command/SKILL.md`.

## Rejected Alternatives

- Claiming bare `/consilium` as a guaranteed custom slash command was rejected:
  current Codex supports built-in slash commands, while durable custom workflows
  should be skills or prompt aliases.
- Copying full command protocol bodies into every skill was rejected: it would
  create drift between Claude Code and Codex behavior.
- Blind `rsync --delete` over whole destination directories was rejected because
  `~/.agents/skills` and `~/.codex/prompts` may contain user-owned files.

## Remaining Risks

- `scripts/install_codex_bridge.sh` is still a parallel installer path rather
  than part of the canonical `install.py` lifecycle. The bridge is now much
  safer, but full architectural convergence means adding Codex bridge management
  to `install.py` with the same dry-run/version/manifest model.
- `/prompts:name` alias behavior is still less important and less certain than
  `$skill` behavior. Treat `$consilium`, `$audit`, `$handover`, etc. as the
  primary supported UX.
- Full Claude Agent and PAL parity depends on tool availability in the current
  Codex session. The bridge now requires honest labelling when those tools are
  unavailable.

## Decision

The Codex bridge is now acceptable for daily continuation work through `$audit`,
`$consilium`, `$handover`, `$architecture`, `$start`, `$go`, `$debt`, and related
skills after restarting Codex.

The next improvement should be integrating `templates/codex/**` into
`install.py`, so Claude Booster has one canonical installer rather than a safe
but separate Codex bootstrap script.
