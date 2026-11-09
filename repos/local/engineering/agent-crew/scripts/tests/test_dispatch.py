#!/usr/bin/env python3
"""dispatch.py 行为契约测试（v2.1 切片 A：dispatch + 继承组合）。

覆盖：
① 组合顺序 append-only（base 先、项目后；不做 section 合并；H1 各一个）；
② 真实含 `# Builder` 的技能基类（从技能仓 templates 复制）；
③ token 全部渲染为实际路径，组合文本零 `{{...}}` 残留；
④ 受保护标题冲突 rc=1（`## 引用`）；
⑤ 项目 frontmatter 权限/工具字段拒绝；
⑥ gates 只追加；列核心 gate → rc=1；
⑦ Qoder 命令构造（--agents / --append-system-prompt / --disallowed-tools）与 delivery；
⑧ opencode 命令构造（OPENCODE_CONFIG_DIR pane + config-dir 组合 agent 文件）与 delivery；
⑨ agent-log 字段（delivery/composedSha256/baseRef/overlaySha256/composePath）；
⑩ 项目角色文件零写入、不生成第二份角色文件；
⑪ fail-closed：缺 skillRoot、未知 token、extends/managed 非法、自定义角色越权。

运行：python3 -m unittest discover -s scripts/tests
"""

from __future__ import annotations

import hashlib
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
    print(json.dumps({"result": {"agents": []}}))
elif args[:2] == ["pane", "split"]:
    print(json.dumps({"result": {"pane_id": "wX:pNEW"}}))
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

    def start(self, role: str, runtime: str, pane: str = "wX:p1") -> subprocess.CompletedProcess:
        return self.run_dispatch(
            "start", "--role", role, "--runtime", runtime, "--pane", pane, "--json",
        )

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
        self.assertEqual("qoder-inline", report["delivery"])

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
        path.write_text("---\n" + head + "permission:\n  edit: allow\n---\n" + rest,
                        encoding="utf-8")
        rc, report = self.compose("builder", "qoder")
        self.assertEqual(1, rc)
        self.assertTrue(any("permission" in err for err in report["errors"]), report)
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
        self.assertIn("--agents", argv)
        inline = json.loads(argv[argv.index("--agents") + 1])
        self.assertIn("builder", inline)
        self.assertIn("BASE_ONLY_MARKER", inline["builder"]["prompt"])
        self.assertNotIn("PROJECT_ONLY_MARKER", inline["builder"]["prompt"])
        append_text = argv[argv.index("--append-system-prompt") + 1]
        self.assertIn("PROJECT_ONLY_MARKER", append_text)
        self.assertIn("## 项目约定", append_text)

        row = self.agent_log()[-1]
        self.assertEqual("start", row["action"])
        self.assertEqual("builder", row["role"])
        self.assertEqual("qoder", row["runtime"])
        self.assertEqual("qoder-inline", row["delivery"])
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

    def test_qoder_disallowed_tools_translated(self):
        self.write_role("qoder", "reviewer", body="# Reviewer\n## 项目补充\n项目补充。\n")
        proc = self.start("reviewer", "qoder", pane="wX:p2")
        self.assertEqual(0, proc.returncode, proc.stderr)
        argv = [c for c in self.herdr_calls() if c[:2] == ["agent", "start"]][0]
        tools = [argv[i + 1] for i, item in enumerate(argv) if item == "--disallowed-tools"]
        self.assertEqual(["Write", "Edit"], tools)

    # ---------- ⑧ opencode 命令构造 + 交付 ----------

    def test_opencode_config_dir_delivery_and_composed_file(self):
        _, compose_report = self.compose("firstmate", "opencode")
        proc = self.start("firstmate", "opencode", pane="wX:pSRC")
        self.assertEqual(0, proc.returncode, proc.stderr)
        calls = self.herdr_calls()
        splits = [c for c in calls if c[:2] == ["pane", "split"]]
        self.assertEqual(1, len(splits), calls)
        env_values = [splits[0][i + 1] for i, item in enumerate(splits[0]) if item == "--env"]
        self.assertTrue(any(v.startswith("OPENCODE_CONFIG_DIR=") for v in env_values), splits)
        starts = [c for c in calls if c[:2] == ["agent", "start"]]
        self.assertEqual(1, len(starts), calls)
        argv = starts[0]
        self.assertEqual("opencode", argv[argv.index("--kind") + 1])
        self.assertEqual("wX:pNEW", argv[argv.index("--pane") + 1])
        self.assertEqual("firstmate", argv[argv.index("--agent") + 1])

        row = self.agent_log()[-1]
        self.assertEqual("opencode-config-dir", row["delivery"])
        self.assertEqual(compose_report["composedSha256"], row["composedSha256"])
        compose_root = Path(row["composePath"])
        self.assertIn(str(self.root / ".git/crew/compose"), str(compose_root))
        agent_file = compose_root / "opencode/agent/firstmate.md"
        self.assertTrue(agent_file.is_file())
        text = agent_file.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        frontmatter = text.split("---\n", 2)[1]
        self.assertIn("permission:", frontmatter)  # 权限只复制基类
        self.assertIn("BASE_ONLY_MARKER", text)
        self.assertIn("PROJECT_ONLY_MARKER", text)
        self.assertLess(text.index("BASE_ONLY_MARKER"), text.index("PROJECT_ONLY_MARKER"))
        self.assertNotIn("{{", text)
        self.assertFalse(list((self.root / ".opencode/agent").rglob("*composed*")))

    # ---------- ⑩ 项目零写入 / 不生成第二份 ----------

    def test_opencode_parse_real_herdr_split_envelope(self):
        # herdr 0.9.5 实际输出：根 `id=cli:pane:split`，真实 pane 在 result.pane.pane_id
        real = HERDR_STUB.replace(
            '{"result": {"pane_id": "wX:pNEW"}}',
            '{"id": "cli:pane:split", "result": {"pane": {"pane_id": "wX:pREAL"}}}',
        )
        stub = self.bin / "herdr"
        stub.write_text(real, encoding="utf-8")
        stub.chmod(0o755)
        proc = self.start("firstmate", "opencode", pane="wX:pSRC")
        self.assertEqual(0, proc.returncode, proc.stderr)
        starts = [c for c in self.herdr_calls() if c[:2] == ["agent", "start"]]
        self.assertEqual(1, len(starts), starts)
        self.assertEqual("wX:pREAL", starts[0][starts[0].index("--pane") + 1])

    def test_project_role_file_zero_write_and_no_second_copy(self):
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


if __name__ == "__main__":
    unittest.main()
