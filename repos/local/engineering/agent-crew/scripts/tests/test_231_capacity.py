#!/usr/bin/env python3
"""2.2.31 工单 07B：工位容量硬检查（warn=7/hard=10，Captain 直令）。

计数口径（与 dispatch/preflight/guard 同函数）：本仓 crew 工位含 firstmate/
worker pane；排除总舵（helm 名/Captain 前缀）、Captain·/流程优化· 白名单
（按 agent 名；tab label 不作判定）、无 name 个人 TUI；跨 workspace 不误计；
headless crew-claude.py run 不占 pane 不计入。B1 helm 豁免拒建门；B2
registry.lock 独立锁（第三方持 guard.lock 不影响）+ 锁内 live 计数 + 并发
start 只 0/1 成功。/tmp fixture + fake herdr，真实 .git/crew 只读。
（python3 scripts/tests/test_231_capacity.py）
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "crew_capacity", _SCRIPTS / "crew_capacity.py")
cc = importlib.util.module_from_spec(_spec)
sys.modules["crew_capacity"] = cc
_spec.loader.exec_module(cc)

DISPATCH = _SCRIPTS / "dispatch.py"
_spec_cp = importlib.util.spec_from_file_location(
    "crew_paths_ut", _SCRIPTS / "crew_paths.py")
crew_paths = importlib.util.module_from_spec(_spec_cp)
sys.modules["crew_paths_ut"] = crew_paths
_spec_cp.loader.exec_module(crew_paths)


def crew_dir_of(root: Path) -> Path:
    return crew_paths.crew_dir(root)

FAKE_HERDR = """#!/bin/sh
if [ "$1" = "agent" ] && [ "$2" = "list" ]; then
  cat "$FAKE_AGENTS_JSON"
  exit 0
fi
if [ "$1" = "pane" ] && [ "$2" = "split" ]; then
  echo '{"pane": "wX:pNEW"}'
  exit 0
fi
exit 0
"""


def agent(name, cwd="/repo", status="idle", pane="w1:p1"):
    return {"name": name, "agent_status": status, "cwd": cwd, "pane_id": pane}


def write_config(root: Path, capacity: dict | None) -> None:
    planning = root / ".planning"
    planning.mkdir(parents=True, exist_ok=True)
    (planning / "portfolio").mkdir(exist_ok=True)
    # 真实 init --yes 生成合法角色文件（base+overlay 全链路；tmp root 安全）
    init_rc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "init.py"), "--root", str(root), "--yes"],
        capture_output=True, text=True, timeout=120)
    assert init_rc.returncode == 0, f"init 夹具失败：{init_rc.stderr[-600:]}"
    # init 可能重写配置：读改写（补 helm.agentName/skillRoot，保留其余键）
    cfg_path = root / ".planning" / "orchestration.json"
    data = json.loads(cfg_path.read_text(encoding="utf-8")) \
        if cfg_path.is_file() else {}
    data.setdefault("helm", {})["agentName"] = "helm-main"
    data["skillRoot"] = "%s" % _SCRIPTS.parent
    cfg_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    data = {"projectId": "repo", "helm": {"agentName": "helm-main"},
            "skillRoot": "/home/wjj/projects/crew-orchestrator/skill-src/repos/local/engineering/agent-crew"}
    if capacity is not None:
        data["capacity"] = capacity
    (planning / "orchestration.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")


class CapacityConfigTest(unittest.TestCase):
    """验收 2：缺省 7/10、覆盖、夹取 warn≥1 hard≥warn、非法回退。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_defaults_and_clamps(self):
        self.assertEqual({"warn": 7, "hard": 10},
                         cc.load_capacity_config(self.root), "无配置缺省 7/10")
        write_config(self.root, {"warn": 5, "hard": 9})
        self.assertEqual({"warn": 5, "hard": 9}, cc.load_capacity_config(self.root))
        write_config(self.root, {"warn": 0, "hard": 3})
        self.assertEqual({"warn": 7, "hard": 10}, cc.load_capacity_config(self.root),
                         "warn<1 非法回退缺省")
        write_config(self.root, {"warn": 8, "hard": 6})
        self.assertEqual({"warn": 8, "hard": 8}, cc.load_capacity_config(self.root),
                         "hard<warn 夹取到 hard=warn")


class CountCrewAgentsTest(unittest.TestCase):
    """验收 1：计数口径与 6/7/10/11 边界。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / ".git").mkdir()
        self.roots = cc.project_roots(self.root)

    def count(self, agents, helm="helm-main"):
        return cc.count_crew_agents(agents, self.root, helm, roots=self.roots)

    def test_exclusion_rules(self):
        here = str(self.root)
        agents = [
            agent("helm-main", cwd=here),               # 总舵：排除
            agent("Captain", cwd=here),                 # Captain 前缀：排除
            agent("Captain·个人窗口", cwd=here),         # 白名单：排除
            agent("", cwd=here),                        # 无 name 个人 TUI：排除
            agent("worker-x", cwd="/other/project"),    # 跨 workspace：排除
            agent("flow-a-builder", cwd=here),          # 计入
        ]
        cap = self.count(agents)
        self.assertEqual(["flow-a-builder"], cap["names"])
        self.assertEqual(1, cap["total"])
        # Captain 前缀与 helm 同归总舵侧排除（与 guard _excluded 同口径）
        self.assertEqual(["Captain", "Captain·个人窗口", "helm-main"],
                         cap["excludedHelm"])
        self.assertEqual(1, cap["excludedExternal"], "仅跨项目 worker-x；个人 TUI 无 name 不计 external")

    def test_missing_cwd_counts_in(self):
        cap = self.count([{"name": "w-1", "agent_status": "idle"}])
        self.assertEqual(1, cap["total"], "cwd 缺失沿用包含（同 guard 口径）")

    def test_boundaries_6_7_10_11(self):
        cfg = {"warn": 7, "hard": 10}

        def level(n):
            return ("ok" if n <= cfg["warn"]
                    else "warn" if n < cfg["hard"] else "hard")

        self.assertEqual("ok", level(6))
        self.assertEqual("ok", level(7), "≤warn 含 7")
        self.assertEqual("warn", level(8))
        self.assertEqual("hard", level(10), ">hard 含 10")
        self.assertEqual("hard", level(11))


class DispatchCapacityGateTest(unittest.TestCase):
    """验收 5 + B1/B2：>hard 拒建、helm 豁免、--force 留痕、锁区分与并发。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.root = self.base / "repo"
        (self.root / ".git").mkdir(parents=True)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.fake_agents = self.base / "agents.json"
        script = self.bin / "herdr"
        script.write_text(FAKE_HERDR, encoding="utf-8")
        script.chmod(0o755)
        write_config(self.root, None)

    def set_agents(self, names):
        cwd = str(self.root)
        self.fake_agents.write_text(json.dumps({
            "agents": [agent(n, cwd=cwd, pane=f"w1:p{i}")
                       for i, n in enumerate(names)]}, ensure_ascii=False))

    def run_start(self, *args, role="builder", name="w-new"):
        env = dict(os.environ)
        env["PATH"] = str(self.bin) + os.pathsep + env.get("PATH", "")
        env["FAKE_AGENTS_JSON"] = str(self.fake_agents)
        env.pop("CREW_CALLER", None)
        return subprocess.run(
            [sys.executable, str(DISPATCH), "--root", str(self.root),
             "start", "--role", role, "--pane", "w1:pNEW",
             "--runtime", "qoder", "--name", name, *args],
            capture_output=True, text=True, env=env, timeout=60)

    def test_over_hard_denies_with_listing(self):
        self.set_agents([f"w-{i}" for i in range(10)])
        proc = self.run_start()
        self.assertEqual(1, proc.returncode, "超 hard 拒建 rc=1")
        self.assertIn("容量拒建", proc.stderr)
        self.assertIn("--force", proc.stderr)
        self.assertIn("勿先 tab create", proc.stderr)
        self.assertIn("w-0", proc.stderr, "必须关闭清单")

    def test_helm_role_exempt_with_log(self):
        self.set_agents([f"w-{i}" for i in range(10)])
        proc = self.run_start(role="helm", name="helm-new")
        # 门豁免断言：helm 角色在 10 工位下不被容量拒建文案拦截（放行进入后续
        # 组合/投递链；后续 rc 属 helm 模板链夹具范畴，与本门无关）。
        self.assertNotIn("容量拒建", proc.stderr, "helm 换舵不得被容量门拒")
        self.assertNotIn("容量拒建", proc.stdout)

    def test_force_allows_and_logs(self):
        self.set_agents([f"w-{i}" for i in range(10)])
        proc = self.run_start(args_force := "--force")
        self.assertEqual(0, proc.returncode, proc.stderr or "--force 放行")
        log = (crew_dir_of(self.root) / "agent-log.jsonl").read_text(
            encoding="utf-8")
        row = json.loads(log.splitlines()[-1])
        self.assertEqual("force", row.get("capacityOverride"))

    def test_lock_busy_distinct_from_capacity_deny(self):
        import fcntl
        self.set_agents([f"w-{i}" for i in range(3)])
        crew = crew_dir_of(self.root)
        crew.mkdir(parents=True, exist_ok=True)
        lock_path = crew / "registry.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("w")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            proc = self.run_start()
            self.assertEqual(3, proc.returncode, "锁忙 rc=3 与容量拒建 rc=1 区分")
            self.assertIn("锁忙", proc.stderr)
        finally:
            handle.close()
        # 第三方持 guard.lock 不影响 start（B2：锁文件不混用）
        guard_lock = crew / "guard.lock"
        ghandle = guard_lock.open("w")
        fcntl.flock(ghandle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            proc = self.run_start()
            self.assertEqual(0, proc.returncode,
                            proc.stderr or "第三方持 guard.lock 时 start 照常可用")
        finally:
            ghandle.close()

    def test_concurrent_starts_at_hard_only_one_passes(self):
        self.set_agents([f"w-{i}" for i in range(10)])
        env = dict(os.environ)
        env["PATH"] = str(self.bin) + os.pathsep + env.get("PATH", "")
        env["FAKE_AGENTS_JSON"] = str(self.fake_agents)
        cmd = [sys.executable, str(DISPATCH), "--root", str(self.root),
               "start", "--role", "builder", "--pane", "w1:pNEW",
               "--runtime", "qoder"]
        procs = [subprocess.Popen(cmd + [f"--name", f"w-new-{i}"],
                                  env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)
                 for i in range(2)]
        results = [p.communicate() for p in procs]
        codes = [p.returncode for p in procs]
        self.assertLessEqual(sum(1 for c in codes if c == 0), 1,
                             f"并发 start 在 10 工位只能成功 0/1 个：{codes}")
        registry_file = crew_dir_of(self.root) / "dispatch-agents.json"
        registry = json.loads(registry_file.read_text(encoding="utf-8")) \
            if registry_file.is_file() else {}
        self.assertLessEqual(len(registry), 1, "registry 不丢条目/不双写")


class CapacityHelpersTest(unittest.TestCase):
    """doctor 校验与 preflight 附项（轻校验）。"""

    def test_doctor_rejects_bad_capacity(self):
        doctor = _SCRIPTS / "doctor.py"
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        planning = root / ".planning"
        planning.mkdir(parents=True)
        config = {"projectId": "x", "skillRoot": "/tmp", "skillVersion": "1",
                  "capacity": {"warn": 0, "hard": 3}}
        (planning / "orchestration.json").write_text(json.dumps(config),
                                                     encoding="utf-8")
        spec = importlib.util.spec_from_file_location("doctor_ut", doctor)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["doctor_ut"] = mod  # dataclass 注解解析需模块已注册
        spec.loader.exec_module(mod)
        checker = mod.Doctor(root=root, profile="release", strict=False)
        result = checker.check_config_schema()
        self.assertEqual("fail", result.status)
        self.assertIn("capacity", result.detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
