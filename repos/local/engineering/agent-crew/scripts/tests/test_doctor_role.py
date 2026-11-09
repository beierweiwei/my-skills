#!/usr/bin/env python3
"""doctor 角色检查（S9-02/S9-16 B/D/G3/S9-17 A）：frontmatter/descriptor/inherit/compose/gates/delivery/names。"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from doctor_fixtures import INIT, ProjectFixture, strip_role_meta

_SCRIPTS = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("doctor_under_test", _SCRIPTS / "doctor.py")
doctor_mod = importlib.util.module_from_spec(_spec)
sys.modules["doctor_under_test"] = doctor_mod
_spec.loader.exec_module(doctor_mod)


def inject_frontmatter(path: Path, extra: str) -> None:
    text = path.read_text(encoding="utf-8")
    parts = text.split("---\n", 2)
    assert len(parts) == 3, "fixture frontmatter 缺失"
    path.write_text("---\n" + parts[1] + extra + "---\n" + parts[2], encoding="utf-8")


class DoctorRoleTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = ProjectFixture(Path(self._tmp.name))

    def test_role_frontmatter_legal_pass(self):
        rc, report = self.fx.doctor_json("--only", "role.frontmatter")
        self.assertEqual(0, rc, report)
        self.assertEqual("pass", self.fx.item(report, "role.frontmatter")["status"])

    def test_role_frontmatter_permission_fields_fail(self):
        path = self.fx.role_path("qoder", "reviewer")
        original = path.read_text(encoding="utf-8")
        for field in ("permission:\n  edit: allow\n", "tools:\n  write: true\n",
                      "disallowedTools: [Write, Edit]\n"):
            with self.subTest(field=field.split(":")[0]):
                path.write_text(original, encoding="utf-8")
                inject_frontmatter(path, field)
                rc, report = self.fx.doctor_json("--only", "role.frontmatter")
                self.assertEqual(1, rc, report)
                item = self.fx.item(report, "role.frontmatter")
                self.assertEqual("fail", item["status"])
                self.assertIn(field.split(":")[0], item["detail"])

    def test_role_compose_legal_pass(self):
        rc, report = self.fx.doctor_json("--only", "role.compose")
        self.assertEqual(0, rc, report)
        self.assertEqual("pass", self.fx.item(report, "role.compose")["status"])

    def test_role_compose_protected_heading_fail(self):
        path = self.fx.role_path("qoder", "builder")
        path.write_text(path.read_text(encoding="utf-8") + "\n## 引用\n项目重写引用。\n",
                        encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "role.compose")
        self.assertEqual(1, rc, report)
        item = self.fx.item(report, "role.compose")
        self.assertEqual("fail", item["status"])
        self.assertIn("引用", item["detail"])

    def test_role_compose_unresolved_tokens_fail(self):
        self.fx.write_role(
            "qoder", "builder",
            body="# Builder\n## 项目补充\n{{unknown_token}}\n",
        )
        rc, report = self.fx.doctor_json("--only", "role.compose")
        self.assertEqual(1, rc, report)
        self.assertIn("unknown_token", self.fx.item(report, "role.compose")["detail"])

    def test_role_compose_residual_skill_root_token_fails(self):
        class FakeOutcome:
            ok = True
            errors: list[str] = []
            report = {
                "unresolvedTokens": [], "residualSkillRootTokens": ["{{skill_root}}"],
                "protectedHeadingConflicts": [], "basePointersPresent": True,
                "composedSha256": "x" * 64,
            }

        doctor = doctor_mod.Doctor(self.fx.root, "dev", False)
        with mock.patch.object(doctor_mod.dispatch_lib, "build_compose",
                               return_value=FakeOutcome()):
            result = doctor.check_role_compose()
        self.assertEqual("fail", result.status)
        self.assertIn("residualSkillRootTokens", result.detail)

    def test_role_compose_base_pointer_missing_fail(self):
        base = self.fx.skill / "templates/agents/qoder/builder.md"
        text = base.read_text(encoding="utf-8")
        start = text.index("## 引用")
        end = text.index("## 职责边界")
        base.write_text(text[:start] + text[end:], encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "role.compose")
        self.assertEqual(1, rc, report)
        self.assertIn("规则指针", self.fx.item(report, "role.compose")["detail"])

    def test_role_gates_append_ok_core_gate_and_dispatch_cap_fail(self):
        self.fx.config["hooks"]["gatePolicy"]["appendGates"] = [
            {"id": "data-review", "description": "数据复核", "approver": "Captain"}
        ]
        self.fx.write_config()
        self.fx.write_role("qoder", "builder", gates=["data-review"])
        rc, report = self.fx.doctor_json("--only", "role.gates")
        self.assertEqual(0, rc, report)

        self.fx.write_role("qoder", "builder", gates=["three-human-gates"])
        rc, report = self.fx.doctor_json("--only", "role.gates")
        self.assertEqual(1, rc, report)
        self.assertIn("核心 gate", self.fx.item(report, "role.gates")["detail"])

        self.fx.write_role(
            "qoder", "process-auditor", base="reviewer", custom=True,
            capabilities=["read", "report", "dispatch"],
        )
        rc, report = self.fx.doctor_json("--only", "role.gates")
        self.assertEqual(1, rc, report)
        self.assertIn("dispatch", self.fx.item(report, "role.gates")["detail"])

    def test_role_names_duplicate_custom_core_fail(self):
        self.fx.write_role("qoder", "builder", custom=True)
        rc, report = self.fx.doctor_json("--only", "role.names")
        self.assertEqual(1, rc, report)
        self.assertEqual("fail", self.fx.item(report, "role.names")["status"])

    def test_role_delivery_skip_pass_and_fail(self):
        rc, report = self.fx.doctor_json("--only", "role.delivery")
        self.assertEqual(0, rc, report)
        self.assertEqual("skip", self.fx.item(report, "role.delivery")["status"])

        _, compose = self.fx.compose("builder", "qoder")
        run_dir = self.fx.root / ".git/crew/compose/run-1"
        run_dir.mkdir(parents=True)
        prompt = run_dir / "qoder/composed.md"
        prompt.parent.mkdir()
        prompt.write_text("BASE_ONLY_MARKER\nPROJECT_ONLY_MARKER\n", encoding="utf-8")
        row = {
            "ts": "2026-11-09T00:00:00Z", "action": "start", "role": "builder",
            "runtime": "qoder", "agent": "proj-builder", "delivery": "qoder-inline",
            "baseRef": "2.1.0:templates/agents/qoder/builder.md",
            "composedSha256": compose["composedSha256"],
            "composePath": str(run_dir), "promptPath": str(prompt),
        }
        self.fx.write_agent_log([row])
        rc, report = self.fx.doctor_json("--only", "role.delivery")
        self.assertEqual(0, rc, report)
        self.assertEqual("pass", self.fx.item(report, "role.delivery")["status"])

        bad = dict(row, composedSha256="0" * 64)
        self.fx.write_agent_log([bad])
        rc, report = self.fx.doctor_json("--only", "role.delivery")
        self.assertEqual(1, rc, report)

        self.fx.write_agent_log([row])
        leak = self.fx.root / ".qoder/agents/builder-composed.md"
        leak.write_text("leak\n", encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "role.delivery")
        self.assertEqual(1, rc, report)
        self.assertIn("composed", self.fx.item(report, "role.delivery")["detail"])

        leak.unlink()
        outside = dict(row, composePath="/tmp/not-crew-compose")
        self.fx.write_agent_log([outside])
        rc, report = self.fx.doctor_json("--only", "role.delivery")
        self.assertEqual(1, rc, report)
        self.assertIn("composePath", self.fx.item(report, "role.delivery")["detail"])

    def test_role_delivery_plugin_dir_pass_and_missing_plugin_fail(self):
        _, compose = self.fx.compose("builder", "qoder")
        run_dir = self.fx.root / ".git/crew/compose/run-plugin"
        plugin_dir = run_dir / "qoder-plugin"
        (plugin_dir / ".qoder-plugin").mkdir(parents=True)
        (plugin_dir / ".qoder-plugin/plugin.json").write_text(
            json.dumps({"name": "crew-run", "version": "0.1.0",
                        "agents": ["./agents/builder.md"]}), encoding="utf-8")
        (plugin_dir / "agents").mkdir()
        (plugin_dir / "agents/builder.md").write_text(
            "---\nname: builder\n---\nBASE_ONLY_MARKER\n", encoding="utf-8")
        prompt = run_dir / "qoder/composed.md"
        prompt.parent.mkdir()
        prompt.write_text("BASE_ONLY_MARKER\nPROJECT_ONLY_MARKER\n", encoding="utf-8")
        row = {
            "ts": "2026-11-09T00:00:00Z", "action": "start", "role": "builder",
            "runtime": "qoder", "agent": "proj-builder", "delivery": "plugin-dir",
            "baseRef": "2.1.0:templates/agents/qoder/builder.md",
            "composedSha256": compose["composedSha256"],
            "composePath": str(run_dir), "promptPath": str(prompt),
            "pluginPath": str(plugin_dir), "pluginAgent": "crew-run:builder",
            "agentFile": str(plugin_dir / "agents/builder.md"),
        }
        self.fx.write_agent_log([row])
        rc, report = self.fx.doctor_json("--only", "role.delivery")
        self.assertEqual(0, rc, report)
        self.assertEqual("pass", self.fx.item(report, "role.delivery")["status"])

        (plugin_dir / ".qoder-plugin/plugin.json").unlink()
        rc, report = self.fx.doctor_json("--only", "role.delivery")
        self.assertEqual(1, rc, report)
        self.assertIn("plugin", self.fx.item(report, "role.delivery")["detail"])

    def test_s9_02_three_group_migration(self):
        legacy_bodies = {
            "qoder/reviewer": "# Reviewer\n\n## 项目职责\n\n- 逐行核对评审意见并给出阻断/建议。\n- 正文保持 legacy 原样，不抽取。\n",
            "qoder/process-auditor": "# Process Auditor\n\n## 项目职责\n\n- 逐行核对评审意见并给出阻断/建议。\n- 正文保持 legacy 原样，不抽取。\n",
            "opencode/firstmate": "# First Mate（大副）\n\n## 项目职责\n\n- 逐行核对评审意见并给出阻断/建议。\n- 正文保持 legacy 原样，不抽取。\n",
        }
        self.fx.config["hooks"]["customRoles"] = [
            {"id": "process-auditor", "enabled": True}
        ]
        self.fx.config["roles"]["enabled"].append("process-auditor")
        self.fx.write_config()
        paths = {}
        for key, body in legacy_bodies.items():
            runtime, role_id = key.split("/")
            custom = role_id == "process-auditor"
            base = "reviewer" if role_id == "process-auditor" else role_id
            path = self.fx.write_role(runtime, role_id, base=base, custom=custom, body=body)
            legacy = path.read_text(encoding="utf-8")
            paths[key] = (path, legacy, body)
        for key, (path, legacy, body) in paths.items():
            self.assertEqual(body.strip(), strip_role_meta(legacy).strip(), key)
            for item_id in ("role.frontmatter", "role.inherit", "role.compose", "role.gates"):
                rc, report = self.fx.doctor_json("--only", item_id)
                self.assertEqual(0, rc, (key, item_id, report))
        hashes = {key: path.read_bytes() for key, (path, _, _) in paths.items()}
        proc = self.fx.run(INIT, "--check")
        self.assertIn(proc.returncode, (0, 1, 2), proc.stderr)
        for key, (path, _, _) in paths.items():
            self.assertEqual(hashes[key], path.read_bytes(), key)


if __name__ == "__main__":
    unittest.main()
