#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export CODEX_BRIDGE_ROOT="$ROOT"

python3 <<'PY'
from __future__ import annotations

import ast
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path


BRIDGE_ID = "claude-booster-codex-bridge"
ROOT = Path(os.environ["CODEX_BRIDGE_ROOT"]).resolve()
HOME = Path.home()

SKILLS_SRC = ROOT / "templates" / "codex" / "skills"
PROMPTS_SRC = ROOT / "templates" / "codex" / "prompts"
COMMANDS_SRC = ROOT / "templates" / "commands"

SKILLS_DST = HOME / ".agents" / "skills"
PROMPTS_DST = HOME / ".codex" / "prompts"
COMMANDS_DST = SKILLS_DST / "booster-command" / "references" / "commands"
MANIFEST_PATH = HOME / ".codex" / "claude-booster-bridge-manifest.json"
BACKUP_ROOT = HOME / ".codex" / "backups"


def fail(message: str) -> None:
    print(f"install_codex_bridge: {message}", file=sys.stderr)
    raise SystemExit(1)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def home_rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(HOME.resolve()))
    except ValueError:
        return str(path.resolve())


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def parse_frontmatter(path: Path) -> dict[str, str]:
    text = read_text(path)
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        fail(f"missing YAML frontmatter: {path}")

    end = None
    for idx, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = idx
            break
    if end is None:
        fail(f"unterminated YAML frontmatter: {path}")

    data: dict[str, str] = {}
    for offset, raw_line in enumerate(lines[1:end], start=2):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if raw_line[:1].isspace():
            fail(f"unsupported indented frontmatter line in {path}:{offset}")
        if ":" not in line:
            fail(f"invalid frontmatter line in {path}:{offset}: {raw_line!r}")
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", key):
            fail(f"invalid frontmatter key in {path}:{offset}: {key!r}")
        if not value:
            fail(f"empty frontmatter value in {path}:{offset}: {key}")

        if value[0] in ("'", '"'):
            try:
                parsed = ast.literal_eval(value)
            except (SyntaxError, ValueError) as exc:
                fail(f"invalid quoted frontmatter value in {path}:{offset}: {exc}")
            if not isinstance(parsed, str):
                fail(f"frontmatter value must be a string in {path}:{offset}: {key}")
            value = parsed
        else:
            if ": " in value:
                fail(
                    f"unquoted frontmatter value contains ': ' in {path}:{offset}; "
                    "quote the value"
                )
            if value[0] in "{}[]&*!|>@`":
                fail(f"unsupported unquoted frontmatter value in {path}:{offset}: {value!r}")

        data[key] = value

    if not data.get("description"):
        fail(f"missing description in frontmatter: {path}")
    if path.name == "SKILL.md":
        expected = path.parent.name
        if data.get("name") != expected:
            fail(f"skill name mismatch in {path}: expected {expected!r}, got {data.get('name')!r}")
    return data


def validate_sources() -> None:
    for root in (SKILLS_SRC, PROMPTS_SRC, COMMANDS_SRC):
        if not root.is_dir():
            fail(f"missing source directory: {root}")
        for path in root.rglob("*"):
            if path.is_symlink():
                fail(f"refusing to install symlink from template tree: {path}")

    for path in sorted(SKILLS_SRC.glob("*/SKILL.md")):
        parse_frontmatter(path)
    for path in sorted(PROMPTS_SRC.glob("*.md")):
        parse_frontmatter(path)

    commands = {path.stem for path in COMMANDS_SRC.glob("*.md")}
    skill_aliases = {path.parent.name for path in SKILLS_SRC.glob("*/SKILL.md")}
    skill_aliases.discard("booster-command")
    prompt_aliases = {path.stem for path in PROMPTS_SRC.glob("*.md")}
    missing = sorted((skill_aliases | prompt_aliases) - commands)
    if missing:
        fail(
            "missing command specs for aliases: "
            + ", ".join(missing)
            + f" (checked {COMMANDS_SRC})"
        )


def collect_tree(src_root: Path, dst_root: Path) -> dict[Path, Path]:
    planned: dict[Path, Path] = {}
    for src in sorted(src_root.rglob("*")):
        if src.is_file():
            planned[dst_root / src.relative_to(src_root)] = src
    return planned


def load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        return {}
    try:
        data = json.loads(read_text(MANIFEST_PATH))
    except json.JSONDecodeError as exc:
        fail(f"invalid existing manifest {MANIFEST_PATH}: {exc}")
    if data.get("bridge_id") != BRIDGE_ID:
        fail(f"refusing unknown manifest format: {MANIFEST_PATH}")
    return data


def manifest_paths(manifest: dict) -> set[str]:
    result: set[str] = set()
    for item in manifest.get("files", []):
        if isinstance(item, str):
            result.add(item)
        elif isinstance(item, dict) and isinstance(item.get("path"), str):
            result.add(item["path"])
    return result


def looks_bridge_owned(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")[:8192]
    except OSError:
        return False
    markers = (
        "Claude Booster",
        "Booster Command",
        "booster-command",
        "Codex compatibility layer",
    )
    return any(marker in text for marker in markers)


def atomic_write(target: Path, data: bytes, mode: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def backup_file(path: Path, backup_dir: Path) -> Path:
    rel = home_rel(path)
    target = backup_dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
    return target


def write_manifest(planned: dict[Path, Path]) -> None:
    files = [
        {
            "path": home_rel(dst),
            "sha256": sha256(dst),
            "source": str(src.relative_to(ROOT)),
        }
        for dst, src in sorted(planned.items(), key=lambda item: home_rel(item[0]))
    ]
    data = {
        "bridge_id": BRIDGE_ID,
        "installed_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_root": str(ROOT),
        "files": files,
    }
    atomic_write(
        MANIFEST_PATH,
        (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        0o644,
    )


def install() -> None:
    validate_sources()

    planned = collect_tree(SKILLS_SRC, SKILLS_DST)
    planned.update(collect_tree(PROMPTS_SRC, PROMPTS_DST))
    for src in sorted(COMMANDS_SRC.glob("*.md")):
        planned[COMMANDS_DST / src.name] = src

    manifest = load_manifest()
    owned = manifest_paths(manifest)
    planned_rels = {home_rel(dst) for dst in planned}
    stale_rels = sorted(owned - planned_rels)
    overwrite = os.environ.get("CODEX_BRIDGE_OVERWRITE") == "1"

    errors: list[str] = []
    for dst, src in planned.items():
        if dst.exists() or dst.is_symlink():
            rel = home_rel(dst)
            if dst.is_symlink():
                errors.append(f"{dst} is a symlink; remove it manually before install")
                continue
            if dst.is_dir():
                errors.append(f"{dst} is a directory but installer needs to write a file")
                continue
            if sha256(dst) == sha256(src):
                continue
            if rel in owned or looks_bridge_owned(dst) or overwrite:
                continue
            errors.append(
                f"{dst} exists and is not recorded as bridge-owned; "
                "set CODEX_BRIDGE_OVERWRITE=1 to back it up and replace it"
            )

    if errors:
        fail("preflight failed:\n  - " + "\n  - ".join(errors))

    changed = [
        (dst, src)
        for dst, src in planned.items()
        if not dst.exists() or sha256(dst) != sha256(src)
    ]
    stale_paths = [HOME / rel for rel in stale_rels if (HOME / rel).exists()]
    backup_dir = BACKUP_ROOT / (
        "claude_booster_codex_bridge_"
        + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    )

    backups: dict[Path, Path] = {}
    created: list[Path] = []
    try:
        for path, _src in changed:
            if path.exists():
                backups[path] = backup_file(path, backup_dir)
            else:
                created.append(path)
        for path in stale_paths:
            backups[path] = backup_file(path, backup_dir)

        for dst, src in changed:
            mode = src.stat().st_mode & 0o777
            atomic_write(dst, src.read_bytes(), mode or 0o644)

        for path in stale_paths:
            path.unlink()

        for dst, src in planned.items():
            if sha256(dst) != sha256(src):
                fail(f"destination hash mismatch after install: {dst}")
            if dst.name == "SKILL.md" or dst.parent == PROMPTS_DST:
                parse_frontmatter(dst)

        write_manifest(planned)
    except Exception:
        for path, backup in backups.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, path)
        for path in created:
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass
        raise

    skills_count = sum(1 for dst in planned if dst.name == "SKILL.md")
    prompts_count = sum(1 for dst in planned if dst.parent == PROMPTS_DST and dst.suffix == ".md")
    command_count = sum(1 for dst in planned if dst.parent == COMMANDS_DST and dst.suffix == ".md")

    print("Installed Claude Booster Codex bridge:")
    print(f"  skills:        {skills_count} -> {SKILLS_DST}")
    print(f"  prompts:       {prompts_count} -> {PROMPTS_DST}")
    print(f"  command specs: {command_count} -> {COMMANDS_DST}")
    print(f"  manifest:      {MANIFEST_PATH}")
    if backups:
        print(f"  backups:       {backup_dir}")
    if stale_paths:
        print(f"  stale removed: {len(stale_paths)}")
    print()
    print("Restart Codex, then use:")
    print("  $consilium <topic>")
    print("  $handover")
    print("  $architecture [--update]")
    print("  /prompts:consilium <topic>")


install()
PY
