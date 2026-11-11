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

    def test_descriptive_block_rejects_bare_absolute_skill_root(self):
        # S2：描述性块（含锚点）出现任何绝对路径 → stale，即使恰为当前 skillRoot
        baseline = [
            "## 协作编排",
            "- 技能根：`.planning/orchestration.json` 的 `skillRoot`。",
            "- 规则：`references/crew-rules.md`（D1–D6）。",
        ]
        cases = (
            ("old-home", "`/home/x/skills/agent-crew`"),
            ("current-root", f"`{self.fx.skill}`"),
        )
        for name, path in cases:
            with self.subTest(case=name):
                self.fx.write_agents(block="\n".join(baseline + [f"- 旧技能根：{path}"]))
                self.fx.write_manifest()
                rc, report = self.fx.doctor_json("--only", "agents.block")
                self.assertEqual(1, rc, report)
                self.assertIn("绝对路径", self.fx.item(report, "agents.block")["detail"])
                rc, report = self.fx.doctor_json("--only", "docs.pointers")
                self.assertEqual(1, rc, report)
                self.assertIn("stale", self.fx.item(report, "docs.pointers")["detail"])

        # 纯相对名描述性块仍 pass
        self.fx.write_agents(block="\n".join(baseline))
        self.fx.write_manifest()
        for item_id in ("agents.block", "docs.pointers"):
            rc, report = self.fx.doctor_json("--only", item_id)
            self.assertEqual(0, rc, (item_id, report))
            self.assertEqual("pass", self.fx.item(report, item_id)["status"], item_id)

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

    def test_docs_duplicate_scans_orchestration_docs(self):
        # FB-017：docs 下文件名含 编排|orchestration|协作|agents 的文档纳入扫描
        line = "- 这是一条长度超过三十个字符的编排规则句，用来验证 docs 编排文档重复检测。"
        self.fx.write_agents(block="## 协作编排\n" + line)
        doc = self.fx.root / "docs/编排系统.md"
        doc.write_text("# 编排系统\n\n" + line + "\n", encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "docs.duplicate")
        self.assertEqual(1, rc, report)
        item = self.fx.item(report, "docs.duplicate")
        self.assertEqual("fail", item["status"])
        self.assertIn("docs/编排系统.md", item["detail"])

        # 大小写不敏感的 orchestration 文件名同样命中（详情按出现顺序列出前两处）
        path = self.fx.root / "docs/ORCHESTRATION-notes.md"
        path.write_text("# 说明\n\n" + line + "\n", encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "docs.duplicate")
        self.assertEqual(1, rc, report)
        self.assertIn("docs/ORCHESTRATION-notes.md",
                      self.fx.item(report, "docs.duplicate")["detail"])

    def test_docs_duplicate_ignores_unrelated_docs(self):
        line = "- 这是一条长度超过三十个字符的编排规则句，无关文档不应参与重复检测。"
        self.fx.write_agents(block="## 协作编排\n" + line)
        doc = self.fx.root / "docs/业务说明.md"
        doc.write_text("# 业务说明\n\n" + line + "\n", encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "docs.duplicate")
        self.assertEqual(0, rc, report)
        self.assertEqual("pass", self.fx.item(report, "docs.duplicate")["status"])

    def test_docs_pointers_project_ext_missing_fails_and_restores(self):
        # FB-018：受管块出现 project-ext 指针且目录不存在 → fail + 可执行 next
        block = "\n".join([
            "## 协作编排",
            "- 项目事实：`CONTEXT.md`、topology、`docs/crew/project-ext/`。",
            f"- 规则：`{self.fx.skill}/references/crew-rules.md`（D1–D6）。",
        ])
        self.fx.write_agents(block=block)
        self.fx.write_manifest()
        shutil.rmtree(self.fx.root / "docs/crew/project-ext")
        rc, report = self.fx.doctor_json("--only", "docs.pointers")
        self.assertEqual(1, rc, report)
        item = self.fx.item(report, "docs.pointers")
        self.assertEqual("fail", item["status"])
        self.assertIn("docs/crew/project-ext", item["detail"])
        self.assertIn("不存在", item["detail"])
        self.assertIn("python3 scripts/init.py --yes", item["next"])

        (self.fx.root / "docs/crew/project-ext").mkdir(parents=True)
        rc, report = self.fx.doctor_json("--only", "docs.pointers")
        self.assertEqual(0, rc, report)
        self.assertEqual("pass", self.fx.item(report, "docs.pointers")["status"])

    def test_docs_pointers_custom_project_ext_passes_with_default_literal(self):
        # B1 复现 1：自定义 projectExt 存在时，受管块模板默认字面量不假红
        self.fx.config["hooks"]["paths"]["projectExt"] = "docs/crew/ext"
        self.fx.write_config()
        self.fx.write_manifest()
        shutil.rmtree(self.fx.root / "docs/crew/project-ext")
        (self.fx.root / "docs/crew/ext").mkdir(parents=True)
        self.fx.write_agents(block="\n".join([
            "## 协作编排",
            f"- 规则：`{self.fx.skill}/references/crew-rules.md`（D1–D6）。",
            "- 项目事实：`CONTEXT.md`、topology、`docs/crew/project-ext/`。",
        ]))
        rc, report = self.fx.doctor_json("--only", "docs.pointers")
        self.assertEqual(0, rc, report)
        self.assertEqual("pass", self.fx.item(report, "docs.pointers")["status"])

    def test_docs_pointers_role_file_broken_project_ext_pointer_fails(self):
        # B1 复现 2：角色文件正文引用不存在的 project-ext 路径 → fail，detail 指文件与路径
        self.fx.config["hooks"]["paths"]["projectExt"] = "docs/crew/ext"
        self.fx.write_config()
        self.fx.write_manifest()
        (self.fx.root / "docs/crew/ext").mkdir(parents=True)
        shutil.rmtree(self.fx.root / "docs/crew/project-ext")
        role = self.fx.role_path("qoder", "builder")
        role.write_text(role.read_text(encoding="utf-8")
                        + "\n项目扩展见 `docs/crew/project-ext/`。\n", encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "docs.pointers")
        self.assertEqual(1, rc, report)
        item = self.fx.item(report, "docs.pointers")
        self.assertEqual("fail", item["status"])
        self.assertIn(".qoder/agents/builder.md", item["detail"])
        self.assertIn("docs/crew/project-ext/", item["detail"])
        self.assertIn("python3 scripts/init.py --yes", item["next"])


class DoctorAgentsBudgetTest(unittest.TestCase):
    """agents.budget：体量非阻断 warn（阈值 20KB / 180 行 / 自有段 65% 且 >16KB）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = ProjectFixture(Path(self._tmp.name))

    def write_budget_agents(self, total_bytes: int, total_lines: int,
                            block_bytes: int, newline: str = "\n") -> None:
        """构造精确 总字节/行数/受管块字节 的 AGENTS.md（按磁盘字节）并同步 manifest。"""
        nl = newline
        block_head = "## 协作编排" + nl
        filler = block_bytes - 2 * len(nl) - len(block_head.encode("utf-8"))
        self.assertGreaterEqual(filler, 0)
        block = block_head + "b" * filler
        suffix = nl + MARK_START + nl + block + nl + MARK_END + nl
        head = "# AGENTS" + nl + nl
        fixed = head + suffix
        pad_bytes = total_bytes - len(fixed.encode("utf-8"))
        pad_lines = total_lines - len(fixed.splitlines())
        self.assertGreater(pad_lines, 0)
        base, rem = divmod(pad_bytes, pad_lines)
        self.assertGreaterEqual(base, len(nl))
        pad = "".join(
            "x" * (base - len(nl) + (1 if index < rem else 0)) + nl
            for index in range(pad_lines)
        )
        text = head + pad + suffix
        self.assertEqual(total_bytes, len(text.encode("utf-8")))
        self.assertEqual(total_lines, len(text.splitlines()))
        self.assertEqual(
            block_bytes,
            len(text[text.index(MARK_START) + len(MARK_START):text.index(MARK_END)]
                .encode("utf-8")),
        )
        (self.fx.root / "AGENTS.md").write_bytes(text.encode("utf-8"))
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

    def test_crlf_uses_disk_byte_accounting(self):
        # S1：磁盘 20,000B / 块 7,000B / 自有段 13,000B（恰 65.0%）→ pass；
        # 旧实现按 read_text 折算 LF 会把自有段算成 65.31% 误报 warn。
        self.write_budget_agents(20000, 149, 7000, newline="\r\n")
        rc, report = self.budget()
        self.assertEqual(0, rc, report)
        item = self.fx.item(report, "agents.budget")
        self.assertEqual("pass", item["status"], item["detail"])
        self.assertIn("20000", item["detail"])
        self.assertIn("7000", item["detail"])
        self.assertIn("65.0%", item["detail"])

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
