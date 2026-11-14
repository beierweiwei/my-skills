#!/usr/bin/env python3
"""2.2.29 上下文瘦身批（S1–S6）回归测试。

S1 `crew-msg.py ls --mark-read` 默认只回计数（--verbose 展开）
S6 `crew-msg.py write --handoff-read-bytes` 首报读取量字段
版本 2.2.29 四处一致
（S2/S3/S4/S5 见同类下方各 TestCase；python3 scripts/tests/test_2229_context_slim.py）"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

_SCRIPTS = Path(__file__).resolve().parent.parent
_SKILL_ROOT = _SCRIPTS.parent

_spec = importlib.util.spec_from_file_location("crew_msg", _SCRIPTS / "crew-msg.py")
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclass 注解解析需要模块已注册
    spec.loader.exec_module(module)
    return module


dm = _load_module("dispatch_2229", _SCRIPTS / "dispatch.py")


def make_receipt(**overrides) -> dict:
    fields = dict(
        from_="builder-1",
        runtime="claude",
        task="2229",
        status="done",
        summary="切片完成",
        commit="abc123",
        changed_files=["scripts/foo.py"],
        evidence=["python3 -m unittest"],
        work_slot="builder",
        findings=[],
        receipt_id="r-1",
        ts="2026-11-14T00:00:00Z",
    )
    fields.update(overrides)
    return cm.build_receipt(**fields)


class CrewDirTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.crew = Path(self._tmp.name) / ".git" / "crew"

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cm.main(argv)
        return code, out.getvalue(), err.getvalue()

    def _seed_inbox(self, n: int, prefix: str = "mark-s1") -> list[str]:
        inbox = cm.inbox_dir(self.crew)
        inbox.mkdir(parents=True, exist_ok=True)
        ids = []
        for i in range(n):
            rid = f"{prefix}-{i:04d}"
            receipt = make_receipt(receipt_id=rid, summary="ok")
            (inbox / f"{rid}.json").write_text(
                json.dumps(receipt, ensure_ascii=False), encoding="utf-8"
            )
            ids.append(rid)
        return ids


# ---------------------------------------------------------------------------
# S1：ls --mark-read 默认只回计数，--verbose 展开，--json 不变
# ---------------------------------------------------------------------------


class S1MarkReadSlimTest(CrewDirTestBase):
    def test_s1_mark_read_all_default_counts_only_within_1kb(self):
        self._seed_inbox(200)
        code, out, err = self._run(
            ["ls", "--crew-dir", str(self.crew), "--mark-read", "--all"]
        )
        self.assertEqual(code, 0, err)
        self.assertLessEqual(len(out.encode("utf-8")), 1024, "默认 stdout 应 ≤1KB")
        self.assertNotIn("mark-s1-", out, "默认不得回显 marked receipt_id 列表")
        self.assertIn("标记 200 条", out)
        self.assertIn("inbox-read-cursor.json", out, "应含游标路径")
        # 标记真实生效：--unread 清空
        code2, out2, err2 = self._run(["ls", "--crew-dir", str(self.crew), "--unread"])
        self.assertEqual(code2, 0, err2)
        self.assertIn("(无未读回执)", out2)

    def test_s1_mark_read_explicit_ids_default_slim(self):
        ids = self._seed_inbox(60)
        pick = ids[:40]
        code, out, err = self._run(
            ["ls", "--crew-dir", str(self.crew), "--mark-read", *pick]
        )
        self.assertEqual(code, 0, err)
        self.assertLessEqual(len(out.encode("utf-8")), 1024, "默认 stdout 应 ≤1KB")
        for rid in pick[:5]:
            self.assertNotIn(rid, out)
        self.assertIn("标记 40 条", out)

    def test_s1_mark_read_verbose_expands_ids(self):
        ids = self._seed_inbox(200)
        code, out, err = self._run(
            ["ls", "--crew-dir", str(self.crew), "--mark-read", "--all", "--verbose"]
        )
        self.assertEqual(code, 0, err)
        for rid in ids[:5]:
            self.assertIn(rid, out, "--verbose 应展开 marked id 列表")
        self.assertIn("标记 200 条", out)

    def test_s1_mark_read_json_structure_unchanged(self):
        self._seed_inbox(5)
        code, out, err = self._run(
            ["ls", "--crew-dir", str(self.crew), "--mark-read", "--all", "--json"]
        )
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        for key in ("marked", "already_read", "skipped", "receipts", "cursor"):
            self.assertIn(key, payload)
        self.assertEqual(len(payload["marked"]), 5)
        self.assertEqual(len(payload["receipts"]), 5)


# ---------------------------------------------------------------------------
# S6：write --handoff-read-bytes（非负 int 校验、落盘可回读、字段可选）
# ---------------------------------------------------------------------------


class S6HandoffReadBytesTest(CrewDirTestBase):
    def _write(self, *extra):
        argv = [
            "write",
            "--crew-dir",
            str(self.crew),
            "--from",
            "builder-x",
            "--runtime",
            "claude",
            "--task",
            "2229",
            "--status",
            "done",
            "--summary",
            "s",
            "--no-push",
            *extra,
        ]
        return self._run(argv)

    def _only_inbox_file(self) -> Path:
        files = sorted(cm.inbox_dir(self.crew).glob("*.json"))
        self.assertEqual(len(files), 1, f"应恰好 1 个回执文件，实际 {files}")
        return files[0]

    def test_s6_write_handoff_read_bytes_roundtrip(self):
        code, out, err = self._write("--handoff-read-bytes", "12345")
        self.assertEqual(code, 0, err)
        path = self._only_inbox_file()
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["handoff_read_bytes"], 12345)
        code, out, err = self._run(
            ["read", "--crew-dir", str(self.crew), path.stem]
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["handoff_read_bytes"], 12345)

    def test_s6_field_absent_when_not_passed(self):
        code, out, err = self._write()
        self.assertEqual(code, 0, err)
        data = json.loads(self._only_inbox_file().read_text(encoding="utf-8"))
        self.assertNotIn("handoff_read_bytes", data, "旧调用不写新字段")

    def test_s6_negative_rejected_and_no_file(self):
        code, out, err = self._write("--handoff-read-bytes", "-1")
        self.assertNotEqual(code, 0)
        self.assertEqual(list(cm.inbox_dir(self.crew).glob("*.json")), [])

    def test_s6_non_int_rejected_and_no_file(self):
        # argparse type=int 对 "abc" 直接 SystemExit(rc=2)，同样不落文件
        with self.assertRaises(SystemExit) as ctx:
            self._write("--handoff-read-bytes", "abc")
        self.assertNotEqual(ctx.exception.code, 0)
        self.assertEqual(list(cm.inbox_dir(self.crew).glob("*.json")), [])

    def test_s6_zero_accepted(self):
        code, out, err = self._write("--handoff-read-bytes", "0")
        self.assertEqual(code, 0, err)
        data = json.loads(self._only_inbox_file().read_text(encoding="utf-8"))
        self.assertEqual(data["handoff_read_bytes"], 0)

    def test_s6_build_receipt_validates_and_keeps_bool_out(self):
        r = cm.build_receipt(
            from_="a", runtime="claude", task="t", status="done", summary="s",
            handoff_read_bytes=0,
        )
        self.assertEqual(r["handoff_read_bytes"], 0)
        with self.assertRaises(cm.CrewMsgError):
            cm.build_receipt(
                from_="a", runtime="claude", task="t", status="done", summary="s",
                handoff_read_bytes=-5,
            )
        with self.assertRaises(cm.CrewMsgError):
            cm.build_receipt(
                from_="a", runtime="claude", task="t", status="done", summary="s",
                handoff_read_bytes=True,
            )

    def test_s6_receipt_fields_tuple_unchanged(self):
        self.assertNotIn("handoff_read_bytes", cm.RECEIPT_FIELDS)

    def test_s6_template_and_guard_prompt_grep(self):
        for runtime in ("qoder", "opencode", "claude"):
            helm = (_SKILL_ROOT_REL / "templates" / "agents" / runtime
                    / "helm.md").read_text(encoding="utf-8")
            self.assertIn("首报须带 `handoff_read_bytes=<n>`", helm,
                          f"{runtime} helm 首报格式")
            self.assertIn("--handoff-read-bytes", helm, f"{runtime} helm 写入命令")
        guard = (_SCRIPTS / "crew-guard.py").read_text(encoding="utf-8")
        self.assertIn("须带 handoff_read_bytes=<n>", guard, "guard 继任 prompt 提醒")


# ---------------------------------------------------------------------------
# S2：dispatch.py status 默认只列在线 agent，历史/done 折叠计数，--all 展开
# ---------------------------------------------------------------------------


class S2DispatchStatusSlimTest(unittest.TestCase):
    @staticmethod
    def _fixture() -> list[dict]:
        agents = [
            {"name": f"old-done-{i:03d}", "status": "done"} for i in range(200)
        ]
        agents += [
            {"name": "live-helm", "status": "working"},
            {"name": "live-builder", "status": "idle"},
            {"name": "live-scout", "status": "blocked"},
            {"name": "live-runner", "status": "pending"},
        ]
        return agents

    def _run_status(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(dm, "herdr_agents_detail",
                               return_value=self._fixture()), \
             mock.patch.object(dm, "load_registry", return_value={}), \
             mock.patch.object(dm, "crew_dir",
                               return_value=Path("/tmp/fake-crew-dir")):
            with redirect_stdout(out), redirect_stderr(err):
                code = dm.main(["status", *extra])
        return code, out.getvalue(), err.getvalue()

    def test_s2_default_online_only_within_2kb(self):
        code, out, err = self._run_status()
        self.assertEqual(code, 0, err)
        self.assertLessEqual(len(out.encode("utf-8")), 2048, "默认 stdout 应 ≤2KB")
        for name in ("live-helm", "live-builder", "live-scout", "live-runner"):
            self.assertIn(name, out)
        self.assertNotIn("old-done-000", out, "默认不得回显历史名")
        self.assertIn("历史/done 200 个（--all 展开）", out, "应含折叠计数行")
        self.assertIn("dispatch 托管：", out, "托管行保留（机器解析兼容）")

    def test_s2_all_expands_retired_names(self):
        code, out, err = self._run_status("--all")
        self.assertEqual(code, 0, err)
        self.assertIn("old-done-000", out)
        self.assertIn("live-helm", out)
        self.assertNotIn("历史/done 200 个（--all 展开）", out)

    def test_s2_json_keeps_full_structure(self):
        code, out, err = self._run_status("--json")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(len(payload["agents"]), 204, "--json 仍回全量名列表")
        self.assertIn("crewDir", payload)
        self.assertIn("managed", payload)

    def test_s2_empty_registry_line_kept(self):
        code, out, err = self._run_status()
        self.assertIn("dispatch 托管：（无）", out)


# ---------------------------------------------------------------------------
# S3：crew-preflight.py 未读展示默认 ≤3 条，--full 全量
# ---------------------------------------------------------------------------


class S3PreflightInboxSlimTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pf = _load_module("preflight_2229", _SCRIPTS / "crew-preflight.py")

    def _crew_with_unread(self, n: int) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        crew = Path(tmp.name) / ".git" / "crew"
        inbox = crew / "inbox"
        inbox.mkdir(parents=True)
        for i in range(n):
            rid = f"unread-s3-{i:03d}"
            (inbox / f"{rid}.json").write_text(json.dumps({
                "receipt_id": rid, "status": "done", "kind": "receipt",
                "from": "builder-x", "task": "2229",
            }), encoding="utf-8")
        return crew

    def test_s3_default_shows_3_and_rest_count(self):
        crew = self._crew_with_unread(12)
        check = self.pf.check_inbox(crew)
        blob = check["value"] + check["detail"]
        self.assertLessEqual(len(blob.encode("utf-8")), 1024, "未读区增量应 ≤1KB")
        for rid in ("unread-s3-000", "unread-s3-001", "unread-s3-002"):
            self.assertIn(rid, check["detail"], "应含前 3 条")
        self.assertNotIn("unread-s3-003", check["detail"], "第 4 条起默认不展示")
        self.assertIn("…另有 9 条（--full 展开）", check["detail"])
        self.assertIn("12 条未读", check["value"], "总数行语义不变")

    def test_s3_full_keeps_previous_behavior(self):
        crew = self._crew_with_unread(12)
        check = self.pf.check_inbox(crew, full=True)
        self.assertNotIn("--full 展开", check["detail"])
        for rid in ("unread-s3-003", "unread-s3-009"):
            self.assertIn(rid, check["detail"], "--full 展开未读明细")

    def test_s3_json_structure_unchanged(self):
        crew = self._crew_with_unread(12)
        check = self.pf.check_inbox(crew)
        self.assertEqual(
            set(check),
            {"id", "title", "available", "ok", "value", "source", "detail"},
        )


# ---------------------------------------------------------------------------
# S4/S5：plan-status.py --handover 8KB 硬上限 + Next Step 未完成首条
# ---------------------------------------------------------------------------

ps = _load_module("plan_status_2229", _SCRIPTS / "plan-status.py")
_SKILL_ROOT_REL = _SKILL_ROOT


def _s5_make_repo(tmp: Path) -> Path:
    """夹具：流 Next Step 18 条已完成 + 1 条未完成；portfolio 同口径；各段超大。"""
    done_lines = [f"- [x] 已完成事项甲乙丙{i:02d}" for i in range(18)]
    unique_unfinished = "- [ ] 未完成独有步骤-S5-UNFINISHED-STEP"
    stream_next = "\n".join(done_lines + [unique_unfinished])
    body = "\n".join([
        "# 流 plan", "", "## Goal", "流目标。", "",
        "## Next Step", stream_next, "",
        "## Phases", "",
        "### Phase 1: 阶段一", "**Status:** in_progress", "",
        "## 备注", "", "注" * 20000, "",
    ]) + "\n"
    portfolio = "\n".join([
        "# 测试项目组合 plan", "", "## Goal", "组合目标：推进多条流。", "",
        "## Next Step",
        *done_lines,
        "组合未完成独有步骤-S5-PORTFOLIO-UNFINISHED", "",
        "## 最近完成", "- [x] 最近完成甲",
        "- [x] 最近完成长行一-S4-DONE-A " + "甲" * 400,
        "- [x] 最近完成长行二-S4-DONE-B " + "乙" * 400, "",
        "## Phases", "",
        "### Phase 1: 流A", "- plan: `.planning/stream-a/task_plan.md`",
        "**Status:** pending", "",
        "### Phase 2: 流B", "- plan: `.planning/stream-b/task_plan.md`",
        "**Status:** pending", "",
        "### Phase 3: 流C", "- plan: `.planning/stream-c/task_plan.md`",
        "**Status:** pending", "",
    ]) + "\n"
    root = tmp / "repo"
    planning = root / ".planning"
    (planning / "portfolio").mkdir(parents=True)
    for name in ("stream-a", "stream-b", "stream-c"):
        (planning / name).mkdir(parents=True)
        (planning / name / "task_plan.md").write_text(body, encoding="utf-8")
    (planning / "portfolio" / "task_plan.md").write_text(portfolio, encoding="utf-8")
    # S4：3 条超长 progress 条目（各 >512 字符）→ 渲染后必然超 8KB 触发确定性截断。
    progress_lines = ["# Progress", "", "## 自动驾驶里程", ""]
    for day in ("01", "02", "03"):
        progress_lines.append(f"- 2026-11-{day} 进度条目前缀-S4-PROGRESS " + "长" * 1200)
    (planning / "portfolio" / "progress.md").write_text(
        "\n".join(progress_lines) + "\n", encoding="utf-8")
    return root


def _ps_run(root: Path, *args: str):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = ps.main(["--root", str(root), *args])
    return rc, out.getvalue(), err.getvalue()


class S4HandoverHardCapTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = _s5_make_repo(Path(self._tmp.name))

    def test_s4_constants_output_cap_8kb_budget_unchanged(self):
        self.assertEqual(ps.HANDOVER_MAX_BYTES, 8192, "输出硬上限改 8KB")
        self.assertEqual(ps.HANDOVER_BUDGET_BYTES, 32 * 1024, "必读预算 32KB 语义不变")

    def test_s4_oversized_output_hard_capped_and_deterministic(self):
        rc, out, err = _ps_run(self.root, "--handover")
        self.assertEqual(rc, 0, err)
        self.assertLessEqual(len(out.encode("utf-8")), 8192, "stdout 硬 ≤8KB")
        self.assertIn("已截断，详情见 plan", out)
        _rc, again, _err = _ps_run(self.root, "--handover")
        self.assertEqual(out, again, "两次输出逐字节一致（确定性）")

    def test_s4_budget_line_still_32k_threshold(self):
        _rc, out, _err = _ps_run(self.root, "--handover")
        self.assertIn("阈值 32768", out, "必读预算阈值文本不变")

    def test_s4_source_and_template_text_updated(self):
        src = (_SCRIPTS / "plan-status.py").read_text(encoding="utf-8")
        self.assertNotIn("输出均硬 ≤32KB", src, "docstring 口径同步")
        self.assertNotIn("≤32KB 交接摘要", src, "模块头注释口径同步")
        for runtime in ("qoder", "opencode", "claude"):
            text = (_SKILL_ROOT_REL / "templates" / "agents" / runtime
                    / "helm.md").read_text(encoding="utf-8")
            self.assertIn("`--handover` 输出自身硬 ≤8KB", text,
                          f"{runtime} helm 模板输出上限表述同步")
            self.assertIn("交接必读 ≤32KB", text, "读取预算 32KB 保持")


class S5NextStepUnfinishedTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = _s5_make_repo(Path(self._tmp.name))
        self.unfinished = "未完成独有步骤-S5-UNFINISHED-STEP"
        self.portfolio_unfinished = "组合未完成独有步骤-S5-PORTFOLIO-UNFINISHED"
        self.done_sample = "已完成事项甲乙丙00"

    def test_s5_pick_next_step_pure_function(self):
        lines = [f"- [x] 已完成事项-{i:02d}" for i in range(18)] + ["- [ ] 唯一未完成步骤"]
        text, done = ps._pick_next_step(lines)
        self.assertEqual(text, "- [ ] 唯一未完成步骤")
        self.assertEqual(done, 18)

    def test_s5_pick_next_step_done_markers_and_indent(self):
        for marker in ("- [x] 甲", "- [X] 甲", "- ✅ 甲", "✅ 甲", "  - [x] 甲"):
            text, done = ps._pick_next_step([marker, "- [ ] 乙"])
            self.assertEqual(text, "- [ ] 乙", marker)
            self.assertEqual(done, 1)

    def test_s5_pick_next_step_all_done_or_empty(self):
        text, done = ps._pick_next_step(["- [x] 甲", "- [x] 乙"])
        self.assertEqual((text, done), ("—", 2))
        text, done = ps._pick_next_step([])
        self.assertEqual((text, done), ("—", 0))

    def _assert_stream_rows(self, out: str, label: str):
        self.assertIn(self.unfinished, out, f"{label} 应含未完成首条")
        self.assertIn("另有 18 条已完成", out, f"{label} 应含折叠计数")
        self.assertNotIn(self.done_sample, out, f"{label} 不得出现已完成全文")

    def test_s5_handover_active_stream_and_portfolio(self):
        rc, out, err = _ps_run(self.root, "--handover")
        self.assertEqual(rc, 0, err)
        self._assert_stream_rows(out, "--handover")
        self.assertIn(self.portfolio_unfinished, out, "portfolio Next Step 同口径")

    def test_s5_brief_stream_table(self):
        rc, out, err = _ps_run(self.root, "--brief")
        self.assertEqual(rc, 0, err)
        self._assert_stream_rows(out, "--brief")

    def test_s5_templates_grep_six_files(self):
        for runtime in ("qoder", "opencode", "claude"):
            helm = (_SKILL_ROOT_REL / "templates" / "agents" / runtime
                    / "helm.md").read_text(encoding="utf-8")
            self.assertIn("Next Step **未完成**首条", helm, f"{runtime} helm 读取范围")
            self.assertIn("跳过 `- [x]` 已完成项，折叠计数", helm, f"{runtime} helm 口径")
            firstmate = (_SKILL_ROOT_REL / "templates" / "agents" / runtime
                         / "firstmate.md").read_text(encoding="utf-8")
            self.assertIn("Next Step 未完成首条", firstmate, f"{runtime} firstmate 换舵段")


# ---------------------------------------------------------------------------
# 版本 2.2.32 四处一致（release-check version.source 口径）
# ---------------------------------------------------------------------------


class Version2229Test(unittest.TestCase):
    def test_version_four_places_consistent(self):
        skill = (_SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("version: 2.2.32", skill, "SKILL.md frontmatter version")
        self.assertIn("v2.2.32", skill, "SKILL.md 标题版本")
        changelog = (_SKILL_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        head = changelog[:3000]
        self.assertTrue(
            "2.2.32" in head.split("2.2.28", 1)[0] if "2.2.28" in head else "2.2.32" in head,
            "CHANGELOG 最新版本段应为 2.2.32",
        )
        init_src = (_SCRIPTS / "init.py").read_text(encoding="utf-8")
        self.assertIn('SKILL_VERSION_FALLBACK = "2.2.32"', init_src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
