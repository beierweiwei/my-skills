#!/usr/bin/env python3
"""init.py 行为契约测试：python3 scripts/tests/test_init.py

覆盖设计 §3.2：检测/生成/幂等（一致 skip、不一致默认不覆盖列冲突清单、--force 仅限受管路径）/
清单外零写入/AGENTS.md 标记节/portfolio 沿用/topic 默认。夹具模板为中性占位内容（无业务词）。
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("init_mod", _SCRIPTS / "init.py")
init_mod = importlib.util.module_from_spec(_spec)
sys.modules["init_mod"] = init_mod
_spec.loader.exec_module(init_mod)

FIXTURE_TEMPLATES = Path(__file__).resolve().parent / "fixtures" / "templates"

# init 受管产物相对路径（清单内；测试断言清单外零写入用）
MANAGED = {
    ".planning/orchestration.json",
    ".qoder/agents/helm.md",
    ".qoder/agents/builder.md",
    "scripts/start-agent.sh",
    "scripts/start-helm.sh",
    "scripts/helm_agent_name.py",
    "scripts/agent-watch.py",
    "scripts/worktree-watch.py",
    "scripts/herdr-events.py",
    "scripts/plan-status.py",
    ".planning/portfolio/task_plan.md",
    ".planning/portfolio/findings.md",
    ".planning/portfolio/progress.md",
    "AGENTS.md",
    "CONTEXT.md",
}


def snapshot(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


class _Tty(io.StringIO):
    """模拟 tty 输入（io.StringIO 不允许实例级 setattr，用子类覆盖 isatty）。"""

    def isatty(self) -> bool:
        return True


class InitTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "proj"
        self.root.mkdir()

    def run_init(self, *extra: str) -> int:
        return init_mod.run(["--root", str(self.root), "--templates", str(FIXTURE_TEMPLATES),
                             "--yes", *extra])

    # --- 首跑：产物齐、占位符渲染 ---

    def test_first_run_creates_products(self):
        rc = self.run_init()
        self.assertEqual(rc, 0)
        for rel in MANAGED:
            self.assertTrue((self.root / rel).exists(), f"缺产物 {rel}")
        builder = (self.root / ".qoder/agents/builder.md").read_text(encoding="utf-8")
        self.assertIn("proj", builder)          # projectName 默认=目录名
        self.assertIn(init_mod.DEFAULT_CONFIG["docNames"]["req"], builder)  # docNames.req 默认渲染
        self.assertIn(".scratch/<topic>/issues/", builder)  # topic 未配置 → 字面 <topic>
        self.assertNotIn("{{", builder)          # 占位符零残留
        cfg = json.loads((self.root / ".planning/orchestration.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["projectName"], "proj")

    # --- 幂等：二跑零副作用 ---

    def test_idempotent_second_run(self):
        self.run_init()
        before = snapshot(self.root)
        rc = self.run_init()
        self.assertEqual(rc, 0)
        self.assertEqual(snapshot(self.root), before, "二次执行产生了副作用")

    # --- 冲突：默认不覆盖 / --force 覆盖 ---

    def test_conflict_no_overwrite_by_default(self):
        self.run_init()
        target = self.root / ".qoder/agents/builder.md"
        target.write_text("手工修改过的角色文件", encoding="utf-8")
        rc = self.run_init()
        self.assertEqual(rc, 2, "存在未处理冲突应返回 2")
        self.assertEqual(target.read_text(encoding="utf-8"), "手工修改过的角色文件", "冲突文件被覆盖")

    def test_conflict_force_overwrites_managed_path_only(self):
        self.run_init()
        (self.root / ".qoder/agents/builder.md").write_text("旧内容", encoding="utf-8")
        rc = self.run_init("--force")
        self.assertEqual(rc, 0)
        builder = (self.root / ".qoder/agents/builder.md").read_text(encoding="utf-8")
        self.assertNotIn("{{", builder)  # 渲染后无占位符残留
        self.assertIn(init_mod.DEFAULT_CONFIG["docNames"]["req"], builder)

    # --- AGENTS.md：追加保留 / 标记节冲突与替换 ---

    def test_agents_append_preserves_original(self):
        (self.root / "AGENTS.md").write_text("# 原有说明\n\n原内容段落。\n", encoding="utf-8")
        self.run_init()
        text = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# 原有说明\n\n原内容段落。"), "原内容被破坏")
        self.assertIn(init_mod.MARK_START, text)
        self.assertIn(init_mod.MARK_END, text)
        before = snapshot(self.root)
        rc = self.run_init()
        self.assertEqual(rc, 0)
        self.assertEqual(snapshot(self.root), before)  # 二次幂等

    def test_agents_marker_section_conflict_then_force(self):
        (self.root / "AGENTS.md").write_text(
            "# 原有说明\n\n"
            f"{init_mod.MARK_START}\n旧节内容（人工改过）\n{init_mod.MARK_END}\n",
            encoding="utf-8",
        )
        rc = self.run_init()
        self.assertEqual(rc, 2)
        self.assertIn("旧节内容", (self.root / "AGENTS.md").read_text(encoding="utf-8"))
        rc = self.run_init("--force")
        self.assertEqual(rc, 0)
        text = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# 原有说明"), "节外内容被破坏")
        self.assertNotIn("旧节内容", text)
        self.assertIn(init_mod.MARK_START, text)

    # --- 边界：清单外零写入 ---

    def test_no_writes_outside_manifest(self):
        (self.root / "user-file.txt").write_text("用户文件", encoding="utf-8")
        before_files = set(snapshot(self.root))
        self.run_init()
        after_files = set(snapshot(self.root))
        self.assertEqual(before_files, {"user-file.txt"})
        self.assertTrue(after_files - before_files <= MANAGED,
                        f"清单外写入：{after_files - before_files - MANAGED}")

    # --- 模板缺失优雅降级 ---

    def test_missing_templates_graceful(self):
        rc = init_mod.run(["--root", str(self.root),
                           "--templates", str(self.root / "no-such-templates"), "--yes"])
        self.assertEqual(rc, 0)
        self.assertFalse((self.root / ".qoder").exists())
        self.assertTrue((self.root / "scripts/plan-status.py").exists())  # 脚本仍落

    # --- portfolio 已有目录沿用 / 配置已存在读现值 ---

    def test_existing_portfolio_dir_reused(self):
        existing = self.root / ".planning" / "2026-01-01-my-portfolio"
        existing.mkdir(parents=True)
        (existing / "task_plan.md").write_text("# 既有组合 plan\n", encoding="utf-8")
        rc = self.run_init()
        self.assertEqual(rc, 0)
        self.assertTrue((existing / "findings.md").exists(), "沿用目录未补齐骨架")
        self.assertFalse((self.root / ".planning/portfolio").exists())

    def test_existing_config_read_and_diff_reported(self):
        planning = self.root / ".planning"
        planning.mkdir(parents=True)
        (planning / "orchestration.json").write_text(
            json.dumps({"projectName": "自定义名", "docNames": {"req": "PRD.md"}},
                       ensure_ascii=False),
            encoding="utf-8",
        )
        rc = self.run_init()
        self.assertEqual(rc, 0)
        builder = (self.root / ".qoder/agents/builder.md").read_text(encoding="utf-8")
        self.assertIn("PRD.md", builder, "配置现值未生效")

    # --- 既有 orchestration 与 portfolio 双存在 + CONTEXT 已存在不动 ---

    def test_context_existing_untouched(self):
        (self.root / "CONTEXT.md").write_text("# 既有术语表\n", encoding="utf-8")
        self.run_init()
        self.assertEqual((self.root / "CONTEXT.md").read_text(encoding="utf-8"), "# 既有术语表\n")

    # --- 阻断2：AGENTS 标记恰一层；二跑全跳过 exit 0（夹具与真实模板同构：自带标记行） ---

    def test_agents_marker_written_once_and_rerun_skips(self):
        rc = self.run_init()
        self.assertEqual(rc, 0)
        text = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertEqual(text.count(init_mod.MARK_START), 1, "出现双层 start 标记")
        self.assertEqual(text.count(init_mod.MARK_END), 1, "出现双层 end 标记")
        # 二跑：渲染一致 → 全跳过 exit 0，标记不再膨胀
        before = snapshot(self.root)
        rc = self.run_init()
        self.assertEqual(rc, 0)
        self.assertEqual(snapshot(self.root), before, "二跑产生副作用")
        # 冲突 → --force 修复路径：force 后标记仍恰一层（回归前此场景持续膨胀）
        (self.root / "AGENTS.md").write_text(
            f"# 头部\n\n{init_mod.MARK_START}\n人工改过的旧节\n{init_mod.MARK_END}\n",
            encoding="utf-8",
        )
        self.assertEqual(self.run_init(), 2)
        self.assertEqual(self.run_init("--force"), 0)
        text = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertEqual(text.count(init_mod.MARK_START), 1)
        self.assertEqual(text.count(init_mod.MARK_END), 1)

    # --- 阻断3：aopReference 空值兜底，零配置直通渲染无病句 ---

    def test_aop_reference_empty_fallback(self):
        # 渲染表：空值 → 兜底文案；配置了路径 → 原样
        ctx = init_mod.render_context({"aopReference": ""})
        self.assertEqual(ctx["aop_reference"], "（未配置——按 AOP 六维自审并记入 plan Decisions）")
        ctx = init_mod.render_context({"aopReference": "docs/AOP总纲.md"})
        self.assertEqual(ctx["aop_reference"], "docs/AOP总纲.md")
        # 零配置直通：产物无空渲染病句、无占位符残留
        self.run_init()
        agents = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertNotIn("{{", agents)
        self.assertIn("按 AOP 六维自审并记入 plan Decisions", agents)
        self.assertNotIn("以  为唯一真源", agents)  # 空串直通的病句形态

    # --- 阻断4：沿用既有 portfolio 后，配置/渲染/start-helm 三处一致 ---

    def test_existing_portfolio_three_way_consistency(self):
        existing_rel = ".planning/2026-01-01-my-portfolio"
        existing = self.root / existing_rel
        existing.mkdir(parents=True)
        (existing / "task_plan.md").write_text("# 既有组合 plan\n", encoding="utf-8")
        rc = self.run_init()
        self.assertEqual(rc, 0)
        # ① orchestration.json 的 portfolioDir = 实际沿用路径
        cfg = json.loads((self.root / ".planning/orchestration.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["portfolioDir"], existing_rel)
        # ② 渲染 ctx（helm 角色文件）= 实际路径，默认路径不得出现
        helm = (self.root / ".qoder/agents/helm.md").read_text(encoding="utf-8")
        self.assertIn(existing_rel, helm)
        self.assertNotIn(".planning/portfolio/", helm)
        # ③ start-helm.sh 必读清单运行期读同一配置键（读取逻辑在 helm_agent_name.py，
        #    由 start-helm.sh 调用，行为由 ① 驱动）
        sh = (self.root / "scripts/start-helm.sh").read_text(encoding="utf-8")
        self.assertIn("helm_agent_name.py", sh)
        han = (self.root / "scripts/helm_agent_name.py").read_text(encoding="utf-8")
        self.assertIn('get(cfg, "portfolioDir"', han)
        self.assertIn("PORTFOLIO_DIR", han)
        # 二跑幂等：配置已同步，不再回写
        before = snapshot(self.root)
        self.assertEqual(self.run_init(), 0)
        self.assertEqual(snapshot(self.root), before)

    # --- 建议10：损坏 orchestration.json 显式警告 ---

    def test_broken_config_warns_and_uses_defaults(self):
        planning = self.root / ".planning"
        planning.mkdir(parents=True)
        (planning / "orchestration.json").write_text("{broken json", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = self.run_init()
        self.assertEqual(rc, 0)
        self.assertIn("配置文件损坏", out.getvalue())
        builder = (self.root / ".qoder/agents/builder.md").read_text(encoding="utf-8")
        self.assertIn(init_mod.DEFAULT_CONFIG["docNames"]["req"], builder)  # 内置默认生效

    # --- 建议14：冲突清单带双方路径 ---

    def test_conflict_report_shows_source_path(self):
        self.run_init()
        (self.root / ".qoder/agents/builder.md").write_text("手工改过", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = self.run_init()
        self.assertEqual(rc, 2)
        text = out.getvalue()
        self.assertIn("技能侧源", text)
        self.assertIn(str(FIXTURE_TEMPLATES / "agents" / "qoder" / "builder.md"), text)

    # --- 建议15a：追加场景核对清单标「追加」 ---

    def test_append_scenario_labeled(self):
        (self.root / "AGENTS.md").write_text("# 原有说明\n", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.run_init()
        self.assertIn("[追加]", out.getvalue())

    # --- 建议8：编号选项问答 ---

    def test_ask_choice(self):
        options = ["沿用 X", "沿用 Y", "新建 Z"]
        self.assertEqual(init_mod.ask_choice("p", options, 1, True), 1)      # --yes 直通
        real = sys.stdin
        sys.stdin = io.StringIO("1\n")                                       # 非 tty 直通默认
        try:
            self.assertEqual(init_mod.ask_choice("p", options, 2, False), 2)
        finally:
            sys.stdin = real
        for raw, want in [("1", 1), ("3", 3), ("新建", 3), ("沿用", 1), ("", 2), ("乱答", 2)]:
            sys.stdin = _Tty(f"{raw}\n")
            try:
                self.assertEqual(init_mod.ask_choice("p", options, 2, False), want,
                                 f"输入 {raw!r} 应得 {want}")
            finally:
                sys.stdin = real

    # --- 建议6：列表占位符句中断行 ---

    def test_list_token_inline_breaks(self):
        ctx = {"xs": "- 甲\n- 乙"}
        self.assertEqual(init_mod.apply_template("决定：{{xs}}。", ctx),
                         "决定：\n- 甲\n- 乙。")            # 句号附着末条
        self.assertEqual(init_mod.apply_template("职责：{{xs}}与纪律", ctx),
                         "职责：\n- 甲\n- 乙\n与纪律")       # 后接文字 → 断行
        self.assertEqual(init_mod.apply_template("前置\n{{xs}}\n后置", ctx),
                         "前置\n- 甲\n- 乙\n后置")           # 独占行 → 不变

    # --- 建议11：配置模板自注释补 topic 条目 ---

    def test_config_template_documents_topic(self):
        tpl = init_mod.TEMPLATES / "project.config.json"
        if not tpl.exists():
            self.skipTest("技能模板目录未就位")
        data = json.loads(tpl.read_text(encoding="utf-8"))
        self.assertIn("topic", data["_placeholders"])
        self.assertIn("<topic>", data["_placeholders"]["topic"])


    # --- R1：旧双层形态（start×2/end×2）--force 一次收敛 ---

    def test_double_marker_force_converges(self):
        self.run_init()
        p = self.root / "AGENTS.md"
        text = p.read_text(encoding="utf-8")
        S, E = init_mod.MARK_START, init_mod.MARK_END
        pre, rest = text.split(S, 1)
        body, post = rest.split(E, 1)
        dbl = pre + S + "\n" + S + "\n" + body + E + "\n" + E + "\n" + post
        p.write_text(dbl, encoding="utf-8")
        self.assertEqual(dbl.count(S), 2)
        self.assertEqual(dbl.count(E), 2)
        self.assertEqual(self.run_init(), 2, "双层残态与干净渲染有差异，应判冲突")
        self.assertEqual(self.run_init("--force"), 0)
        fixed = p.read_text(encoding="utf-8")
        self.assertEqual(fixed.count(S), 1, "start 整行应收敛为 1")
        self.assertEqual(fixed.count(E), 1, "end 整行应收敛为 1")
        self.assertNotIn("<!-- agent-crew:orchestration:", fixed.split(E, 1)[1], "节后无孤儿标记残留")
        before = snapshot(self.root)
        self.assertEqual(self.run_init(), 0, "收敛后再跑应 exit 0 全跳过")
        self.assertEqual(snapshot(self.root), before, "收敛后二跑应零副作用")


if __name__ == "__main__":
    unittest.main(verbosity=2)
