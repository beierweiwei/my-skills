#!/usr/bin/env python3
"""2.2.27 / T57：dispatch claude runtime 三栈一等公民（切片 A）。

覆盖（评估报告 §3 表 1 + §4 R7）：
① compose 显式 `--runtime claude`：单栈 claude 项目解析成功（single-runtime）；
② locate_role 默认三元枚举（qoder/opencode/claude）；
③ registry_runtime_resolution 三元化：runtime=claude 记录可解析；
④ R7 backwards：旧过渡记录 runtime=qoder + actualRuntime=claude → 读作 claude；
⑤ 并列拒绝语义保持（claude 与 qoder 最新并列 → 拒绝）；
⑥ role_file_path claude 分支：core `.claude/agents/<role>.md`、custom
   `.claude/agents/custom-<role>.md`；
⑦ PROJECT_FRONTMATTER_FIELDS["claude"]={name,description}；非法字段拒绝；
⑧ discover_roles 发现 .claude/agents 条目；
⑨ deliver_claude：herdr `--kind claude` + 首条消息注入 `.claude/agents/crew-<role>.md`
   （沿 helm-9 过渡验证路径）；model 透传 `-- --model <model>`；
⑩ start --runtime claude：registry 记录 runtime=claude 且不再写 actualRuntime 双轨；
⑪ permission-mode 归一化（bypass_permissions → bypassPermissions）；
⑫ 组合产物守卫：`.claude/agents/crew-<role>.md` 缺失/过期 → 拒绝且不触 herdr。

运行：python3 -m unittest scripts.tests.test_t57_dispatch_claude -v
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
_spec = importlib.util.spec_from_file_location("dispatch_t57", DISPATCH)
dispatch_mod = importlib.util.module_from_spec(_spec)
sys.modules["dispatch_t57"] = dispatch_mod
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


class DispatchClaudeTest(unittest.TestCase):
    """T57 切片 A：dispatch claude 三栈。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.skill = self.base / "skill"
        for runtime in ("qoder", "opencode", "claude"):
            src = SKILL_DIR / "templates/agents" / runtime
            if src.is_dir():
                shutil.copytree(src, self.skill / "templates/agents" / runtime)
        refs = self.skill / "references"
        refs.mkdir(exist_ok=True)
        for name in ("crew-rules.md", "crew-acceptance.md", "plan-discipline.md"):
            (refs / name).write_text(f"# {name}\n", encoding="utf-8")
        (self.skill / "SKILL.md").write_text(
            "---\nname: agent-crew\nversion: 2.2.27\n---\n# skill\n", encoding="utf-8"
        )

        self.root = self.base / "proj"
        self.root.mkdir()
        (self.root / ".planning/portfolio").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.config = {
            "projectId": "proj",
            "projectName": "Probe Project",
            "skillRoot": str(self.skill),
            "skillVersion": "2.2.27",
            "portfolioDir": ".planning/portfolio",
            "roles": {"enabled": list(CORE_CAPS)},
            "hooks": {"gatePolicy": {"appendGates": []}, "customRoles": []},
        }
        self.write_config()
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

    def role_path(self, runtime: str, role_id: str, custom: bool = False) -> Path:
        return dispatch_mod.role_file_path(self.root, runtime, role_id, custom)

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
        if runtime == "opencode":
            frontmatter = f"description: project {role_id} overlay\nmode: primary"
        else:
            frontmatter = f"name: {role_id}\ndescription: project {role_id} overlay"
        path.write_text(
            "---\n" + frontmatter + "\n---\n"
            + "<!-- crew-role\n" + json.dumps(descriptor, ensure_ascii=False) + "\n-->\n"
            + f"# {role_id.capitalize()}\n## 项目约定\nPROJECT_ONLY_MARKER\n",
            encoding="utf-8",
        )
        return path

    def write_composed(self, runtime: str, role: str, stale: bool = False) -> Path:
        outcome = dispatch_mod.build_compose(self.root, role, runtime)
        self.assertTrue(outcome.ok, outcome.errors)
        desc = dispatch_mod.scalar(
            outcome.base_frontmatter["fields"].get("description", "")) or role
        if runtime == "opencode":
            rel = Path(".opencode/agent") / f"crew-{role}.md"
            text = f"---\n{outcome.base_frontmatter['raw'].strip()}\n---\n{outcome.composed_body}"
        else:
            if runtime == "qoder":
                rel = Path(".qoder/agents/crew") / f"{role}.md"
            else:
                rel = Path(".claude/agents") / f"crew-{role}.md"
            descriptor = {
                "id": f"crew-{role}", "runtime": runtime, "composed": True,
                "managed": True, "source": outcome.report.get("baseSource"),
                "override": outcome.role_path.relative_to(self.root).as_posix(),
            }
            text = ("---\n" f"name: crew-{role}\n"
                    f"description: {json.dumps(desc, ensure_ascii=False)}\n---\n"
                    "<!-- crew-role\n" f"{json.dumps(descriptor, ensure_ascii=False)}\n-->\n"
                    f"{outcome.composed_body}")
        if stale:
            text = text.replace("PROJECT_ONLY_MARKER", "STALE_MARKER")
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def write_registry(self, records: dict) -> Path:
        path = dispatch_mod.registry_path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
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

    def herdr_calls(self) -> list[list[str]]:
        if not self.herdr_log.exists():
            return []
        return [json.loads(line) for line in
                self.herdr_log.read_text(encoding="utf-8").splitlines() if line.strip()]

    # ---------- ① ② 显式 --runtime claude / 默认三元 ----------

    def test_compose_explicit_claude_runtime_single_stack(self):
        self.write_role("claude", "builder")
        rc, report, _ = self.compose("builder", "--runtime", "claude")
        self.assertEqual(0, rc, report)
        self.assertEqual("claude", report["runtime"])
        self.assertEqual("flag", report["runtimeResolvedFrom"])

    def test_locate_role_defaults_enumerate_three_runtimes(self):
        self.write_role("claude", "builder")
        resolved = dispatch_mod.locate_role(self.root, "builder", None)
        self.assertEqual("claude", resolved[0])
        self.assertFalse(resolved[1])
        self.assertEqual(self.role_path("claude", "builder"), resolved[2])

    def test_role_file_path_claude_core_and_custom(self):
        self.assertEqual(
            self.root / ".claude/agents/builder.md",
            dispatch_mod.role_file_path(self.root, "claude", "builder", False))
        self.assertEqual(
            self.root / ".claude/agents/custom-acme.md",
            dispatch_mod.role_file_path(self.root, "claude", "acme", True))

    # ---------- ③ ④ ⑤ registry 解析 / backwards / 并列 ----------

    def test_registry_runtime_claude_resolves(self):
        self.write_role("qoder", "builder")
        self.write_role("claude", "builder")
        self.write_registry({
            "proj-builder-c": {
                "role": "builder", "runtime": "claude",
                "startedAt": "2026-11-13T07:00:00Z",
            },
            "proj-builder-q": {
                "role": "builder", "runtime": "qoder",
                "startedAt": "2026-11-12T00:00:00Z",
            },
        })
        rc, report, _ = self.compose("builder")
        self.assertEqual(0, rc, report)
        self.assertEqual("claude", report["runtime"])
        self.assertEqual("registry:proj-builder-c", report["runtimeResolvedFrom"])

    def test_legacy_transition_record_reads_as_claude(self):
        """R7：旧过渡记录 runtime=qoder+actualRuntime=claude 在新版语义下读作 claude。"""
        self.write_role("qoder", "builder")
        self.write_role("opencode", "builder")
        self.write_role("claude", "builder")
        self.write_registry({
            "proj-builder-9": {
                "role": "builder", "runtime": "qoder", "actualRuntime": "claude",
                "startedAt": "2026-11-13T07:09:16Z",
            },
        })
        rc, report, _ = self.compose("builder")
        self.assertEqual(0, rc, report)
        self.assertEqual("claude", report["runtime"])
        self.assertEqual("registry:proj-builder-9", report["runtimeResolvedFrom"])

    def test_legacy_resolution_without_claude_role_file_fails_cleanly(self):
        """backwards 解析到 claude 但项目无 claude 角色文件 → 干净拒绝（不 IndexError）。"""
        self.write_role("qoder", "builder")
        self.write_role("opencode", "builder")
        self.write_registry({
            "proj-builder-9": {
                "role": "builder", "runtime": "qoder", "actualRuntime": "claude",
                "startedAt": "2026-11-13T07:09:16Z",
            },
        })
        rc, report, _ = self.compose("builder")
        self.assertEqual(1, rc)
        message = "\n".join(report["errors"])
        self.assertIn("runtime=claude", message)
        self.assertIn("角色文件", message)

    def test_conflicting_claude_and_qoder_latest_rejects(self):
        self.write_role("qoder", "builder")
        self.write_role("claude", "builder")
        self.write_registry({
            "proj-builder-c": {
                "role": "builder", "runtime": "claude",
                "startedAt": "2026-11-13T07:00:00Z",
            },
            "proj-builder-q": {
                "role": "builder", "runtime": "qoder",
                "startedAt": "2026-11-13T07:00:00Z",
            },
        })
        rc, report, _ = self.compose("builder")
        self.assertEqual(1, rc)
        message = "\n".join(report["errors"])
        self.assertIn("并列", message)
        self.assertIn("--runtime claude", message)
        self.assertIn("--runtime qoder", message)

    def test_effective_runtime_backwards_mapping(self):
        self.assertEqual(
            "claude", dispatch_mod.effective_runtime(
                {"runtime": "qoder", "actualRuntime": "claude"}))
        self.assertEqual("claude", dispatch_mod.effective_runtime({"runtime": "claude"}))
        self.assertEqual("qoder", dispatch_mod.effective_runtime({"runtime": "qoder"}))
        self.assertIsNone(dispatch_mod.effective_runtime({"runtime": "weird"}))
        self.assertIsNone(dispatch_mod.effective_runtime(None))

    # ---------- ⑦ frontmatter 校验 ----------

    def test_project_frontmatter_fields_claude(self):
        self.assertIn("claude", dispatch_mod.PROJECT_FRONTMATTER_FIELDS)
        self.assertEqual(
            {"name", "description"},
            dispatch_mod.PROJECT_FRONTMATTER_FIELDS["claude"])

    def test_validate_claude_frontmatter_rejects_permission_fields(self):
        self.write_role("claude", "builder")
        path = self.role_path("claude", "builder")
        text = path.read_text(encoding="utf-8").replace(
            "name: builder", "name: builder\ndisallowedTools: [Task]")
        path.write_text(text, encoding="utf-8")
        parsed = dispatch_mod.parse_project_role(
            self.root, path, "claude", False)
        errors = dispatch_mod.validate_project_frontmatter(
            parsed, "builder", "claude", False)
        self.assertTrue(any("权限/工具字段" in e for e in errors), errors)

    def test_validate_claude_frontmatter_requires_name_equal_id(self):
        self.write_role("claude", "builder")
        path = self.role_path("claude", "builder")
        text = path.read_text(encoding="utf-8").replace("name: builder", "name: wrong")
        path.write_text(text, encoding="utf-8")
        parsed = dispatch_mod.parse_project_role(self.root, path, "claude", False)
        errors = dispatch_mod.validate_project_frontmatter(
            parsed, "builder", "claude", False)
        self.assertTrue(any("name" in e for e in errors), errors)

    # ---------- ⑧ discover_roles ----------

    def test_discover_roles_finds_claude_entries(self):
        self.write_role("claude", "builder")
        entries = dispatch_mod.discover_roles(self.root)
        claude = [e for e in entries if e["runtime"] == "claude"]
        self.assertEqual(1, len(claude), entries)
        self.assertEqual("builder", claude[0]["id"])
        self.assertTrue(claude[0]["core"])

    def test_discover_roles_finds_claude_custom(self):
        path = self.role_path("claude", "acme", custom=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = {
            "id": "acme", "runtime": "claude", "extends": "skill:builder",
            "managed": False, "capabilities": ["read"], "gates": [],
            "lifecycle": "keep-until",
            "prompt": path.relative_to(self.root).as_posix(),
        }
        path.write_text(
            "---\nname: acme\ndescription: custom acme\n---\n"
            "<!-- crew-role\n" + json.dumps(descriptor) + "\n-->\n"
            "# Acme\n## 项目约定\nX\n", encoding="utf-8")
        entries = dispatch_mod.discover_roles(self.root)
        claude = [e for e in entries if e["runtime"] == "claude"]
        self.assertEqual(1, len(claude), entries)
        self.assertTrue(claude[0]["core"] is False)

    # ---------- ⑨ ⑩ ⑪ ⑫ deliver_claude / start ----------

    def _start_claude(self, *extra: str) -> subprocess.CompletedProcess:
        self.write_role("claude", "builder")
        self.write_composed("claude", "builder")
        return self.run_dispatch("start", "--role", "builder", "--runtime", "claude",
                                 "--pane", "wX:p1", "--json", *extra)

    def test_start_claude_delivers_via_herdr_kind_claude_with_first_inject(self):
        proc = self._start_claude()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual("claude", payload["runtime"])
        calls = self.herdr_calls()
        starts = [c for c in calls if c[:2] == ["agent", "start"]]
        self.assertEqual(1, len(starts), calls)
        self.assertIn("--kind", starts[0])
        self.assertEqual("claude", starts[0][starts[0].index("--kind") + 1])
        prompts = [c for c in calls if c[:2] == ["agent", "prompt"]]
        self.assertEqual(1, len(prompts), calls)
        self.assertIn("crew-builder.md", prompts[0][3])
        # registry 记录：runtime=claude，不再写 actualRuntime 双轨
        registry = json.loads(
            dispatch_mod.registry_path(self.root).read_text(encoding="utf-8"))
        info = registry["proj-builder"]
        self.assertEqual("claude", info["runtime"])
        self.assertNotIn("actualRuntime", info)

    def test_start_claude_model_from_config_via_flag(self):
        # 2.2.32/T76/C2：模型不手传——agents.roles.<role> 解析后经 --model 传 claude
        self.config["agents"] = {
            "default": {"backend": "claude", "model": ""},
            "roles": {"builder": {"backend": "claude", "model": "glm-5.3"}},
        }
        self.write_config()
        proc = self._start_claude()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual("glm-5.3", payload["model"])
        self.assertEqual("roles.builder", payload["modelSource"])
        starts = [c for c in self.herdr_calls() if c[:2] == ["agent", "start"]]
        self.assertEqual(1, len(starts))
        self.assertEqual(["--", "--model", "glm-5.3"], starts[0][-3:])

    def test_start_claude_rejects_manual_model_and_permission_mode(self):
        # 手传一律 rc=2（配置解析为唯一入口；permissionMode 由配置/模板归一化）
        for flag, value in (("--model", "glm-5.3"),
                            ("--permission-mode", "bypass_permissions")):
            proc = self._start_claude(flag, value)
            self.assertEqual(2, proc.returncode, proc.stdout + proc.stderr)
            self.assertIn("不再接受手传", proc.stderr)

    def test_start_claude_rejects_when_composed_missing(self):
        self.write_role("claude", "builder")
        proc = self.run_dispatch("start", "--role", "builder", "--runtime", "claude",
                                 "--pane", "wX:p1", "--json")
        self.assertEqual(1, proc.returncode)
        self.assertIn("组合基础代理", proc.stderr)
        self.assertEqual([], [c for c in self.herdr_calls()
                              if c[:2] in (["agent", "start"], ["agent", "prompt"])])

    def test_start_claude_rejects_when_composed_stale(self):
        self.write_role("claude", "builder")
        self.write_composed("claude", "builder", stale=True)
        proc = self.run_dispatch("start", "--role", "builder", "--runtime", "claude",
                                 "--pane", "wX:p1", "--json")
        self.assertEqual(1, proc.returncode)
        self.assertIn("组合基础代理", proc.stderr)
        self.assertEqual([], [c for c in self.herdr_calls()
                              if c[:2] in (["agent", "start"], ["agent", "prompt"])])


if __name__ == "__main__":
    unittest.main()
