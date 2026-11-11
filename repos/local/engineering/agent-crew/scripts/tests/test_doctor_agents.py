#!/usr/bin/env python3
"""doctor AGENTS/指针/重复句检查（S9-13/S9-17 A/D）：agents.block/docs.pointers/agents.markers/docs.duplicate。"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from doctor_fixtures import MARK_END, MARK_START, ProjectFixture


class DoctorAgentsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = ProjectFixture(Path(self._tmp.name))

    def test_agents_block_and_pointers_pass(self):
        for item_id in ("agents.block", "docs.pointers", "agents.markers"):
            rc, report = self.fx.doctor_json("--only", item_id)
            self.assertEqual(0, rc, (item_id, report))
            self.assertEqual("pass", self.fx.item(report, item_id)["status"], item_id)

    def test_skill_root_stale_fails_block_and_pointers(self):
        skill2 = self.fx.base / "skill2"
        shutil.copytree(self.fx.skill, skill2)
        self.fx.config["skillRoot"] = str(skill2)
        self.fx.write_config()
        rc, report = self.fx.doctor_json("--only", "agents.block")
        self.assertEqual(1, rc, report)
        self.assertIn("stale", self.fx.item(report, "agents.block")["detail"])
        rc, report = self.fx.doctor_json("--only", "docs.pointers")
        self.assertEqual(1, rc, report)
        self.assertIn("stale", self.fx.item(report, "docs.pointers")["detail"])

    def test_descriptive_block_pass_anchor_missing_and_skill_root_change(self):
        block = "\n".join([
            "## 协作编排",
            "- 技能根：`.planning/orchestration.json` 的 `skillRoot`。",
            "- 规则：`references/crew-rules.md`（D1–D6）。",
            "- 验收：`references/crew-acceptance.md`（A1–A5）。",
        ])
        self.fx.write_agents(block=block)
        self.fx.write_manifest()
        for item_id in ("agents.block", "docs.pointers"):
            rc, report = self.fx.doctor_json("--only", item_id)
            self.assertEqual(0, rc, (item_id, report))
            self.assertEqual("pass", self.fx.item(report, item_id)["status"], item_id)

        # 改 skillRoot 后不重渲染：描述性块不 stale
        skill2 = self.fx.base / "skill2"
        shutil.copytree(self.fx.skill, skill2)
        self.fx.config["skillRoot"] = str(skill2)
        self.fx.write_config()
        for item_id in ("agents.block", "docs.pointers"):
            rc, report = self.fx.doctor_json("--only", item_id)
            self.assertEqual(0, rc, (item_id, report))
            self.assertEqual("pass", self.fx.item(report, item_id)["status"], item_id)

        # 缺技能根锚点 → stale，并给可执行 next
        self.fx.write_agents(block="## 协作编排\n- 规则：`references/crew-rules.md`（D1–D6）。\n")
        self.fx.write_manifest()
        rc, report = self.fx.doctor_json("--only", "agents.block")
        self.assertEqual(1, rc, report)
        item = self.fx.item(report, "agents.block")
        self.assertIn("锚点", item["detail"])
        self.assertIn("refresh-block", item["next"])
        rc, report = self.fx.doctor_json("--only", "docs.pointers")
        self.assertEqual(1, rc, report)
        self.assertIn("stale", self.fx.item(report, "docs.pointers")["detail"])

    def test_unresolved_token_in_role_fails_pointers(self):
        path = self.fx.role_path("qoder", "builder")
        path.write_text(path.read_text(encoding="utf-8") + "\n{{doc_req}}\n", encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "docs.pointers")
        self.assertEqual(1, rc, report)
        self.assertIn("{{doc_req}}", self.fx.item(report, "docs.pointers")["detail"])

    def test_agents_markers_pending_and_duplicate(self):
        (self.fx.root / "docs/crew/AGENTS-block.pending.md").write_text(
            MARK_START + "\n## 编排\n" + MARK_END + "\n", encoding="utf-8")
        (self.fx.root / "AGENTS.md").write_text("# AGENTS\n\n无标记项目。\n", encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "agents.markers")
        self.assertEqual(2, rc, report)
        self.assertEqual("pending", self.fx.item(report, "agents.markers")["status"])

        self.fx.write_agents(outside="# AGENTS\n\n## 协作编排\n\n## 协作编排\n")
        rc, report = self.fx.doctor_json("--only", "agents.duplicate")
        self.assertEqual(1, rc, report)
        self.assertIn("协作编排", self.fx.item(report, "agents.duplicate")["detail"])

    def test_docs_duplicate_rule_line_fails(self):
        line = "- 这是一条长度超过三十个字符的项目规则句，用来验证跨文档重复检测不会漏报。"
        self.fx.write_agents(block="## 协作编排\n" + line)
        ext = self.fx.root / "docs/crew/project-ext/builder.md"
        ext.write_text("# Builder 扩展\n\n" + line + "\n", encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "docs.duplicate")
        self.assertEqual(1, rc, report)
        item = self.fx.item(report, "docs.duplicate")
        self.assertEqual("fail", item["status"])
        self.assertIn("project-ext", item["detail"])


class DoctorAgentsBudgetTest(unittest.TestCase):
    """agents.budget：体量非阻断 warn（阈值 20KB / 180 行 / 自有段 65% 且 >16KB）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = ProjectFixture(Path(self._tmp.name))

    def write_budget_agents(self, total_bytes: int, total_lines: int,
                            block_bytes: int) -> None:
        """构造精确 总字节/行数/受管块字节 的 AGENTS.md 并同步 manifest。"""
        block_head = "## 协作编排\n"
        filler = block_bytes - 2 - len(block_head.encode("utf-8"))
        self.assertGreaterEqual(filler, 0)
        block = block_head + "b" * filler
        suffix = "\n" + MARK_START + "\n" + block + "\n" + MARK_END + "\n"
        fixed = "# AGENTS\n\n" + suffix
        pad_bytes = total_bytes - len(fixed.encode("utf-8"))
        pad_lines = total_lines - len(fixed.splitlines())
        self.assertGreater(pad_lines, 0)
        base, rem = divmod(pad_bytes, pad_lines)
        self.assertGreaterEqual(base, 1)
        pad = "".join(
            "x" * (base - 1 + (1 if index < rem else 0)) + "\n"
            for index in range(pad_lines)
        )
        text = fixed[: len("# AGENTS\n\n")] + pad + suffix
        self.assertEqual(total_bytes, len(text.encode("utf-8")))
        self.assertEqual(total_lines, len(text.splitlines()))
        self.assertEqual(
            block_bytes,
            len(text[text.index(MARK_START) + len(MARK_START):text.index(MARK_END)]
                .encode("utf-8")),
        )
        (self.fx.root / "AGENTS.md").write_text(text, encoding="utf-8")
        self.fx.write_manifest()

    def budget(self) -> tuple[int, dict]:
        return self.fx.doctor_json("--only", "agents.budget")

    def test_repo_scale_passes(self):
        # 本仓实测量级：14,621B / 149 行 / 受管块 5,633B / 自有段 61.5%
        self.write_budget_agents(14621, 149, 5633)
        rc, report = self.budget()
        self.assertEqual(0, rc, report)
        item = self.fx.item(report, "agents.budget")
        self.assertEqual("pass", item["status"])
        for value in ("14621", "149", "5633", "61.5%"):
            self.assertIn(value, item["detail"])

    def test_bytes_and_lines_warn_with_boundary(self):
        cases = (
            ("bytes-20480", 20480, 149, 10000, "pass"),
            ("bytes-20481", 20481, 149, 10000, "warn"),
            ("lines-180", 4000, 180, 300, "pass"),
            ("lines-181", 4000, 181, 300, "warn"),
        )
        for name, total_bytes, total_lines, owned_bytes, expected in cases:
            with self.subTest(case=name):
                self.fx = ProjectFixture(Path(self._tmp.name) / name)
                self.write_budget_agents(total_bytes, total_lines,
                                         total_bytes - owned_bytes)
                rc, report = self.budget()
                item = self.fx.item(report, "agents.budget")
                self.assertEqual(expected, item["status"], item["detail"])
                self.assertEqual(0, rc, report)
                if expected == "warn":
                    self.assertIn("agents-md-slimming", item["next"])
                    self.assertIn("templates/playbooks/agents-md-slimming.md",
                                  item["next"])

    def test_owned_ratio_warn_with_boundary(self):
        cases = (
            ("ratio-under", 20000, 12999, "pass"),   # 64.995%
            ("ratio-equal", 20000, 13000, "pass"),   # 恰 65%，要求 >65%
            ("ratio-over", 20000, 13001, "warn"),    # 65.005%
            ("small-bytes", 16384, 16000, "pass"),   # 占比高但全文未超 16KB
            ("over-16kb", 16385, 16001, "warn"),
        )
        for name, total_bytes, owned_bytes, expected in cases:
            with self.subTest(case=name):
                self.fx = ProjectFixture(Path(self._tmp.name) / name)
                self.write_budget_agents(total_bytes, 149,
                                         total_bytes - owned_bytes)
                rc, report = self.budget()
                item = self.fx.item(report, "agents.budget")
                self.assertEqual(expected, item["status"], item["detail"])
                self.assertEqual(0, rc, report)
                if expected == "warn":
                    self.assertIn("项目自有段 >65%", item["detail"])

    def test_warn_keeps_non_strict_rc_and_strict_semantics(self):
        self.write_budget_agents(20481, 149, 10481)
        self.fx.commit()
        rc, report = self.fx.doctor_json()
        item = self.fx.item(report, "agents.budget")
        self.assertEqual("warn", item["status"])
        self.assertIn("agents.budget", [row["id"] for row in report["items"]])
        self.assertNotEqual(1, rc, report)
        proc = self.fx.doctor("--only", "agents.budget", "--strict", "--json")
        self.assertEqual(1, proc.returncode, proc.stdout + proc.stderr)

    def test_missing_file_or_markers_skips(self):
        (self.fx.root / "AGENTS.md").unlink()
        rc, report = self.budget()
        self.assertEqual(0, rc, report)
        self.assertEqual("skip", self.fx.item(report, "agents.budget")["status"])

        (self.fx.root / "AGENTS.md").write_text("# AGENTS\n\n无标记。\n", encoding="utf-8")
        rc, report = self.budget()
        self.assertEqual(0, rc, report)
        self.assertEqual("skip", self.fx.item(report, "agents.budget")["status"])


if __name__ == "__main__":
    unittest.main()
