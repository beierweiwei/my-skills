#!/usr/bin/env python3
"""2.2.18 / FB-043：dispatch compose/start 双 runtime 解析与拒绝指引。

覆盖：
① 双 runtime + 无 --runtime：按 `<crew-dir>/dispatch-agents.json` 同 role 最新
   `startedAt` 的 runtime 唯一解析；JSON 与 human 输出注明
   `runtimeResolvedFrom=registry:<agent>`；
② 跨 runtime 取最新（opencode 记录更晚 → 解析 opencode，反之 qoder）；
③ 无注册记录 → rc=1，指引列出 `--runtime qoder|opencode` 与判定方法；
④ 多 runtime 最新记录并列（无法判定）→ rc=1 拒绝；
⑤ 显式 `--runtime` 优先且不读注册表（注册表损坏仍按显式 runtime 组合）；
⑥ 单 runtime 项目不读注册表、行为不变（runtimeResolvedFrom=single-runtime）；
⑦ start 与 compose 同口径，start JSON 带 runtimeResolvedFrom；
⑧ 无依据时 start 同样拒绝且不调用 herdr。

运行：python3 -m unittest scripts/tests/test_2218_fb043_compose_runtime.py -v
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
DISPATCH = SKILL_DIR / "scripts" / "dispatch.py"
_spec = importlib.util.spec_from_file_location("dispatch_fb043", DISPATCH)
dispatch_mod = importlib.util.module_from_spec(_spec)
sys.modules["dispatch_fb043"] = dispatch_mod
_spec.loader.exec_module(dispatch_mod)

CORE_CAPS = {
    "helm": ["read", "dispatch"],
    "builder": ["read", "write-code", "run-commands"],
}
CORE_LIFECYCLE = {"helm": "persistent", "builder": "keep-until"}

HERDR_STUB = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["CREW_TEST_HERDR_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(sys.argv[1:], ensure_ascii=False) + "\\n")
args = sys.argv[1:]
if args[:2] == ["agent", "list"]:
    print(json.dumps({"result": {"agents": []}}))
elif args[:2] == ["pane", "split"]:
    print(json.dumps({"result": {"pane_id": "wX:pNEW"}}))
else:
    print(json.dumps({"result": {"ok": True}}))
"""


class ComposeRuntimeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.skill = self.base / "skill"
        for runtime in ("qoder", "opencode"):
            shutil.copytree(
                SKILL_DIR / "templates/agents" / runtime,
                self.skill / "templates/agents" / runtime,
            )
        refs = self.skill / "references"
        refs.mkdir()
        for name in ("crew-rules.md", "crew-acceptance.md", "plan-discipline.md"):
            (refs / name).write_text(f"# {name}\n", encoding="utf-8")
        (self.skill / "SKILL.md").write_text(
            "---\nname: agent-crew\nversion: 2.2.18\n---\n# skill\n", encoding="utf-8"
        )

        self.root = self.base / "proj"
        self.root.mkdir()
        (self.root / ".planning/portfolio").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.config = {
            "projectId": "proj",
            "projectName": "Probe Project",
            "skillRoot": str(self.skill),
            "skillVersion": "2.2.18",
            "portfolioDir": ".planning/portfolio",
            "roles": {"enabled": list(CORE_CAPS)},
            "hooks": {"gatePolicy": {"appendGates": []}, "customRoles": []},
        }
        self.write_config()
        self.write_role("qoder", "builder")

        self.bin = self.base / "bin"
        self.bin.mkdir()
        stub = self.bin / "herdr"
        stub.write_text(HERDR_STUB, encoding="utf-8")
        stub.chmod(0o755)
        self.herdr_log = self.base / "herdr.log"

    # ---------- fixture ----------

    def write_config(self) -> None:
        path = self.root / ".planning/orchestration.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.config, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")

    def role_path(self, runtime: str, role_id: str) -> Path:
        if runtime == "qoder":
            rel = Path(".qoder/agents") / f"{role_id}.md"
        else:
            rel = Path(".opencode/agent") / f"{role_id}.md"
        return self.root / rel

    def write_role(self, runtime: str, role_id: str) -> Path:
        path = self.role_path(runtime, role_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = {
            "id": role_id,
            "runtime": runtime,
            "extends": f"skill:{role_id}",
            "managed": False,
            "capabilities": CORE_CAPS[role_id],
            "gates": [],
            "lifecycle": CORE_LIFECYCLE[role_id],
            "prompt": path.relative_to(self.root).as_posix(),
        }
        if runtime == "qoder":
            frontmatter = f"name: {role_id}\ndescription: project {role_id} overlay"
        else:
            frontmatter = f"description: project {role_id} overlay\nmode: primary"
        path.write_text(
            "---\n" + frontmatter + "\n---\n"
            + "<!-- crew-role\n" + json.dumps(descriptor, ensure_ascii=False) + "\n-->\n"
            + f"# {role_id.capitalize()}\n## 项目约定\nPROJECT_ONLY_MARKER\n",
            encoding="utf-8",
        )
        return path

    def write_registry(self, records: dict) -> Path:
        path = dispatch_mod.registry_path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
        return path

    def write_corrupt_registry(self) -> Path:
        path = dispatch_mod.registry_path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        return path

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["PATH"] = str(self.bin) + os.pathsep + env.get("PATH", "")
        env["CREW_TEST_HERDR_LOG"] = str(self.herdr_log)
        return env

    def run_dispatch(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(DISPATCH), "--root", str(self.root), *args],
            capture_output=True, text=True, env=self.env(), cwd=str(self.root),
        )

    def compose(self, role: str = "builder", *extra: str) -> tuple[int, dict, str]:
        proc = self.run_dispatch("compose", "--role", role, *extra, "--json")
        try:
            report = json.loads(proc.stdout)
        except ValueError:
            self.fail(f"compose stdout 非 JSON：rc={proc.returncode}\n"
                      f"stdout={proc.stdout}\nstderr={proc.stderr}")
        return proc.returncode, report, proc.stdout

    def _write_composed(self, runtime: str, role: str) -> None:
        outcome = dispatch_mod.build_compose(self.root, role, runtime)
        self.assertTrue(outcome.ok, outcome.errors)
        desc = dispatch_mod.scalar(
            outcome.base_frontmatter["fields"].get("description", "")) or role
        if runtime == "qoder":
            rel = Path(".qoder/agents/crew") / f"{role}.md"
            descriptor = {
                "id": f"crew-{role}", "runtime": "qoder", "composed": True,
                "managed": True, "source": outcome.report.get("baseSource"),
                "override": outcome.role_path.relative_to(self.root).as_posix(),
            }
            text = ("---\n" f"name: crew-{role}\n"
                    f"description: {json.dumps(desc, ensure_ascii=False)}\n---\n"
                    "<!-- crew-role\n" f"{json.dumps(descriptor, ensure_ascii=False)}\n-->\n"
                    f"{outcome.composed_body}")
        else:
            rel = Path(".opencode/agent") / f"crew-{role}.md"
            text = f"---\n{outcome.base_frontmatter['raw'].strip()}\n---\n{outcome.composed_body}"
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def herdr_calls(self) -> list[list[str]]:
        if not self.herdr_log.exists():
            return []
        return [json.loads(line) for line in
                self.herdr_log.read_text(encoding="utf-8").splitlines() if line.strip()]

    # ---------- ① ② 注册表唯一解析 / 取最新 ----------

    def test_dual_runtime_resolves_latest_registry_runtime(self):
        self.write_role("opencode", "builder")
        self.write_registry({
            "proj-builder-q": {
                "role": "builder", "runtime": "qoder",
                "startedAt": "2026-11-10T00:00:00Z",
            },
            "proj-builder-o": {
                "role": "builder", "runtime": "opencode",
                "startedAt": "2026-11-11T00:00:00Z",
            },
        })
        rc, report, _ = self.compose("builder")
        self.assertEqual(0, rc, report)
        self.assertEqual("opencode", report["runtime"])
        self.assertEqual("registry:proj-builder-o", report["runtimeResolvedFrom"])

    def test_dual_runtime_latest_wins_in_either_direction(self):
        self.write_role("opencode", "builder")
        self.write_registry({
            "proj-builder-o": {
                "role": "builder", "runtime": "opencode",
                "startedAt": "2026-11-10T00:00:00Z",
            },
            "proj-builder-q": {
                "role": "builder", "runtime": "qoder",
                "startedAt": "2026-11-11T00:00:00Z",
            },
        })
        rc, report, _ = self.compose("builder")
        self.assertEqual(0, rc, report)
        self.assertEqual("qoder", report["runtime"])
        self.assertEqual("registry:proj-builder-q", report["runtimeResolvedFrom"])

    def test_dual_runtime_human_output_notes_resolution_source(self):
        self.write_role("opencode", "builder")
        self.write_registry({
            "proj-builder-q": {
                "role": "builder", "runtime": "qoder",
                "startedAt": "2026-11-11T00:00:00Z",
            },
        })
        proc = self.run_dispatch("compose", "--role", "builder")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn("registry:proj-builder-q", proc.stdout)

    # ---------- ③ ④ 无依据 / 冲突拒绝 ----------

    def test_no_registry_rejects_with_guidance(self):
        self.write_role("opencode", "builder")
        rc, report, _ = self.compose("builder")
        self.assertEqual(1, rc)
        self.assertIsNone(report["runtimeResolvedFrom"])
        message = "\n".join(report["errors"])
        self.assertIn("同时存在于多套 runtime", message)
        self.assertIn("无 role=builder", message)
        self.assertIn("--runtime qoder", message)
        self.assertIn("--runtime opencode", message)
        self.assertIn("现役总舵", message)
        self.assertFalse(dispatch_mod.registry_path(self.root).exists())

    def test_corrupt_registry_rejects_with_guidance(self):
        self.write_role("opencode", "builder")
        self.write_corrupt_registry()
        rc, report, _ = self.compose("builder")
        self.assertEqual(1, rc)
        message = "\n".join(report["errors"])
        self.assertIn("注册表不可读/损坏", message)
        self.assertIn("--runtime qoder", message)
        self.assertIn("--runtime opencode", message)
        self.assertIn("现役总舵", message)
        # start 同口径：拒绝且不触 herdr
        self._write_composed("qoder", "builder")
        proc = self.run_dispatch("start", "--role", "builder", "--pane", "wX:p1", "--json")
        self.assertEqual(1, proc.returncode)
        start_errors = "\n".join(json.loads(proc.stdout)["errors"])
        self.assertIn("注册表不可读/损坏", start_errors)
        self.assertIn("--runtime qoder", start_errors)
        self.assertEqual([], [c for c in self.herdr_calls() if c != ["agent", "list"]],
                    "除容量门只读 agent list（07B）外不得有 herdr 调用")

    def test_unreadable_registry_rejects_with_guidance(self):
        self.write_role("opencode", "builder")
        path = self.write_registry({
            "proj-builder-q": {
                "role": "builder", "runtime": "qoder",
                "startedAt": "2026-11-11T00:00:00Z",
            },
        })
        path.chmod(0o000)
        self.addCleanup(path.chmod, 0o644)
        rc, report, _ = self.compose("builder")
        self.assertEqual(1, rc)
        message = "\n".join(report["errors"])
        self.assertIn("注册表不可读/损坏", message)
        self.assertIn("--runtime qoder", message)
        self.assertIn("--runtime opencode", message)

    def test_conflicting_latest_records_reject_with_guidance(self):
        self.write_role("opencode", "builder")
        self.write_registry({
            "proj-builder-q": {
                "role": "builder", "runtime": "qoder",
                "startedAt": "2026-11-11T00:00:00Z",
            },
            "proj-builder-o": {
                "role": "builder", "runtime": "opencode",
                "startedAt": "2026-11-11T00:00:00Z",
            },
        })
        rc, report, _ = self.compose("builder")
        self.assertEqual(1, rc)
        message = "\n".join(report["errors"])
        self.assertIn("并列", message)
        self.assertIn("--runtime qoder", message)
        self.assertIn("--runtime opencode", message)

    # ---------- ⑤ 显式优先 ----------

    def test_explicit_runtime_takes_precedence_and_skips_registry(self):
        self.write_role("opencode", "builder")
        self.write_corrupt_registry()
        rc, report, _ = self.compose("builder", "--runtime", "qoder")
        self.assertEqual(0, rc, report)
        self.assertEqual("qoder", report["runtime"])
        self.assertEqual("flag", report["runtimeResolvedFrom"])

    # ---------- ⑥ 单 runtime 不变 ----------

    def test_single_runtime_ignores_registry(self):
        self.write_corrupt_registry()
        rc, report, _ = self.compose("builder")
        self.assertEqual(0, rc, report)
        self.assertEqual("qoder", report["runtime"])
        self.assertEqual("single-runtime", report["runtimeResolvedFrom"])

    # ---------- ⑦ ⑧ start 同口径 ----------

    def test_start_uses_same_resolution_and_reports_source(self):
        self.write_role("opencode", "builder")
        self.write_registry({
            "proj-builder-q": {
                "role": "builder", "runtime": "qoder",
                "startedAt": "2026-11-11T00:00:00Z",
            },
            "proj-builder-o": {
                "role": "builder", "runtime": "opencode",
                "startedAt": "2026-11-10T00:00:00Z",
            },
        })
        _, compose_report, _ = self.compose("builder")
        self._write_composed("qoder", "builder")
        proc = self.run_dispatch("start", "--role", "builder", "--pane", "wX:p1", "--json")
        self.assertEqual(0, proc.returncode, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(compose_report["runtime"], payload["runtime"])
        self.assertEqual("qoder", payload["runtime"])
        self.assertEqual("registry:proj-builder-q", payload["runtimeResolvedFrom"])
        starts = [c for c in self.herdr_calls() if c[:2] == ["agent", "start"]]
        self.assertEqual(1, len(starts), starts)
        self.assertIn("qodercli", starts[0])

    def test_start_without_evidence_rejects_before_herdr(self):
        self.write_role("opencode", "builder")
        self._write_composed("qoder", "builder")
        proc = self.run_dispatch("start", "--role", "builder", "--pane", "wX:p1", "--json")
        self.assertEqual(1, proc.returncode)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["errors"], payload)
        self.assertIn("--runtime", "\n".join(payload["errors"]))
        self.assertEqual([], [c for c in self.herdr_calls() if c != ["agent", "list"]],
                    "除容量门只读 agent list（07B）外不得有 herdr 调用")


if __name__ == "__main__":
    unittest.main()
