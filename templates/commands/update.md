---
description: "Auto-update Claude Booster from GitHub without leaving the session. Pulls latest, runs install.py, hot-reloads rules."
---

# /update — Mid-Session Auto-Update

Update Claude Booster to the latest version from GitHub without restarting.

## What to do

1. **Read the manifest** to find repo path:
```bash
python3 -c "import json; m=json.load(open('$HOME/.claude/.booster-manifest.json')); print(m.get('repo_path',''))"
```
Store the result as REPO_PATH. If empty — abort: "No repo_path in manifest. Run install.py manually."

2. **Check for dirty tree** (abort if dirty):
```bash
git -C <REPO_PATH> status --porcelain
```
If output is non-empty — abort: "Working tree is dirty. Run `git -C <REPO_PATH> stash` first."

3. **Read current version** before update:
```bash
python3 -c "import json; print(json.load(open('$HOME/.claude/.booster-manifest.json')).get('version','?'))"
```

4. **Fetch and pull** (fast-forward only):
```bash
git -C <REPO_PATH> fetch origin main
git -C <REPO_PATH> pull --ff-only
```
If pull fails (non-fast-forward) — abort: "Cannot fast-forward. Local repo has diverged."

5. **Run installer**:
```bash
python3 <REPO_PATH>/install.py --yes
```
If exit code ≠ 0 — report the error. Backup path is in output.

6. **Read new version** and report:
```bash
python3 -c "import json; print(json.load(open('$HOME/.claude/.booster-manifest.json')).get('version','?'))"
```

7. **Report results:**
   - Old version → New version
   - Number of files changed (from install.py output)
   - Backup path (from install.py output)
   - "Rules and commands hot-reloaded. Changes active on your next prompt."
   - If install.py output mentions scripts/ or settings.json changes: "⚠ Scripts or hooks changed. Restart Claude Code for full effect."

## Safety rules

- **Dirty tree = hard abort.** Never force-push, stash, or reset.
- **Fast-forward only.** If the local repo diverged — abort, don't merge.
- **Never run with --force.** Let the user decide.
- **Always print the backup path** from install.py output.
