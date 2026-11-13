"""crew-model.py 单测（2.2.30 工单 A）：fake herdr + 临时 HOME，禁触真实 herdr/settings。"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "crew-model.py"
MODEL = "glm-5.3-flash"

# fake herdr：状态文件驱动（panes/reads/after），send-text 后把 reads[id] 换成 after[id]
HERDR_STUB = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["CREW_TEST_HERDR_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(args, ensure_ascii=False) + "\\n")
state_path = os.environ["CREW_TEST_HERDR_STATE"]
with open(state_path, encoding="utf-8") as fh:
    state = json.load(fh)
if args[:2] == ["pane", "list"]:
    print(json.dumps({"result": {"panes": state["panes"]}}))
elif args[:2] == ["pane", "read"]:
    sys.stdout.write(state["reads"].get(args[2], ""))
elif args[:2] == ["pane", "send-text"]:
    pane_id = args[2]
    if pane_id in state.get("after", {}):
        state["reads"][pane_id] = state["after"][pane_id]
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False)
    print(json.dumps({"result": {"ok": True}}))
else:
    print(json.dumps({"result": {"ok": True}}))
"""

PROMPT_QODER = "➜ Type your message"
ECHO_OK = f"Model set to {MODEL}\n[{MODEL}]  ctx 1k/1M"
ECHO_STALE = "still thinking about it…"


def pane(pane_id, agent, status, workspace="wT", title="w"):
    return {"pane_id": pane_id, "agent": agent, "agent_status": status,
            "workspace_id": workspace, "terminal_title_stripped": title}


class CrewModelTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.home = self.base / "home"
        (self.home / ".qoder-cn").mkdir(parents=True)
        (self.home / ".claude").mkdir(parents=True)
        self.qoder_settings = self.home / ".qoder-cn" / "settings.json"
        self.qoder_settings.write_text(json.dumps({
            "agents": {"overrides": {
                "helm": {"modelConfig": {"model": "DeepSeek-Flash"}},
                "builder": {"modelConfig": {"model": "DeepSeek-Flash"}},
            }},
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.claude_settings = self.home / ".claude" / "settings.json"
        self.claude_settings.write_text(json.dumps({
            "model": "old-model", "permissions": {"allow": ["Bash"]},
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.bin = self.base / "bin"
        self.bin.mkdir()
        stub = self.bin / "herdr"
        stub.write_text(HERDR_STUB, encoding="utf-8")
        stub.chmod(0o755)
        self.herdr_log = self.base / "herdr.log"
        self.state_path = self.base / "state.json"
        self.write_state(panes=[], reads={}, after={})

    # ---------- fixture ----------

    def write_state(self, panes, reads, after) -> None:
        self.state_path.write_text(json.dumps(
            {"panes": panes, "reads": reads, "after": after}, ensure_ascii=False),
            encoding="utf-8")

    def run_cli(self, *argv: str, workspace: str | None = "wT") -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["PATH"] = f"{self.bin}:{env.get('PATH', '')}"
        env["CREW_TEST_HERDR_LOG"] = str(self.herdr_log)
        env["CREW_TEST_HERDR_STATE"] = str(self.state_path)
        env["CREW_MODEL_VERIFY_DELAY"] = "0.05"
        env.pop("HERDR_WORKSPACE_ID", None)
        if workspace is not None:
            env["HERDR_WORKSPACE_ID"] = workspace
        return subprocess.run([sys.executable, str(SCRIPT), *argv],
                              capture_output=True, text=True, env=env, timeout=60)

    def herdr_calls(self) -> list[list[str]]:
        if not self.herdr_log.exists():
            return []
        return [json.loads(line) for line in
                self.herdr_log.read_text(encoding="utf-8").splitlines() if line.strip()]

    def send_calls(self) -> list[list[str]]:
        return [c for c in self.herdr_calls()
                if c[:2] == ["pane", "send-text"] or c[:2] == ["pane", "send-keys"]]

    @staticmethod
    def sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def default_panes(self) -> list[dict]:
        return [
            pane("wT:p1", "qodercli", "idle", title="worker-1"),
            pane("wT:p2", "qodercli", "working", title="worker-2-working"),
            pane("wT:p3", "qodercli", "idle", workspace="wOTHER", title="decoy-other-ws"),
            pane("wT:p4", "claude", "idle", title="claude-worker"),
            pane("wT:p5", "qodercli", "done", title="worker-5-no-prompt"),
        ]

    # ---------- 断言 1：help / 无默认模型 ----------

    def test_help_and_dry_run_flag(self):
        for argv in (("--help",), ("settings", "--help"), ("panes", "--help")):
            proc = self.run_cli(*argv)
            self.assertEqual(0, proc.returncode, proc.stderr)

    def test_missing_model_rejected(self):
        proc = self.run_cli("settings", "--runtime", "qoder", "--apply")
        self.assertEqual(2, proc.returncode, proc.stderr)
        self.assertIn("--model", proc.stderr)
        proc = self.run_cli("panes", "--runtime", "qoder")
        self.assertEqual(2, proc.returncode, proc.stderr)

    # ---------- 断言 2：settings qoder ----------

    def test_settings_dry_run_no_write(self):
        before = self.sha256(self.qoder_settings)
        proc = self.run_cli("settings", "--runtime", "qoder", "--model", MODEL)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(before, self.sha256(self.qoder_settings), "dry-run 落盘了")
        self.assertIn("DeepSeek-Flash", proc.stdout)
        self.assertIn(MODEL, proc.stdout)

    def test_settings_apply_backup_and_idempotent(self):
        original = self.qoder_settings.read_text(encoding="utf-8")
        proc = self.run_cli("settings", "--runtime", "qoder", "--model", MODEL, "--apply")
        self.assertEqual(0, proc.returncode, proc.stderr)
        data = json.loads(self.qoder_settings.read_text(encoding="utf-8"))
        overrides = data["agents"]["overrides"]
        self.assertTrue(all(c["modelConfig"]["model"] == MODEL for c in overrides.values()))
        baks = list(self.qoder_settings.parent.glob("settings.json.bak-*"))
        self.assertEqual(1, len(baks), "首次 apply 应生成一份备份")
        self.assertEqual(original, baks[0].read_text(encoding="utf-8"))
        first_apply = self.qoder_settings.read_text(encoding="utf-8")
        for _ in range(7):  # 幂等（重复 apply 内容稳定）+ 同秒备份不被覆盖 + 不无界堆积
            proc = self.run_cli("settings", "--runtime", "qoder", "--model", MODEL, "--apply")
            self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(first_apply, self.qoder_settings.read_text(encoding="utf-8"),
                         "重复 apply 幂等失败")
        baks = list(self.qoder_settings.parent.glob("settings.json.bak-*"))
        self.assertLessEqual(len(baks), 5, f"备份堆积：{baks}")
        self.assertGreaterEqual(len(baks), 2, f"同秒多次 apply 备份互被覆盖：{baks}")
        # 备份为 5 份滑动窗口：恢复走反向 --apply（幂等），不依赖首份原始备份

    def test_settings_qoder_empty_overrides_rejected(self):
        self.qoder_settings.write_text(json.dumps({"agents": {"overrides": {}}}),
                                       encoding="utf-8")
        proc = self.run_cli("settings", "--runtime", "qoder", "--model", MODEL, "--apply")
        self.assertEqual(1, proc.returncode)
        self.assertEqual(json.loads(self.qoder_settings.read_text(encoding="utf-8")),
                         {"agents": {"overrides": {} }}, "拒绝后不得半写")

    # ---------- 断言 3：settings claude ----------

    def test_settings_claude_top_level_only(self):
        before_sha = self.sha256(self.claude_settings)
        proc = self.run_cli("settings", "--runtime", "claude", "--model", MODEL)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(before_sha, self.sha256(self.claude_settings), "dry-run 落盘了")
        proc = self.run_cli("settings", "--runtime", "claude", "--model", MODEL, "--apply")
        self.assertEqual(0, proc.returncode, proc.stderr)
        data = json.loads(self.claude_settings.read_text(encoding="utf-8"))
        self.assertEqual(MODEL, data["model"])
        self.assertEqual({"allow": ["Bash"]}, data["permissions"], "只改顶层 model")
        self.assertIn("per-role", proc.stdout)

    # ---------- 断言 4：opencode / 未知运行时拒绝 ----------

    def test_settings_opencode_rejected(self):
        before = self.sha256(self.claude_settings)
        proc = self.run_cli("settings", "--runtime", "opencode", "--model", MODEL, "--apply")
        self.assertNotEqual(0, proc.returncode)
        self.assertIn("opencode", proc.stderr)
        self.assertIn("替代", proc.stderr)
        self.assertEqual(before, self.sha256(self.claude_settings), "拒绝后不得半写")

    def test_panes_opencode_rejected(self):
        proc = self.run_cli("panes", "--runtime", "opencode", "--model", MODEL, "--apply")
        self.assertNotEqual(0, proc.returncode)
        self.assertEqual([], self.send_calls(), "拒绝后不得注入")

    def test_unknown_runtime_argparse(self):
        proc = self.run_cli("settings", "--runtime", "codex", "--model", MODEL)
        self.assertEqual(2, proc.returncode)

    # ---------- 断言 5/7：panes 行为 ----------

    def _setup_panes(self, *, p1_echo: str = ECHO_OK) -> None:
        self.write_state(
            panes=self.default_panes(),
            reads={"wT:p1": PROMPT_QODER, "wT:p4": "❯ input here", "wT:p5": ECHO_STALE},
            after={"wT:p1": p1_echo, "wT:p4": f"[{MODEL}]  ctx"},
        )

    def test_panes_working_skipped_zero_inject(self):
        self._setup_panes()
        proc = self.run_cli("panes", "--runtime", "qoder", "--model", MODEL, "--apply")
        self.assertNotEqual(0, proc.returncode, "含失败项（p5 无提示符）")
        self.assertIn("worker-2-working", proc.stdout)
        self.assertIn("跳过", proc.stdout)
        for call in self.send_calls():
            self.assertNotEqual("wT:p2", call[2] if call[0] == "pane" else "",
                                f"working pane 被注入：{call}")

    def test_panes_decoy_not_injected(self):
        self._setup_panes()
        self.run_cli("panes", "--runtime", "qoder", "--model", MODEL, "--apply")
        for call in self.send_calls():
            self.assertNotEqual("wT:p3", call[2] if call[0] == "pane" else "",
                                f"非目标 workspace pane 被注入：{call}")

    def test_panes_no_prompt_not_switched(self):
        self._setup_panes()
        proc = self.run_cli("panes", "--runtime", "qoder", "--model", MODEL, "--apply")
        self.assertEqual(1, proc.returncode)
        self.assertIn("worker-5-no-prompt", proc.stdout)
        self.assertIn("未见输入提示符", proc.stdout)
        for call in self.send_calls():
            self.assertNotEqual("wT:p5", call[2] if call[0] == "pane" else "",
                                f"无提示符 pane 被硬切：{call}")

    def test_panes_success_flow(self):
        self._setup_panes()
        proc = self.run_cli("panes", "--runtime", "qoder", "--model", MODEL, "--apply")
        self.assertEqual(1, proc.returncode)  # p5 无提示符计失败，p1 应成功
        self.assertIn("[已切] wT:p1", proc.stdout)
        sends = [c for c in self.send_calls() if len(c) > 2 and c[2] == "wT:p1"]
        self.assertEqual(2, len(sends), f"p1 应一次 send-text + 一次 send-keys：{sends}")
        self.assertEqual(f"/model {MODEL}", sends[0][3])

    def test_panes_echo_not_confirmed_fails(self):
        self._setup_panes(p1_echo=ECHO_STALE)
        proc = self.run_cli("panes", "--runtime", "qoder", "--model", MODEL, "--apply")
        self.assertEqual(1, proc.returncode)
        self.assertIn("未确认生效", proc.stdout)
        self.assertIn(ECHO_STALE.strip().splitlines()[-1][:10], proc.stdout, "失败须带末行回显")

    def test_panes_dry_run_zero_write(self):
        self._setup_panes()
        proc = self.run_cli("panes", "--runtime", "qoder", "--model", MODEL, "--dry-run")
        self.assertEqual(1, proc.returncode, "预检仍报告将失败项")
        self.assertEqual([], self.send_calls(), "dry-run 出现写命令")
        self.assertIn("dry-run", proc.stdout)

    def test_panes_claude_runtime(self):
        self._setup_panes()
        proc = self.run_cli("panes", "--runtime", "claude", "--model", MODEL, "--apply")
        self.assertIn("[已切] wT:p4", proc.stdout)
        sends = [c for c in self.send_calls() if len(c) > 2 and c[2] == "wT:p4"]
        self.assertEqual(2, len(sends), f"claude pane 注入：{sends}")
        for call in self.send_calls():
            if len(call) > 2:
                self.assertNotEqual("wT:p1", call[2], "claude runtime 不得触碰 qoder pane")

    def test_panes_workspace_guard(self):
        self._setup_panes()
        proc = self.run_cli("panes", "--runtime", "qoder", "--model", MODEL,
                            workspace=None)
        self.assertNotEqual(0, proc.returncode)
        self.assertIn("--workspace", proc.stderr)
        self.assertEqual([], self.send_calls())

    def test_panes_explicit_workspace(self):
        self._setup_panes()
        proc = self.run_cli("panes", "--runtime", "qoder", "--model", MODEL,
                            "--workspace", "wOTHER", "--apply")
        self.assertNotEqual(0, proc.returncode)  # 诱饵 pane 无提示符 → 失败清单
        self.assertIn("wT:p3", proc.stdout)
        for call in self.send_calls():
            self.assertNotEqual("wT:p1", call[2], "显式 workspace 不得越界")


if __name__ == "__main__":
    unittest.main()
