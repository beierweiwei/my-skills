#!/usr/bin/env python3
"""2.2.25 T60：opencode YOLO 口径纠正 + dispatch 默认权限模式 + 文档去 --auto。

覆盖（红→绿）：
- 生产目录（references/、templates/、scripts/ 排 tests）无 `--auto` 派单指引；
  D8/cheatsheet/opencode firstmate 口径为「opencode 经角色模板 permission 块承担」。
- crew-claude.py run --help description 不再出现「env 传参」，且 --model 可见。
- pane-audit opencode 工位静态校验：组合产物 frontmatter 含 permission 块，缺则告警。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
_SCRIPTS = _TESTS_DIR.parent
SKILL = _SCRIPTS.parent

_pa_spec = importlib.util.spec_from_file_location(
    "pane_audit_t60", _SCRIPTS / "pane-audit.py")
pane_audit = importlib.util.module_from_spec(_pa_spec)
sys.modules["pane_audit_t60"] = pane_audit
_pa_spec.loader.exec_module(pane_audit)


def read(rel: str) -> str:
    return (SKILL / rel).read_text(encoding="utf-8")


class ProductionDocsNoAutoFlagTest(unittest.TestCase):
    """B1：整条工具链都不存在 `--auto`，生产文档不得再指挥它。"""

    def test_no_auto_flag_in_production_dirs(self):
        roots = [SKILL / "references", SKILL / "templates", SKILL / "scripts"]
        offenders: list[str] = []
        for root in roots:
            for path in sorted(root.rglob("*")):
                if not path.is_file() or path.suffix not in (".md", ".py"):
                    continue
                if path.suffix == ".py" and path.parent.name == "tests":
                    continue
                if "--auto" in path.read_text(encoding="utf-8"):
                    offenders.append(path.relative_to(SKILL).as_posix())
        self.assertEqual([], offenders, offenders)

    def test_d8_points_to_template_permission(self):
        rules = read("references/crew-rules.md")
        self.assertIn("**D8 YOLO 派单**", rules)
        self.assertIn("角色模板", rules)
        self.assertIn("permission", rules)
        self.assertIn("agents.permissionMode", rules)

    def test_cheatsheet_points_to_template_permission(self):
        text = read("references/herdr-cheatsheet.md")
        self.assertIn("角色模板", text)
        self.assertIn("agents.permissionMode", text)

    def test_opencode_firstmate_has_no_cli_permission_flag(self):
        text = read("templates/agents/opencode/firstmate.md")
        self.assertIn("D8 YOLO 派单", text)
        self.assertIn("角色模板", text)
        self.assertIn("permission", text)
        self.assertIn("不带任何权限旗标", text)
        self.assertIn("scout/runner/planner", text)
        self.assertNotIn("--permission-mode bypass_permissions", text)


class CrewClaudeHelpTest(unittest.TestCase):
    def run_help(self, *args: str) -> str:
        proc = subprocess.run(
            [sys.executable, str(_SCRIPTS / "crew-claude.py"), *args],
            capture_output=True, text=True,
        )
        output = proc.stdout + proc.stderr
        self.assertEqual(0, proc.returncode, output)
        return output

    def test_help_description_matches_model_flag(self):
        main_help = self.run_help("--help")
        run_help = self.run_help("run", "--help")
        for output in (main_help, run_help):
            self.assertNotIn("env 传参", output)
        self.assertIn("--model", main_help)


class OpencodePermissionAuditTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def write_agent(self, rel: str, frontmatter: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\n{frontmatter}\n---\n# Agent\n", encoding="utf-8")
        return path

    def test_kind_registered(self):
        self.assertIn("opencode-permission-missing", pane_audit.KIND_TEXT)

    def test_detects_missing_and_unreadable(self):
        good = self.write_agent(
            ".opencode/agent/crew-builder.md",
            "description: builder\nmode: primary\npermission:\n  bash: allow",
        )
        bad = self.write_agent(
            ".opencode/agent/crew-firstmate.md",
            "description: firstmate\nmode: primary",
        )
        registry = {
            "oc-good": {"runtime": "opencode", "composedPath": str(good)},
            "oc-bad": {"runtime": "opencode", "composedPath": str(bad)},
            "oc-gone": {"runtime": "opencode", "composedPath": str(self.root / "nope.md")},
            "q-builder": {"runtime": "qoder", "composedPath": str(bad)},
        }
        missing = pane_audit.opencode_permission_missing_entries(
            registry, self.root)
        self.assertEqual(["oc-bad", "oc-gone"], [name for name, _ in missing])

    def test_relative_composed_path_resolves_against_root(self):
        self.write_agent(
            ".opencode/agent/crew-custom.md",
            "description: custom\npermission:\n  edit: allow",
        )
        registry = {
            "oc-relative": {
                "runtime": "opencode",
                "composedPath": ".opencode/agent/crew-custom.md",
            },
        }
        self.assertEqual(
            [],
            pane_audit.opencode_permission_missing_entries(registry, self.root),
        )

    def test_non_dict_registry_is_ignored(self):
        self.assertEqual(
            [], pane_audit.opencode_permission_missing_entries(None, self.root))
        self.assertEqual(
            [], pane_audit.opencode_permission_missing_entries([], self.root))

    def test_audit_emits_finding(self):
        snapshot = {"tabs": [], "panes": [], "agents": []}
        findings = pane_audit.audit(
            snapshot, opencode_entries=[("oc-bad", "/tmp/crew-bad.md")])
        hits = [f for f in findings
                if f.kind == "opencode-permission-missing"]
        self.assertEqual(1, len(hits), findings)
        self.assertEqual("oc-bad", hits[0].target)
        self.assertIn("permission", hits[0].next_step)
        self.assertIsNone(hits[0].action)

    def test_audit_clean_without_entries(self):
        snapshot = {"tabs": [], "panes": [], "agents": []}
        findings = pane_audit.audit(snapshot, opencode_entries=[])
        self.assertEqual(
            [], [f for f in findings
                 if f.kind == "opencode-permission-missing"])


if __name__ == "__main__":
    unittest.main()
