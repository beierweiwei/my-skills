#!/usr/bin/env python3
"""T46 交接/总舵启动 YOLO 缺口（FB-037）回归。

两条启动路径的 `dispatch start` 必须带 `--permission-mode bypass_permissions`：

1. `crew-guard.py` 自动交接：注入 dispatch 记录 argv 断言（不触真实 herdr/agent）。
2. `start-helm.sh` 一键启动：脚本文本/结构化断言。
   不用运行夹具的原因：start-helm.sh 会真实创建 herdr tab/pane、启动 qoder agent
   并发送接手指令（依赖 live herdr server 与真实会话），单测夹具无法隔离这些副作用，
   也不应触碰真实总舵；因此断言脚本里实际执行的 dispatch start 命令行文本。
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent

_cg_spec = importlib.util.spec_from_file_location(
    "crew_guard_t46", SKILL_DIR / "scripts" / "crew-guard.py")
cg = importlib.util.module_from_spec(_cg_spec)
sys.modules["crew_guard_t46"] = cg  # dataclass 注解解析需要模块已注册
_cg_spec.loader.exec_module(cg)

from test_crew_guard import EMPTY_PORTFOLIO, EMPTY_STREAM, GuardTestBase  # noqa: E402


class HandoffYoloTest(GuardTestBase):
    OLD_PANE = "wX:pOLD"

    def prepare_handoff(self, ctx_k: int = 650) -> None:
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.agents = [
            {"name": "helm-1", "pane_id": self.OLD_PANE, "tab_id": "wX:tOLD",
             "workspace_id": "wX", "agent_status": "working"}
        ]
        self.pane_outputs[self.OLD_PANE] = f"status line ctx ({ctx_k}k/1M) tail"

    def test_handoff_start_no_manual_permission_mode(self):
        # 2.2.32/T76/C2：交接 start 不再手传 --permission-mode（YOLO 由配置
        # agents.permissionMode 承担）；role/启动口径不回退
        self.prepare_handoff()
        self.make_guard().run_once()

        start_calls = [c for c in self.dispatch_calls if c[:1] == ["start"]]
        self.assertEqual(1, len(start_calls), self.dispatch_calls)
        argv = start_calls[0]
        self.assertNotIn("--permission-mode", argv, argv)
        self.assertIn("--role", argv)
        self.assertEqual("helm", argv[argv.index("--role") + 1])
        self.assertIn("--pane", argv)
        self.assertIn("--name", argv)


class StartHelmScriptYoloTest(unittest.TestCase):
    SCRIPT = SKILL_DIR / "scripts" / "start-helm.sh"

    def test_dispatch_start_no_manual_permission_mode(self):
        # 2.2.32/T76/C2：start-helm.sh 不再手传 --permission-mode——YOLO 来源
        # 改 orchestration.json agents.permissionMode=bypass_permissions（配置）
        text = self.SCRIPT.read_text(encoding="utf-8")
        start_lines = [
            line for line in text.splitlines()
            if "start --role helm" in line and not line.lstrip().startswith("#")
        ]
        self.assertEqual(1, len(start_lines), text)
        self.assertNotIn("--permission-mode", start_lines[0])


if __name__ == "__main__":
    unittest.main()
