"""Tests for check_booster_update.py — Booster version drift detection.

Run:
    python3 -m unittest discover -s templates/scripts/supervisor/tests -p "test_*.py" -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2]  # templates/scripts
HOOK = SCRIPTS / "check_booster_update.py"


def _run_hook(env_overrides: dict | None = None, stdin: str = "") -> subprocess.CompletedProcess:
    env = {**os.environ, **(env_overrides or {})}
    return subprocess.run(
        ["python3", str(HOOK), "--check"],
        capture_output=True, text=True, timeout=30, env=env, input=stdin,
    )


class TestNoManifest(unittest.TestCase):
    def test_missing_manifest_exits_silent(self):
        """No manifest on disk → exit 0, no stdout."""
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_hook({"CLAUDE_BOOSTER_MANIFEST_PATH": str(Path(tmp) / "nope.json")})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")


class TestManifestWithoutGitState(unittest.TestCase):
    def test_tar_install_no_git_sha_exits_silent(self):
        """Manifest without git_sha (tar-extracted install) → silent skip."""
        with tempfile.TemporaryDirectory() as tmp:
            m = Path(tmp) / "manifest.json"
            m.write_text(json.dumps({"version": "1.2.0", "installed_at": "2026-01-01T00:00:00Z"}))
            result = _run_hook({"CLAUDE_BOOSTER_MANIFEST_PATH": str(m)})
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")


class TestWithRealGitRepo(unittest.TestCase):
    """Integration: spin up a real git repo, create drift, verify hook detects it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.origin = Path(self.tmp) / "origin.git"
        self.clone = Path(self.tmp) / "clone"
        # Bare origin repo
        subprocess.check_call(["git", "init", "--bare", "--quiet", str(self.origin)])
        # Seed a working checkout with 2 commits, push to origin.
        subprocess.check_call(["git", "init", "--quiet", "-b", "main", str(self.clone)])
        subprocess.check_call(["git", "-C", str(self.clone), "config", "user.email", "t@t"])
        subprocess.check_call(["git", "-C", str(self.clone), "config", "user.name", "T"])
        subprocess.check_call(["git", "-C", str(self.clone), "remote", "add", "origin", str(self.origin)])
        (self.clone / "a").write_text("a")
        subprocess.check_call(["git", "-C", str(self.clone), "add", "."])
        subprocess.check_call(["git", "-C", str(self.clone), "commit", "--quiet", "-m", "first"])
        subprocess.check_call(["git", "-C", str(self.clone), "push", "--quiet", "-u", "origin", "main"])

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_manifest(self, extra: dict | None = None) -> Path:
        m = Path(self.tmp) / "manifest.json"
        sha = subprocess.check_output(["git", "-C", str(self.clone), "rev-parse", "HEAD"], text=True).strip()
        data = {
            "version": "1.2.0", "installed_at": "2026-01-01T00:00:00Z",
            "repo_path": str(self.clone), "git_sha": sha,
            "git_branch": "main", "git_remote": str(self.origin),
            **(extra or {}),
        }
        m.write_text(json.dumps(data))
        return m

    def test_up_to_date_exits_silent(self):
        """HEAD == origin/main → no output."""
        m = self._write_manifest()
        result = _run_hook({"CLAUDE_BOOSTER_MANIFEST_PATH": str(m)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_behind_origin_emits_warning(self):
        """Origin gains a commit the local clone doesn't have → hook emits
        additionalContext warning."""
        # Make a second clone, push a commit, so the tracked clone falls behind.
        other = Path(self.tmp) / "other"
        subprocess.check_call(["git", "clone", "--quiet", str(self.origin), str(other)])
        subprocess.check_call(["git", "-C", str(other), "config", "user.email", "o@o"])
        subprocess.check_call(["git", "-C", str(other), "config", "user.name", "O"])
        (other / "b").write_text("b")
        subprocess.check_call(["git", "-C", str(other), "add", "."])
        subprocess.check_call(["git", "-C", str(other), "commit", "--quiet", "-m", "newer"])
        subprocess.check_call(["git", "-C", str(other), "push", "--quiet", "origin", "main"])

        m = self._write_manifest()
        result = _run_hook({"CLAUDE_BOOSTER_MANIFEST_PATH": str(m)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(result.stdout, "")
        payload = json.loads(result.stdout)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Claude Booster update available", ctx)
        self.assertIn("1 commit", ctx)  # "1 commit(s) behind"
        self.assertIn(str(self.clone), ctx)


if __name__ == "__main__":
    unittest.main()
