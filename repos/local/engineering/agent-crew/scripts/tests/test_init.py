#!/usr/bin/env python3
"""init.py v2.1 行为契约测试：空仓安装/stub/pending/非法标记/force/refresh/check/dry-run/锁。

覆盖 impl-plan slice C A1/A2/A3/A5/A6 的单元级断言。
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("init_mod", _SCRIPTS / "init.py")
init_mod = importlib.util.module_from_spec(_spec)
sys.modules["init_mod"] = init_mod
_spec.loader.exec_module(init_mod)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(root: Path, exclude: tuple[str, ...] = ()) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if not path.is_file() or rel.startswith(".git/"):
            continue
        if rel in exclude:
            continue
        out[rel] = sha(path)
    return out


class InitV21Test(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "proj"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True,
                       capture_output=True)

    def run_init(self, *extra: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = init_mod.run(["--root", str(self.root), *extra])
        return code, out.getvalue()

    def crew(self) -> Path:
        return init_mod.crew_dir(self.root)

    def backups(self) -> int:
        directory = self.crew() / "backups"
        return len([p for p in directory.iterdir() if p.is_dir()]) if directory.is_dir() else 0

    def doctor(self, check: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.root / "scripts/doctor.py"), "--root", str(self.root),
             "--only", check, "--json"],
            capture_output=True, text=True,
        )

    # ---- A1 空仓 ----

    def test_empty_install_contents(self):
        code, _ = self.run_init("--yes")
        self.assertEqual(code, 0)
        agents = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertEqual(agents.count("<!-- agent-crew:orchestration:start -->"), 1)
        self.assertEqual(agents.count("<!-- agent-crew:orchestration:end -->"), 1)
        stubs = list((self.root / ".qoder/agents").glob("*.md")) + \
            list((self.root / ".opencode/agent").glob("*.md"))
        self.assertEqual(len(stubs), 14)
        manifest = json.loads((self.root / "docs/crew/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["manifestVersion"], 1)
        self.assertEqual({entry["kind"] for entry in manifest["entries"]}, {"file", "block"})
        self.assertEqual(len(manifest["roleStubs"]), 14)
        self.assertTrue(manifest["configHash"])
        for rel in ("docs/crew-rules.md", "docs/crew-acceptance.md",
                    "docs/orchestration-guide.md", "docs/plan-discipline.md"):
            self.assertFalse((self.root / rel).exists(), rel)
        self.assertFalse((self.root / "scripts/crew-lint.py").exists())
        self.assertFalse((self.root / ".opencode/skills/firstmate-dispatch").exists())
        cfg = json.loads((self.root / ".planning/orchestration.json").read_text(encoding="utf-8"))
        for key in ("projectId", "skillRoot", "skillVersion", "roles", "hooks"):
            self.assertIn(key, cfg)
        self.assertTrue((self.root / "docs/orchestration/topology.md").is_file())

    def test_second_run_idempotent_except_install_log(self):
        self.assertEqual(self.run_init("--yes")[0], 0)
        before = snapshot(self.root, exclude=("docs/crew/install-log.jsonl",))
        code, _ = self.run_init("--yes")
        self.assertEqual(code, 0)
        self.assertEqual(snapshot(self.root, exclude=("docs/crew/install-log.jsonl",)), before)

    def test_dry_run_zero_write(self):
        before = snapshot(self.root)
        code, out = self.run_init("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("dry-run", out)
        self.assertEqual(snapshot(self.root), before)

    def test_check_clean_then_drift(self):
        self.run_init("--yes")
        code, out = self.run_init("--check")
        self.assertEqual(code, 0, out)
        target = self.root / "scripts/dispatch.py"
        target.write_text(target.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")
        before = snapshot(self.root, exclude=("docs/crew/install-log.jsonl",))
        code, out = self.run_init("--check")
        self.assertEqual(code, 1)
        self.assertIn("scripts/dispatch.py", out)
        self.assertEqual(snapshot(self.root, exclude=("docs/crew/install-log.jsonl",)), before)

    # ---- A2 AGENTS ----

    def test_pending_keeps_agents_byte_identical(self):
        agents = self.root / "AGENTS.md"
        agents.write_text("# AGENTS\n\n项目自有内容。\n", encoding="utf-8")
        h0 = sha(agents)
        code, _ = self.run_init("--yes")
        self.assertEqual(code, 2)
        self.assertEqual(sha(agents), h0)
        pending = self.root / "docs/crew/AGENTS-block.pending.md"
        self.assertTrue(pending.is_file())
        text = pending.read_text(encoding="utf-8")
        for step in ("插到哪", "插什么", "插完怎么验"):
            self.assertIn(step, text)

    def test_invalid_markers_fail_closed(self):
        cases = {
            "two-pairs": (
                "<!-- agent-crew:orchestration:start -->\nA\n"
                "<!-- agent-crew:orchestration:end -->\n"
                "<!-- agent-crew:orchestration:start -->\nB\n"
                "<!-- agent-crew:orchestration:end -->\n"
            ),
            "half": "<!-- agent-crew:orchestration:start -->\nA\n",
            "fenced": "```\n<!-- agent-crew:orchestration:start -->\nA\n"
                      "<!-- agent-crew:orchestration:end -->\n```\n",
            "indented": "  <!-- agent-crew:orchestration:start -->\nA\n"
                        "  <!-- agent-crew:orchestration:end -->\n",
            "other": "<!-- agent-crew:orchestration:start -->\nA\n"
                     "<!-- agent-crew:orchestration:end -->\n"
                     "<!-- agent-crew:orchestration:extra -->\n",
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                self.setUp()
                (self.root / "AGENTS.md").write_text(text, encoding="utf-8")
                before = snapshot(self.root)
                code, _ = self.run_init("--yes")
                self.assertEqual(code, 1)
                self.assertEqual(snapshot(self.root), before)

    # ---- A5 force ----

    def test_force_refuses_agents_and_role(self):
        self.run_init("--yes")
        agents = self.root / "AGENTS.md"
        original = agents.read_text(encoding="utf-8")
        agents.write_text(original.replace(
            "<!-- agent-crew:orchestration:start -->",
            "<!-- agent-crew:orchestration:start -->\n<!-- probe -->", 1), encoding="utf-8")
        h0, b0 = sha(agents), self.backups()
        code, _ = self.run_init("--force")
        self.assertEqual(code, 1)
        self.assertEqual(sha(agents), h0)
        self.assertEqual(self.backups(), b0)

        agents.write_text(original, encoding="utf-8")
        role = self.root / ".qoder/agents/helm.md"
        role.write_text(role.read_text(encoding="utf-8") + "\n<!-- probe -->\n",
                        encoding="utf-8")
        h0, b0 = sha(role), self.backups()
        code, _ = self.run_init("--force")
        self.assertEqual(code, 1)
        self.assertEqual(sha(role), h0)
        self.assertEqual(self.backups(), b0)

    def test_force_overwrites_script_with_backup(self):
        self.run_init("--yes")
        target = self.root / "scripts/dispatch.py"
        target.write_text(target.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")
        h_drift = sha(target)
        b0 = self.backups()
        code, _ = self.run_init("--force")
        self.assertEqual(code, 0)
        self.assertNotEqual(sha(target), h_drift)
        self.assertGreater(self.backups(), b0)
        self.assertEqual(self.doctor("manifest.hash.file").returncode, 0)

    # ---- A6 refresh-block ----

    def test_refresh_block_requires_confirm_and_keeps_outside(self):
        self.run_init("--yes")
        alt = Path(self._tmp.name) / "skill-alt"
        alt.mkdir()
        shutil.copytree(_SCRIPTS.parent / "templates", alt / "templates")
        shutil.copy(_SCRIPTS.parent / "SKILL.md", alt / "SKILL.md")
        cfg_path = self.root / ".planning/orchestration.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["skillRoot"] = str(alt)
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        cfg_sha = sha(cfg_path)
        agents = self.root / "AGENTS.md"
        before = agents.read_text(encoding="utf-8")
        self.assertEqual(self.doctor("agents.block").returncode, 1)
        self.assertEqual(self.doctor("docs.pointers").returncode, 1)

        code, _ = self.run_init("--refresh-block")
        self.assertEqual(code, 2)
        self.assertEqual(agents.read_text(encoding="utf-8"), before)

        code, _ = self.run_init("--refresh-block", "--confirm")
        self.assertEqual(code, 0)
        after = agents.read_text(encoding="utf-8")
        self.assertIn(str(alt), after)
        self.assertEqual(sha(cfg_path), cfg_sha)
        start = "<!-- agent-crew:orchestration:start -->"
        end = "<!-- agent-crew:orchestration:end -->"
        self.assertEqual(after.split(start)[0], before.split(start)[0])
        self.assertEqual(after.split(end)[1], before.split(end)[1])
        self.assertEqual(self.doctor("agents.block").returncode, 0)
        self.assertEqual(self.doctor("docs.pointers").returncode, 0)
        self.assertEqual(self.doctor("manifest.config").returncode, 0)
        log = (self.root / "docs/crew/install-log.jsonl").read_text(encoding="utf-8")
        self.assertIn('"refresh-block"', log)

    # ---- A3 锁 / A1 only-failed ----

    def test_lock_contention_returns_2_and_logs_locked(self):
        with init_mod.init_lock(self.root):
            code, _ = self.run_init("--yes")
        self.assertEqual(code, 2)
        log = (self.root / "docs/crew/install-log.jsonl").read_text(encoding="utf-8")
        self.assertIn('"locked"', log)

    def test_only_failed_reinstalls_missing_script(self):
        self.run_init("--yes")
        target = self.root / "scripts/dispatch.py"
        target.write_text("drift\n", encoding="utf-8")
        code, _ = self.run_init("--yes")
        self.assertEqual(code, 2)
        target.unlink()
        code, _ = self.run_init("--only-failed")
        self.assertEqual(code, 0)
        self.assertTrue(target.is_file())
        self.assertEqual(self.doctor("manifest.hash.file").returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
