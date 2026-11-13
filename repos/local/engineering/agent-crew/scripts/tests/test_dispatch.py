#!/usr/bin/env python3
"""dispatch.py 行为契约测试（v2.1 切片 A：dispatch + 继承组合）。

覆盖：
① 组合顺序 append-only（base 先、项目后；不做 section 合并；H1 各一个）；
② 真实含 `# Builder` 的技能基类（从技能仓 templates 复制）；
③ token 全部渲染为实际路径，组合文本零 `{{...}}` 残留；
④ 受保护标题冲突 rc=1（`## 引用`）；
⑤ 项目 frontmatter 权限/工具字段拒绝；
⑥ gates 只追加；列核心 gate → rc=1；
⑦ Qoder 命令构造（--agent crew-<role>；组合基础代理缺失/过期 fail closed）与 delivery；
⑧ opencode 命令构造（--agent crew-<role>）与 delivery；
⑨ agent-log 字段（delivery/composedSha256/baseRef/overlaySha256/composePath）；
⑩ 项目角色文件零写入、不生成第二份角色文件；
⑪ fail-closed：缺 skillRoot、未知 token、extends/managed 非法、自定义角色越权。

运行：python3 -m unittest discover -s scripts/tests
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
DISPATCH = SKILL_DIR / "scripts" / "dispatch.py"
_spec = importlib.util.spec_from_file_location("dispatch_under_test", DISPATCH)
dispatch_mod = importlib.util.module_from_spec(_spec)
sys.modules["dispatch_under_test"] = dispatch_mod
_spec.loader.exec_module(dispatch_mod)
QODER_TEMPLATES = SKILL_DIR / "templates" / "agents" / "qoder"
OPENCODE_TEMPLATES = SKILL_DIR / "templates" / "agents" / "opencode"

CORE_CAPS = {
    "helm": ["read", "dispatch"],
    "firstmate": ["read", "write-docs", "dispatch"],
    "builder": ["read", "write-code", "run-commands"],
    "planner": ["read", "write-docs"],
    "reviewer": ["read", "report"],
    "runner": ["read", "run-commands"],
    "scout": ["read", "report"],
}
CORE_LIFECYCLE = {
    "helm": "persistent",
    "firstmate": "persistent",
    "builder": "keep-until",
    "planner": "one-shot",
    "reviewer": "one-shot",
    "runner": "one-shot",
    "scout": "one-shot",
}
CORE_GATES = {
    "helm": ["three-human-gates", "no-merge", "no-push"],
    "firstmate": ["three-human-gates", "no-merge", "no-push"],
    "builder": ["three-human-gates", "review-required"],
    "planner": ["three-human-gates"],
    "reviewer": ["three-human-gates", "report-only"],
    "runner": ["three-human-gates", "data-safety"],
    "scout": ["three-human-gates", "report-only"],
}

HERDR_STUB = """#!/usr/bin/env python3
import json, os, sys
with open(os.environ["CREW_TEST_HERDR_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(sys.argv[1:], ensure_ascii=False) + "\\n")
args = sys.argv[1:]
if args[:2] == ["agent", "list"]:
    names = json.loads(os.environ.get("CREW_TEST_HERDR_AGENTS", "[]"))
    print(json.dumps({"result": {"agents": [{"name": name} for name in names]}}))
elif args[:2] == ["pane", "split"]:
    print(json.dumps({"result": {"pane_id": "wX:pNEW"}}))
elif args[:2] == ["tab", "create"]:
    print(json.dumps({"result": {
        "tab": {"tab_id": "wX:tNEW"}, "root_pane": {"pane_id": "wX:pNEW"},
    }}))
else:
    print(json.dumps({"result": {"ok": True}}))
"""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DispatchTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.skill = self.base / "skill"
        self._make_skill()
        self.root = self.base / "proj"
        self.root.mkdir()
        (self.root / ".planning/portfolio").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.config = {
            "projectId": "proj",
            "projectName": "Probe Project",
            "skillRoot": str(self.skill),
            "skillVersion": "2.1.0",
            "portfolioDir": ".planning/portfolio",
            "docNames": {
                "req": "需求.md",
                "reqPath": "",
                "accept": "验收.md",
                "context": "CONTEXT.md",
            },
            "captainDecisions": ["合并/推送/worktree 清理"],
            "redLines": ["原始数据不全量读取，只读必要列/行"],
            "dataDir": "data/raw/",
            "roles": {"enabled": list(CORE_CAPS)},
            "hooks": {"gatePolicy": {"appendGates": []}, "customRoles": []},
        }
        self.write_config()
        self.write_role("qoder", "builder")
        self.write_role("opencode", "firstmate")
        self.bin = self.base / "bin"
        self.bin.mkdir()
        stub = self.bin / "herdr"
        stub.write_text(HERDR_STUB, encoding="utf-8")
        stub.chmod(0o755)
        self.herdr_log = self.base / "herdr.log"
        self.herdr_agents: list[str] = []

    # ---------- fixture ----------

    def _make_skill(self) -> None:
        for src, dst in (
            (QODER_TEMPLATES, self.skill / "templates/agents/qoder"),
            (OPENCODE_TEMPLATES, self.skill / "templates/agents/opencode"),
        ):
            shutil.copytree(src, dst)
        for rel in (
            "templates/agents/qoder/builder.md",
            "templates/agents/qoder/firstmate.md",
            "templates/agents/opencode/firstmate.md",
        ):
            path = self.skill / rel
            path.write_text(path.read_text(encoding="utf-8") + "\nBASE_ONLY_MARKER\n",
                            encoding="utf-8")
        refs = self.skill / "references"
        refs.mkdir()
        for name in ("crew-rules.md", "crew-acceptance.md", "plan-discipline.md"):
            (refs / name).write_text(f"# {name}\n", encoding="utf-8")
        (self.skill / "SKILL.md").write_text(
            "---\nname: agent-crew\nversion: 2.1.0\n---\n# skill\n", encoding="utf-8"
        )

    def write_config(self) -> None:
        path = self.root / ".planning/orchestration.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.config, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")

    def role_path(self, runtime: str, role_id: str, custom: bool = False) -> Path:
        if runtime == "qoder":
            rel = Path(".qoder/agents") / ("custom" if custom else "") / f"{role_id}.md"
        else:
            name = f"custom-{role_id}" if custom else role_id
            rel = Path(".opencode/agent") / f"{name}.md"
        return self.root / rel

    def write_role(self, runtime: str, role_id: str, base: str | None = None,
                   custom: bool = False, capabilities: list[str] | None = None,
                   gates: list[str] | None = None, lifecycle: str | None = None,
                   body: str | None = None, frontmatter_extra: str = "",
                   descriptor_extra: dict | None = None) -> Path:
        base = base or role_id
        path = self.role_path(runtime, role_id, custom)
        path.parent.mkdir(parents=True, exist_ok=True)
        caps = capabilities or CORE_CAPS[base]
        life = lifecycle or CORE_LIFECYCLE[base]
        descriptor = {
            "id": role_id,
            "runtime": runtime,
            "extends": f"skill:{base}",
            "managed": False,
            "capabilities": caps,
            "gates": gates or [],
            "lifecycle": life,
            "prompt": path.relative_to(self.root).as_posix(),
        }
        if descriptor_extra:
            descriptor.update(descriptor_extra)
        if runtime == "qoder":
            frontmatter = f"name: {role_id}\ndescription: project {role_id} overlay"
        else:
            frontmatter = f"description: project {role_id} overlay\nmode: primary"
        if frontmatter_extra:
            frontmatter += "\n" + frontmatter_extra
        default_body = f"# {base.capitalize()}\n## 项目约定\nPROJECT_ONLY_MARKER：项目追加动作。\n"
        path.write_text(
            "---\n" + frontmatter + "\n---\n"
            + "<!-- crew-role\n" + json.dumps(descriptor, ensure_ascii=False) + "\n-->\n"
            + (body if body is not None else default_body),
            encoding="utf-8",
        )
        return path

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["PATH"] = str(self.bin) + os.pathsep + env.get("PATH", "")
        env["CREW_TEST_HERDR_LOG"] = str(self.herdr_log)
        env["CREW_TEST_HERDR_AGENTS"] = json.dumps(self.herdr_agents)
        return env

    def run_dispatch(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(DISPATCH), "--root", str(self.root), *args],
            capture_output=True, text=True, env=self.env(), cwd=str(self.root),
        )

    def compose(self, role: str, runtime: str) -> tuple[int, dict]:
        proc = self.run_dispatch("compose", "--role", role, "--runtime", runtime, "--json")
        try:
            report = json.loads(proc.stdout)
        except ValueError:
            self.fail(f"compose stdout 非 JSON：rc={proc.returncode}\n"
                      f"stdout={proc.stdout}\nstderr={proc.stderr}")
        return proc.returncode, report

    def _write_composed(self, runtime: str, role: str) -> None:
        outcome = dispatch_mod.build_compose(self.root, role, runtime)
        if not outcome.ok:
            return
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

    def start(self, role: str, runtime: str, pane: str = "wX:p1",
              name: str | None = None) -> subprocess.CompletedProcess:
        self._write_composed(runtime, role)
        args = ["start", "--role", role, "--runtime", runtime, "--pane", pane, "--json"]
        if name is not None:
            args.extend(["--name", name])
        return self.run_dispatch(*args)

    def herdr_calls(self) -> list[list[str]]:
        if not self.herdr_log.exists():
            return []
        return [json.loads(line) for line in
                self.herdr_log.read_text(encoding="utf-8").splitlines() if line.strip()]

    def agent_log(self) -> list[dict]:
        path = self.root / ".git/crew/agent-log.jsonl"
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def project_snapshot(self) -> dict[str, str]:
        return {
            path.relative_to(self.root).as_posix(): sha256_file(path)
            for path in sorted(self.root.rglob("*"))
            if path.is_file() and ".git/" not in path.relative_to(self.root).as_posix()
        }

    # ---------- ① ② 组合顺序 / 真实基类 ----------

    def test_compose_append_only_with_real_builder_base(self):
        before = self.project_snapshot()
        rc, report = self.compose("builder", "qoder")
        self.assertEqual(0, rc, report)
        # compose 是 dry-run：不写项目、不写 agent-log
        self.assertEqual(before, self.project_snapshot())
        self.assertFalse((self.root / ".git/crew/agent-log.jsonl").exists())
        self.assertIn("Builder", report["baseHeadings"])
        self.assertIn("引用", report["baseHeadings"])
        self.assertIn("职责边界", report["baseHeadings"])
        self.assertIn("项目约定", report["appendedHeadings"])
        self.assertEqual([], report["duplicateHeadings"])
        self.assertEqual([], report["unresolvedTokens"])
        self.assertEqual([], report["residualSkillRootTokens"])
        self.assertTrue(report["basePointersPresent"])
        self.assertTrue(report["composedSha256"])
        self.assertEqual("composed-agent", report["delivery"])

    def test_compose_no_section_merge_and_base_first_order(self):
        rc, _ = self.compose("builder", "qoder")
        self.assertEqual(0, rc)
        start = self.start("builder", "qoder")
        self.assertEqual(0, start.returncode, start.stderr)
        row = self.agent_log()[-1]
        composed = Path(row["promptPath"]).read_text(encoding="utf-8")
        # H1 各一个、均交付：base 的 # Builder 未被项目同名根标题吞掉
        self.assertEqual(2, composed.count("# Builder"))
        # base 全文在前，项目追加在后（不做 section 合并）
        self.assertLess(composed.index("## 上下文与工位"), composed.index("PROJECT_ONLY_MARKER"))
        self.assertIn("## 职责边界", composed)
        self.assertIn("<!-- agent-crew:project-overlay -->", composed)

    def test_compose_affected_roles_carry_verification_pointer(self):
        # S-R3：五角色双栈组合通过且组合正文含 VR 实际路径（指针随基类交付）
        for runtime in ("qoder", "opencode"):
            for role in ("builder", "reviewer", "firstmate", "runner", "helm"):
                if not self.role_path(runtime, role).is_file():
                    self.write_role(runtime, role)
                with self.subTest(runtime=runtime, role=role):
                    rc, report = self.compose(role, runtime)
                    self.assertEqual(0, rc, report)
                    self.assertEqual([], report["unresolvedTokens"])
                    outcome = dispatch_mod.build_compose(self.root, role, runtime)
                    self.assertTrue(outcome.ok, outcome.errors)
                    self.assertIn(
                        str(self.skill / "references" / "verification-redlines.md"),
                        outcome.composed_body,
                    )

    # ---------- ③ token 渲染 ----------

    def test_tokens_render_to_real_paths_without_residue(self):
        start = self.start("builder", "qoder")
        self.assertEqual(0, start.returncode, start.stderr)
        composed = Path(self.agent_log()[-1]["promptPath"]).read_text(encoding="utf-8")
        self.assertNotIn("{{", composed)
        self.assertIn(str(self.skill / "references/crew-rules.md"), composed)
        self.assertIn(str(self.skill / "references/crew-acceptance.md"), composed)
        self.assertIn(str(self.skill), composed)
        self.assertIn("CONTEXT.md", composed)
        self.assertIn("需求.md", composed)

    def test_plan_discipline_token_renders_real_path(self):
        base = self.skill / "templates/agents/qoder/builder.md"
        base.write_text(base.read_text(encoding="utf-8")
                        + "\n规划纪律：`{{plan_discipline}}`\n", encoding="utf-8")
        start = self.start("builder", "qoder")
        self.assertEqual(0, start.returncode, start.stderr)
        composed = Path(self.agent_log()[-1]["promptPath"]).read_text(encoding="utf-8")
        self.assertIn(str(self.skill / "references/plan-discipline.md"), composed)
        self.assertNotIn("{{", composed)

    # ---------- ④ 受保护标题 ----------

    def test_protected_heading_conflict_rc1(self):
        path = self.role_path("qoder", "builder")
        path.write_text(path.read_text(encoding="utf-8") + "\n## 引用\n项目重写引用。\n",
                        encoding="utf-8")
        rc, report = self.compose("builder", "qoder")
        self.assertEqual(1, rc)
        self.assertIn("引用", report["protectedHeadingConflicts"])
        started = self.start("builder", "qoder")
        self.assertEqual(1, started.returncode)
        self.assertEqual([], self.herdr_calls())

    def test_non_protected_duplicate_heading_warns_but_passes(self):
        self.write_role(
            "qoder", "builder",
            body="# Builder\n## 职责边界\n项目自己的职责边界。\n## 项目约定\nPROJECT_ONLY_MARKER\n",
        )
        rc, report = self.compose("builder", "qoder")
        self.assertEqual(0, rc, report)
        self.assertIn("职责边界", report["duplicateHeadings"])
        self.assertTrue(report["warnings"], report)

    def test_base_rule_pointers_required(self):
        base = self.skill / "templates/agents/qoder/builder.md"
        text = base.read_text(encoding="utf-8")
        start = text.index("## 引用")
        end = text.index("## 职责边界")
        base.write_text(text[:start] + text[end:], encoding="utf-8")
        rc, report = self.compose("builder", "qoder")
        self.assertEqual(1, rc)
        self.assertFalse(report["basePointersPresent"])
        self.assertTrue(any("规则指针" in err for err in report["errors"]), report)

    def test_base_upgrade_propagates_without_project_change(self):
        before = sha256_file(self.role_path("qoder", "builder"))
        skill2 = self.base / "skill2"
        shutil.copytree(self.skill, skill2)
        upgraded = skill2 / "templates/agents/qoder/builder.md"
        upgraded.write_text(upgraded.read_text(encoding="utf-8")
                            + "\n## 升级说明\nV2_ONLY_MARKER\n", encoding="utf-8")
        self.config["skillRoot"] = str(skill2)
        self.write_config()
        rc, report = self.compose("builder", "qoder")
        self.assertEqual(0, rc, report)
        self.assertIn("升级说明", report["baseHeadings"])
        self.assertEqual(before, sha256_file(self.role_path("qoder", "builder")))

    # ---------- ⑤ 权限字段拒绝 ----------
    def test_project_frontmatter_permission_rejected(self):
        path = self.role_path("qoder", "builder")
        text = path.read_text(encoding="utf-8")
        head, rest = text.split("---\n", 2)[1:]
        for extra in ("permission:\n  edit: allow\n",
                      "disallowedTools: []\n",
                      "tools:\n  SendMessage: true\n"):
            with self.subTest(extra=extra.split(":")[0]):
                path.write_text("---\n" + head + extra + "---\n" + rest,
                                encoding="utf-8")
                rc, report = self.compose("builder", "qoder")
                self.assertEqual(1, rc)
                self.assertTrue(any(extra.split(":")[0] in err for err in report["errors"]),
                                report)
        started = self.start("builder", "qoder")
        self.assertEqual(1, started.returncode)
        self.assertEqual([], self.herdr_calls())

    # ---------- ⑥ gates ----------

    def test_gates_append_allowed_core_gate_rejected(self):
        self.config["hooks"]["gatePolicy"]["appendGates"] = [
            {"id": "data-review", "description": "数据复核", "approver": "Captain"}
        ]
        self.write_config()
        self.write_role("qoder", "builder", gates=["data-review"])
        rc, report = self.compose("builder", "qoder")
        self.assertEqual(0, rc, report)
        self.assertEqual(CORE_GATES["builder"] + ["data-review"], report["composedGates"])

        self.write_role("qoder", "builder", gates=["three-human-gates"])
        rc, report = self.compose("builder", "qoder")
        self.assertEqual(1, rc)
        self.assertTrue(any("核心 gate" in err for err in report["errors"]), report)
        started = self.start("builder", "qoder")
        self.assertEqual(1, started.returncode)
        self.assertEqual([], self.herdr_calls())

    # ---------- ⑦ Qoder 命令构造 + 交付 + agent-log ----------

    def test_start_rejects_invalid_config_paths_before_herdr(self):
        cfg_path = self.root / ".planning/orchestration.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["hooks"]["paths"] = {"topology": "docs/orchestration/missing.md"}
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        proc = self.run_dispatch("start", "--role", "builder", "--runtime", "qoder",
                                 "--pane", "wX:p1", "--json")
        self.assertEqual(1, proc.returncode)
        self.assertIn("config.paths", proc.stderr)
        self.assertEqual([], self.herdr_calls())

    def test_list_discovers_opencode_custom_agent(self):
        self.write_role("opencode", "process-auditor", base="reviewer", custom=True,
                        capabilities=["read", "report"])
        proc = self.run_dispatch("list", "--json")
        self.assertEqual(0, proc.returncode, proc.stderr)
        roles = json.loads(proc.stdout)["roles"]
        entries = [r for r in roles if r.get("id") == "process-auditor"]
        self.assertEqual(1, len(entries), entries)
        self.assertEqual("opencode", entries[0]["runtime"])

    def test_qoder_command_delivery_and_agent_log(self):
        _, compose_report = self.compose("builder", "qoder")
        proc = self.start("builder", "qoder", pane="wX:p1")
        self.assertEqual(0, proc.returncode, proc.stderr)
        calls = self.herdr_calls()
        start_calls = [c for c in calls if c[:2] == ["agent", "start"]]
        self.assertEqual(1, len(start_calls), calls)
        argv = start_calls[0]
        self.assertEqual("proj-builder", argv[2])
        self.assertIn("--kind", argv)
        self.assertEqual("qodercli", argv[argv.index("--kind") + 1])
        self.assertEqual("wX:p1", argv[argv.index("--pane") + 1])
        self.assertNotIn("--append-system-prompt", argv)
        self.assertNotIn("--plugin-dir", argv)
        self.assertNotIn("--agents", argv)
        self.assertEqual("crew-builder", argv[argv.index("--agent") + 1])
        # Qoder 会话名与 Herdr/dispatch 启动名一致，且位于 `--` 之后（runtime 参数）
        self.assertIn("--name", argv)
        self.assertEqual(argv[2], argv[argv.index("--name") + 1])
        self.assertLess(argv.index("--"), argv.index("--name"))
        agent_path = self.root / ".qoder/agents/crew/builder.md"
        self.assertTrue(agent_path.is_file())
        agent_text = agent_path.read_text(encoding="utf-8")
        self.assertIn("name: crew-builder", agent_text)
        self.assertIn("BASE_ONLY_MARKER", agent_text)
        self.assertIn("PROJECT_ONLY_MARKER", agent_text)
        self.assertIn("## 职责边界", agent_text)
        self.assertIn("## 项目约定", agent_text)

        row = self.agent_log()[-1]
        self.assertEqual("start", row["action"])
        self.assertEqual("builder", row["role"])
        self.assertEqual("qoder", row["runtime"])
        self.assertEqual("composed-agent", row["delivery"])
        self.assertEqual(str(agent_path), row["composedPath"])
        self.assertEqual("base", row["policySource"])
        self.assertIn("templates/agents/qoder/builder.md", row["baseRef"])
        self.assertIn("2.1.0", row["baseRef"])
        self.assertEqual(compose_report["composedSha256"], row["composedSha256"])
        self.assertEqual(sha256_file(self.role_path("qoder", "builder")),
                         row["overlaySha256"])
        self.assertIn(str(self.root / ".git/crew/compose"), row["composePath"])
        composed_path = Path(row["promptPath"])
        self.assertTrue(composed_path.is_file())
        composed = composed_path.read_text(encoding="utf-8")
        self.assertIn("BASE_ONLY_MARKER", composed)
        self.assertIn("PROJECT_ONLY_MARKER", composed)
        self.assertIn("## 职责边界", composed)
        self.assertIn("## 项目约定", composed)
        self.assertNotIn("{{", composed)

    def test_start_explicit_name_used_for_herdr_and_registry(self):
        proc = self.start("builder", "qoder", name="custom-builder")
        self.assertEqual(0, proc.returncode, proc.stderr)
        starts = [c for c in self.herdr_calls() if c[:2] == ["agent", "start"]]
        self.assertEqual(1, len(starts), starts)
        self.assertEqual("custom-builder", starts[0][2])
        registry = json.loads(
            (self.root / ".git/crew/dispatch-agents.json").read_text(encoding="utf-8")
        )
        self.assertIn("custom-builder", registry)
        self.assertNotIn("proj-builder", registry)
        self.assertEqual("custom-builder", self.agent_log()[-1]["agent"])

    def test_start_records_permission_mode_in_log_and_registry(self):
        self._write_composed("qoder", "builder")
        proc = self.run_dispatch(
            "start", "--role", "builder", "--runtime", "qoder",
            "--pane", "wX:p1", "--name", "yolo-builder",
            "--permission-mode", "bypass_permissions", "--json")
        self.assertEqual(0, proc.returncode, proc.stderr)
        row = self.agent_log()[-1]
        self.assertEqual("start", row["action"])
        self.assertEqual("bypass_permissions", row["permissionMode"])
        registry = json.loads(
            (self.root / ".git/crew/dispatch-agents.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            "bypass_permissions", registry["yolo-builder"]["permissionMode"])

    def test_start_without_permission_mode_records_empty(self):
        proc = self.start("builder", "qoder")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual("", self.agent_log()[-1]["permissionMode"])

    def test_start_rejects_invalid_explicit_name_before_herdr(self):
        proc = self.start("builder", "qoder", name="Bad Name")
        self.assertEqual(1, proc.returncode)
        self.assertIn("--name", proc.stderr)
        self.assertEqual([], self.herdr_calls())

    def test_start_rejects_duplicate_explicit_name(self):
        self.herdr_agents = ["custom-builder"]
        proc = self.start("builder", "qoder", name="custom-builder")
        self.assertEqual(1, proc.returncode)
        self.assertIn("已存在", proc.stderr)
        self.assertFalse([c for c in self.herdr_calls() if c[:2] == ["agent", "start"]])

    def test_start_without_name_keeps_derived_behavior(self):
        self.config["helm"] = {"agentName": "helm-case"}
        self.write_config()
        self.write_role("qoder", "helm")
        helm = self.start("helm", "qoder")
        self.assertEqual(0, helm.returncode, helm.stdout + helm.stderr)
        starts = [c for c in self.herdr_calls() if c[:2] == ["agent", "start"]]
        self.assertEqual("helm-case", starts[-1][2])

        builder = self.start("builder", "qoder")
        self.assertEqual(0, builder.returncode, builder.stderr)
        starts = [c for c in self.herdr_calls() if c[:2] == ["agent", "start"]]
        self.assertEqual("proj-builder", starts[-1][2])

    def test_qoder_disallowed_tools_translated(self):
        self.write_role("qoder", "reviewer", body="# Reviewer\n## 项目补充\n项目补充。\n")
        proc = self.start("reviewer", "qoder", pane="wX:p2")
        self.assertEqual(0, proc.returncode, proc.stderr)
        argv = [c for c in self.herdr_calls() if c[:2] == ["agent", "start"]][0]
        tools = [argv[i + 1] for i, item in enumerate(argv) if item == "--disallowed-tools"]
        self.assertEqual(["Write", "Edit", "SendMessage"], tools)

    def test_all_qoder_bases_disallow_sendmessage(self):
        for path in sorted(QODER_TEMPLATES.glob("*.md")):
            with self.subTest(path=path.name):
                fm, _ = dispatch_mod.split_frontmatter(path.read_text(encoding="utf-8"))
                self.assertIsNotNone(fm)
                self.assertIn("SendMessage", dispatch_mod.parse_list(fm, "disallowedTools"))

    def test_all_bases_document_sendmessage_policy(self):
        for base in (QODER_TEMPLATES, OPENCODE_TEMPLATES):
            for path in sorted(base.glob("*.md")):
                with self.subTest(path=f"{base.name}/{path.name}"):
                    text = path.read_text(encoding="utf-8")
                    self.assertIn("不主动使用 SendMessage", text)
                    self.assertIn("Decline", text)
                    self.assertIn("inbox", text)

    def test_all_opencode_bases_disable_sendmessage(self):
        for path in sorted(OPENCODE_TEMPLATES.glob("*.md")):
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                frontmatter = text.split("---\n", 2)[1]
                self.assertIn("SendMessage: false", frontmatter)

    # ---------- ⑧ opencode 命令构造 + 交付 ----------

    def test_opencode_composed_agent_delivery(self):
        _, compose_report = self.compose("firstmate", "opencode")
        proc = self.start("firstmate", "opencode", pane="wX:pSRC")
        self.assertEqual(0, proc.returncode, proc.stderr)
        calls = self.herdr_calls()
        self.assertFalse([c for c in calls if c[:2] == ["pane", "split"]], calls)
        starts = [c for c in calls if c[:2] == ["agent", "start"]]
        self.assertEqual(1, len(starts), calls)
        argv = starts[0]
        self.assertEqual("opencode", argv[argv.index("--kind") + 1])
        self.assertEqual("wX:pSRC", argv[argv.index("--pane") + 1])
        self.assertEqual("crew-firstmate", argv[argv.index("--agent") + 1])
        self.assertNotIn("--name", argv)

        row = self.agent_log()[-1]
        self.assertEqual("composed-agent", row["delivery"])
        self.assertEqual(compose_report["composedSha256"], row["composedSha256"])
        agent_file = self.root / ".opencode/agent/crew-firstmate.md"
        self.assertTrue(agent_file.is_file())
        text = agent_file.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        frontmatter = text.split("---\n", 2)[1]
        self.assertIn("permission:", frontmatter)
        self.assertIn("SendMessage: false", frontmatter)
        self.assertIn("BASE_ONLY_MARKER", text)
        self.assertIn("PROJECT_ONLY_MARKER", text)
        self.assertLess(text.index("BASE_ONLY_MARKER"), text.index("PROJECT_ONLY_MARKER"))
        self.assertNotIn("{{", text)
        self.assertFalse(list((self.root / ".opencode/agent").rglob("*composed*")))
        self.assertEqual(str(agent_file), row["composedPath"])

    # ---------- ⑩ 项目零写入 / 不生成第二份 ----------

    def test_pane_split_parse_real_herdr_envelope(self):
        real = HERDR_STUB.replace(
            '{"result": {"pane_id": "wX:pNEW"}}',
            '{"id": "cli:pane:split", "result": {"pane": {"pane_id": "wX:pREAL"}}}',
        )
        stub = self.bin / "herdr"
        stub.write_text(real, encoding="utf-8")
        stub.chmod(0o755)
        proc = self.run_dispatch("pane", "split", "--pane", "wX:pSRC",
                                 "--direction", "right")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual("wX:pREAL", json.loads(proc.stdout)["pane"])

    def test_tab_create_outputs_tab_and_pane(self):
        proc = self.run_dispatch(
            "tab", "create", "--workspace", "wX", "--cwd", str(self.root),
            "--label", "总舵", "--no-focus",
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual("wX:tNEW", payload["tab"])
        self.assertEqual("wX:pNEW", payload["pane"])  # pane 键保留（start-helm.sh 兼容）
        self.assertEqual(
            [["tab", "create", "--workspace", "wX", "--cwd", str(self.root),
              "--label", "总舵", "--no-focus"]],
            self.herdr_calls(),
        )

    def test_tab_create_falls_back_to_pane_list_for_tab_id(self):
        stub = self.bin / "herdr"
        stub.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "args = sys.argv[1:]\n"
            "if args[:2] == ['tab', 'create']:\n"
            "    print(json.dumps({'result': {'root_pane': {'pane_id': 'wX:pNEW'}}}))\n"
            "elif args[:2] == ['pane', 'list']:\n"
            "    print(json.dumps({'result': {'panes': ["
            "{'pane_id': 'wX:pNEW', 'tab_id': 'wX:tLIST'}]}}))\n"
            "else:\n"
            "    print(json.dumps({'result': {'ok': True}}))\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
        proc = self.run_dispatch("tab", "create", "--workspace", "wX", "--label", "总舵")
        self.assertEqual(0, proc.returncode, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual("wX:tLIST", payload["tab"])
        self.assertEqual("wX:pNEW", payload["pane"])

    def test_tab_create_missing_tab_id_logs_partial_and_warns(self):
        stub = self.bin / "herdr"
        stub.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "args = sys.argv[1:]\n"
            "if args[:2] == ['tab', 'create']:\n"
            "    print(json.dumps({'result': {'root_pane': {'pane_id': 'wX:pNEW'}}}))\n"
            "elif args[:2] == ['pane', 'list']:\n"
            "    print(json.dumps({'result': {'panes': []}}))\n"
            "else:\n"
            "    print(json.dumps({'result': {'ok': True}}))\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
        proc = self.run_dispatch("tab", "create", "--workspace", "wX", "--label", "总舵")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn("未返回 tab_id", proc.stderr)
        self.assertIn("partial", proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertIsNone(payload["tab"])
        self.assertEqual("wX:pNEW", payload["pane"])
        row = self.agent_log()[-1]
        self.assertEqual("partial", row["result"])
        self.assertIsNone(row["tab"])

    def test_project_role_file_zero_write_and_no_second_copy(self):
        self._write_composed("qoder", "builder")
        self._write_composed("opencode", "firstmate")
        before = self.project_snapshot()
        self.assertEqual(0, self.start("builder", "qoder").returncode)
        self.assertEqual(0, self.start("firstmate", "opencode", pane="wX:pS").returncode)
        after = self.project_snapshot()
        self.assertEqual(before, after)
        self.assertEqual(1, len(list((self.root / ".qoder/agents").glob("builder*.md"))))
        self.assertFalse(list((self.root / ".qoder/agents").rglob("*composed*")))
        leaked = [
            path for path in self.root.rglob("*composed*")
            if ".git/" not in path.relative_to(self.root).as_posix()
        ]
        self.assertEqual([], leaked)

    # ---------- ⑪ fail-closed ----------

    def test_missing_skill_root_fail_closed(self):
        self.config.pop("skillRoot")
        self.write_config()
        rc, report = self.compose("builder", "qoder")
        self.assertEqual(1, rc)
        self.assertTrue(any("skillRoot" in err for err in report["errors"]), report)

    def test_unknown_token_fail_closed(self):
        base = self.skill / "templates/agents/qoder/builder.md"
        base.write_text(base.read_text(encoding="utf-8") + "\n{{unknown_token}}\n",
                        encoding="utf-8")
        rc, report = self.compose("builder", "qoder")
        self.assertEqual(1, rc)
        self.assertIn("{{unknown_token}}", report["unresolvedTokens"])

    def test_descriptor_requires_extends_and_managed_false(self):
        self.write_role("qoder", "builder", descriptor_extra={"managed": True})
        rc, report = self.compose("builder", "qoder")
        self.assertEqual(1, rc)
        self.assertTrue(any("managed" in err for err in report["errors"]), report)

        self.write_role("qoder", "builder", descriptor_extra={"extends": "builder"})
        rc, report = self.compose("builder", "qoder")
        self.assertEqual(1, rc)
        self.assertTrue(any("extends" in err for err in report["errors"]), report)

    def test_custom_role_inherits_reviewer_and_cannot_dispatch(self):
        self.write_role(
            "qoder", "process-auditor", base="reviewer", custom=True,
            capabilities=["read", "report"], body="# Process Auditor\n## 项目补充\n只读补充。\n",
        )
        self.config["hooks"]["customRoles"] = [{"id": "process-auditor", "enabled": True}]
        self.write_config()
        rc, report = self.compose("process-auditor", "qoder")
        self.assertEqual(0, rc, report)
        self.assertIn("Reviewer", report["baseHeadings"])
        self.assertIn("项目补充", report["appendedHeadings"])

        self.write_role(
            "qoder", "process-auditor", base="reviewer", custom=True,
            capabilities=["read", "report", "dispatch"],
        )
        rc, report = self.compose("process-auditor", "qoder")
        self.assertEqual(1, rc)
        self.assertTrue(any("dispatch" in err for err in report["errors"]), report)


class NotifyCommandTest(unittest.TestCase):
    """T22/FB-012/FB-015：notify 只 herdr agent prompt，不查/不写注册表，不碰 pane/tab；
    --workspace+--role 按角色命名约定与代际解析当前活跃 agent。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "external-proj"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.calls: list[list[str]] = []
        self.agents: list[dict] = []

    def fake_herdr(self, argv: list[str]) -> str:
        self.calls.append(list(argv))
        if argv[:3] == ["herdr", "agent", "list"]:
            agents = []
            for agent in self.agents:
                entry = dict(agent)
                entry.setdefault("pane_id", f"pane-{entry.get('name', 'agent')}")
                agents.append(entry)
            return json.dumps({"result": {"agents": agents}})
        if argv[:3] == ["herdr", "pane", "list"]:
            panes = [
                {"pane_id": agent.get("pane_id") or f"pane-{agent.get('name', 'agent')}",
                 "focused": bool(agent.get("focused"))}
                for agent in self.agents
            ]
            return json.dumps({"result": {"panes": panes}})
        return ""

    def dispatch_main(self, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        with mock.patch.object(dispatch_mod, "run_herdr", side_effect=self.fake_herdr):
            with redirect_stdout(out):
                code = dispatch_mod.main(["--root", str(self.root), *args])
        return code, out.getvalue()

    def agent_log(self) -> list[dict]:
        path = dispatch_mod.crew_dir(self.root) / "agent-log.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def prompt_calls(self) -> list[list[str]]:
        return [call for call in self.calls if call[:3] == ["herdr", "agent", "prompt"]]

    def test_unregistered_agent_notified_via_herdr_prompt_only(self):
        registry = dispatch_mod.registry_path(self.root)
        self.assertFalse(registry.exists())
        code, out = self.dispatch_main(
            "notify", "--agent", "case-agent", "--message", "2.2.11 已发布", "--json")
        self.assertEqual(0, code)
        # T28：literal 模式先查 herdr agent list 解析收件方 inbox；目标不在本机 →
        # 只 prompt 并显式标记未落盘。
        self.assertEqual(
            [["herdr", "agent", "prompt", "case-agent",
              "2.2.11 已发布\n" + dispatch_mod.RECEIPT_LINE]], self.prompt_calls())
        self.assertFalse(registry.exists())
        row = self.agent_log()[-1]
        self.assertEqual("notify", row["action"])
        self.assertEqual("case-agent", row["agent"])
        self.assertEqual("case-agent", row["target"])
        self.assertEqual("herdr", row["delivery"])
        self.assertEqual("literal", row["target_resolution"])
        self.assertIsNone(row["prompt_skipped"])
        self.assertTrue(row["receipt_required"])
        self.assertEqual("ok", row["result"])
        self.assertEqual("skipped", row["inbox_status"])
        self.assertIn("本机 herdr agent list", row["inbox_reason"])
        self.assertIn("ts", row)
        self.assertEqual(
            {"agent": "case-agent", "target": "case-agent", "delivery": "herdr",
             "target_resolution": "literal", "prompt_skipped": None, "notified": False,
             "receipt_required": True,
             "inbox": {"status": "skipped", "path": None,
                       "reason": "目标不在本机 herdr agent list"},
             "result": "ok"}, json.loads(out))

    def test_existing_registry_is_left_untouched(self):
        registry = dispatch_mod.registry_path(self.root)
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(
            json.dumps({"crew-helm": {"role": "helm", "runtime": "qoder"}}),
            encoding="utf-8")
        before = registry.read_bytes()
        code, out = self.dispatch_main(
            "notify", "--agent", "case-agent", "--message", "ping", "--no-require-receipt")
        self.assertEqual(0, code)
        self.assertEqual(before, registry.read_bytes())
        self.assertEqual(
            [["herdr", "agent", "prompt", "case-agent", "ping"]], self.prompt_calls())
        self.assertIn("未落收件方 inbox", out)

    def test_cli_description_lists_notify(self):
        parser = dispatch_mod.build_parser()
        self.assertIn(
            "compose/start/prompt/close/notify/status/list/tab/pane",
            parser.description)
        notify_parser = parser._subparsers._group_actions[0].choices["notify"]
        help_text = notify_parser.format_help()
        self.assertIn("notify", parser.format_help())
        self.assertIn("--workspace", help_text)
        self.assertIn("--no-require-receipt", help_text)
        self.assertIn("helm 按代际解析", help_text)
        self.assertIn("其他角色要求唯一匹配", help_text)
        self.assertIn("多实例用", help_text)

    def test_receipt_line_default_and_opt_out(self):
        code, _ = self.dispatch_main(
            "notify", "--agent", "case-agent", "--message", "默认", "--json")
        self.assertEqual(0, code)
        self.assertEqual("默认\n" + dispatch_mod.RECEIPT_LINE,
                         self.calls[-1][-1])

        code, _ = self.dispatch_main(
            "notify", "--agent", "case-agent", "--message", "关闭",
            "--no-require-receipt", "--json")
        self.assertEqual(0, code)
        self.assertEqual("关闭", self.calls[-1][-1])
        self.assertFalse(self.agent_log()[-1]["receipt_required"])

    def test_workspace_role_picks_latest_active_generation(self):
        self.agents = [
            {"name": "helm-x-2", "workspace_id": "w1", "agent_status": "done"},
            {"name": "helm-x-3", "workspace_id": "w1", "agent_status": "idle"},
            {"name": "worker", "workspace_id": "w1", "agent_status": "working"},
        ]
        code, out = self.dispatch_main(
            "notify", "--workspace", "w1", "--role", "helm",
            "--message", "请接管", "--no-require-receipt", "--json")
        self.assertEqual(0, code)
        self.assertEqual(
            [["herdr", "agent", "list"],
             ["herdr", "pane", "list"],
             ["herdr", "agent", "prompt", "helm-x-3", "请接管"]], self.calls)
        payload = json.loads(out)
        self.assertEqual("herdr", payload["delivery"])
        self.assertEqual("resolved", payload["target_resolution"])
        self.assertEqual("helm-x-3", payload["target"])
        row = self.agent_log()[-1]
        self.assertEqual("herdr", row["delivery"])
        self.assertEqual("resolved", row["target_resolution"])
        self.assertEqual("helm-x-3", row["target"])
        self.assertEqual("helm", row["role"])

    def test_other_workspace_agents_excluded(self):
        self.agents = [
            {"name": "proj-helm-9", "workspace_id": "w2", "agent_status": "idle"},
            {"name": "proj-helm-4", "workspace_id": "w1", "agent_status": "idle"},
        ]
        code, _ = self.dispatch_main(
            "notify", "--workspace", "w1", "--role", "helm",
            "--message", "hi", "--no-require-receipt", "--json")
        self.assertEqual(0, code)
        self.assertEqual([["herdr", "agent", "prompt", "proj-helm-4", "hi"]],
                         self.prompt_calls())

    def test_latest_generation_done_fails_without_prompt(self):
        self.agents = [
            {"name": "helm-x-3", "workspace_id": "w1", "agent_status": "unknown"},
            {"name": "helm-x-2", "workspace_id": "w1", "agent_status": "idle"},
        ]
        code, _ = self.dispatch_main(
            "notify", "--workspace", "w1", "--role", "helm",
            "--message", "hi", "--no-require-receipt", "--json")
        self.assertEqual(1, code)
        self.assertEqual([], self.prompt_calls())
        self.assertEqual([], self.agent_log())

    def test_missing_agent_status_not_selected(self):
        # A-S8：agent_status 缺失不视为活跃（与 done/unknown 同等排除）
        self.agents = [{"name": "helm-x-3", "workspace_id": "w1"}]
        code, _ = self.dispatch_main(
            "notify", "--workspace", "w1", "--role", "helm",
            "--message", "hi", "--no-require-receipt", "--json")
        self.assertEqual(1, code)
        self.assertEqual([], self.prompt_calls())
        with self.assertRaises(dispatch_mod.DispatchError) as ctx:
            dispatch_mod.resolve_notify_target(self.agents, "w1", "helm")
        message = str(ctx.exception)
        self.assertIn("agent_status", message)
        self.assertIn("herdr agent list", message)

    def test_none_agent_status_not_selected(self):
        # A-S8：agent_status=None 不视为活跃
        self.agents = [
            {"name": "helm-x-3", "workspace_id": "w1", "agent_status": None}]
        code, _ = self.dispatch_main(
            "notify", "--workspace", "w1", "--role", "helm",
            "--message", "hi", "--no-require-receipt", "--json")
        self.assertEqual(1, code)
        self.assertEqual([], self.prompt_calls())
        with self.assertRaises(dispatch_mod.DispatchError) as ctx:
            dispatch_mod.resolve_notify_target(self.agents, "w1", "helm")
        self.assertIn("agent_status", str(ctx.exception))

    def test_non_whitelisted_status_rejected(self):
        # S3：归一化后仅 idle/working 放行；Done/空白/未知值一律拒绝
        for status in ("Done", " ", "unknown-state", "starting"):
            with self.subTest(status=status):
                self.agents = [{"name": "helm-x-3", "workspace_id": "w1",
                                "agent_status": status}]
                code, _ = self.dispatch_main(
                    "notify", "--workspace", "w1", "--role", "helm",
                    "--message", "hi", "--no-require-receipt", "--json")
                self.assertEqual(1, code)
                self.assertEqual([], self.prompt_calls())
                with self.assertRaises(dispatch_mod.DispatchError) as ctx:
                    dispatch_mod.resolve_notify_target(self.agents, "w1", "helm")
                self.assertIn("herdr agent list", str(ctx.exception))

    def test_status_normalized_whitelist_accepted(self):
        # S3：大小写/空白归一化后 idle/working 正常放行
        for status in (" Working ", "IDLE"):
            with self.subTest(status=status):
                self.agents = [{"name": "helm-x-3", "workspace_id": "w1",
                                "agent_status": status}]
                code, _ = self.dispatch_main(
                    "notify", "--workspace", "w1", "--role", "helm",
                    "--message", "hi", "--no-require-receipt", "--json")
                self.assertEqual(0, code)
                self.assertEqual("helm-x-3", self.agent_log()[-1]["target"])

    def test_no_candidates_fails(self):
        self.agents = [
            {"name": "worker", "workspace_id": "w1", "agent_status": "idle"}]
        code, _ = self.dispatch_main(
            "notify", "--workspace", "w1", "--role", "helm",
            "--message", "hi", "--no-require-receipt", "--json")
        self.assertEqual(1, code)
        self.assertEqual([], self.prompt_calls())

    def test_helm_with_multiple_workspaces_requires_explicit_workspace(self):
        self.agents = [
            {"name": "helm-a-1", "workspace_id": "w1", "agent_status": "idle"},
            {"name": "helm-b-1", "workspace_id": "w2", "agent_status": "idle"},
        ]
        code, _ = self.dispatch_main(
            "notify", "--role", "helm", "--message", "hi", "--json")
        self.assertEqual(1, code)
        self.assertEqual([], self.prompt_calls())

    def test_workspace_without_role_rejected(self):
        code, _ = self.dispatch_main(
            "notify", "--workspace", "w1", "--message", "hi", "--json")
        self.assertEqual(1, code)
        self.assertEqual([], self.calls)

    def test_non_helm_multiple_instances_rejected(self):
        # S1：非 helm 角色不做代际挑选；同 workspace 多匹配 → rc=1 列候选
        self.agents = [
            {"name": "req2-main-firstmate-3", "workspace_id": "wQ", "agent_status": "working"},
            {"name": "skill-pilot-firstmate", "workspace_id": "wQ", "agent_status": "working"},
        ]
        code, _ = self.dispatch_main(
            "notify", "--workspace", "wQ", "--role", "firstmate",
            "--message", "hi", "--no-require-receipt")
        self.assertEqual(1, code)
        self.assertEqual([], self.prompt_calls())
        with self.assertRaises(dispatch_mod.DispatchError) as ctx:
            dispatch_mod.resolve_notify_target(self.agents, "wQ", "firstmate")
        message = str(ctx.exception)
        self.assertIn("req2-main-firstmate-3", message)
        self.assertIn("skill-pilot-firstmate", message)
        self.assertIn("--agent", message)

    def test_non_helm_unique_match_resolves(self):
        self.agents = [
            {"name": "req2-main-firstmate-3", "workspace_id": "wQ", "agent_status": "working"},
            {"name": "skill-pilot-firstmate", "workspace_id": "w1", "agent_status": "working"},
        ]
        code, _ = self.dispatch_main(
            "notify", "--workspace", "wQ", "--role", "firstmate",
            "--message", "hi", "--no-require-receipt")
        self.assertEqual(0, code)
        self.assertEqual(
            [["herdr", "agent", "prompt", "req2-main-firstmate-3", "hi"]],
            self.prompt_calls())

    def test_latest_generation_blocked_rejected(self):
        # S2：最新代际 blocked → rc=1，herdr prompt 不调用
        self.agents = [
            {"name": "helm-x-4", "workspace_id": "w1", "agent_status": "blocked"},
        ]
        code, _ = self.dispatch_main(
            "notify", "--workspace", "w1", "--role", "helm",
            "--message", "hi", "--no-require-receipt")
        self.assertEqual(1, code)
        self.assertEqual([], self.prompt_calls())
        with self.assertRaises(dispatch_mod.DispatchError) as ctx:
            dispatch_mod.resolve_notify_target(self.agents, "w1", "helm")
        self.assertIn("blocked", str(ctx.exception))
        self.assertIn("herdr 会拒绝投递", str(ctx.exception))

    def test_receipt_line_not_duplicated(self):
        # S6：消息已以回执行结尾时不重复追加
        code, _ = self.dispatch_main(
            "notify", "--agent", "case-agent",
            "--message", "已处理\n" + dispatch_mod.RECEIPT_LINE, "--json")
        self.assertEqual(0, code)
        sent = self.calls[-1][-1]
        self.assertEqual(1, sent.count(dispatch_mod.RECEIPT_LINE))


if __name__ == "__main__":
    unittest.main()
