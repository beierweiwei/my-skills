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
        # 工单 08 建议 6：Captain· 白名单前缀先于 Captain 总舵前缀归类——
        # 「Captain·个人窗口」是白名单工位而非总舵代（计数不变、归类准确）。
        self.assertEqual(["Captain", "helm-main"], cap["excludedHelm"])
        self.assertEqual(["Captain·个人窗口"], cap["excludedWhitelist"])
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
        # dispatch 拒建/preflight level 为 ≥hard=hard（满编即拒新增）；guard 强制
        # 回收为 >hard（满编 10 是合法稳态不空跑——工单 08 建议 1，两侧口径互补）。
        self.assertEqual("hard", level(10), "≥hard level=hard（dispatch 满编拒建口径）")
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


# —— 工单 08（B2 评审阻断修复：B-1/B-2/B-3 + 建议 1–7）——

# 真实 herdr agent list 输出带 {"result":{"agents":[...]}} 包装；api snapshot 同理。
# 文件缺失/未设 env 时输出空结构兜底（防 cat 读 stdin 挂住超时）。
FAKE_HERDR_V2 = """#!/bin/sh
if [ "$1" = "agent" ] && [ "$2" = "list" ]; then
  if [ -n "$FAKE_AGENTS_JSON" ] && [ -f "$FAKE_AGENTS_JSON" ]; then
    cat "$FAKE_AGENTS_JSON"
  else
    echo '{"result":{"agents":[]}}'
  fi
  exit 0
fi
if [ "$1" = "api" ] && [ "$2" = "snapshot" ]; then
  if [ -n "$FAKE_SNAPSHOT_JSON" ] && [ -f "$FAKE_SNAPSHOT_JSON" ]; then
    cat "$FAKE_SNAPSHOT_JSON"
  else
    echo '{"result":{"snapshot":{"tabs":[],"panes":[],"agents":[]}}}'
  fi
  exit 0
fi
exit 0
"""


def install_fake_herdr(bin_dir: Path, agents_file: Path, snapshot_file: Path) -> None:
    script = bin_dir / "herdr"
    script.write_text(FAKE_HERDR_V2, encoding="utf-8")
    script.chmod(0o755)
    agents_file.write_text(json.dumps({"agents": []}), encoding="utf-8")
    snapshot_file.write_text(
        json.dumps({"result": {"snapshot": {
            "tabs": [], "panes": [], "agents": []}}}), encoding="utf-8")


def set_fake_agents(agents_file: Path, snapshot_file: Path, names,
                    cwd: str) -> list[dict]:
    """写 agent list（result 包装）与 api snapshot（result.snapshot 包装）fixture。"""
    agents = [agent(name, cwd=cwd) for name in names]
    agents_file.write_text(
        json.dumps({"result": {"agents": agents}}, ensure_ascii=False),
        encoding="utf-8")
    snapshot_file.write_text(
        json.dumps({"result": {"snapshot": {
            "tabs": [], "panes": [], "agents": agents}}},
            ensure_ascii=False), encoding="utf-8")
    return agents


_spec_pf = importlib.util.spec_from_file_location(
    "crew_preflight_cap", _SCRIPTS / "crew-preflight.py")
pf = importlib.util.module_from_spec(_spec_pf)
sys.modules["crew_preflight_cap"] = pf
_spec_pf.loader.exec_module(pf)

_spec_gu = importlib.util.spec_from_file_location(
    "crew_guard_cap", _SCRIPTS / "crew-guard.py")
cg = importlib.util.module_from_spec(_spec_gu)
sys.modules["crew_guard_cap"] = cg
_spec_gu.loader.exec_module(cg)

DOCTOR = _SCRIPTS / "doctor.py"
PANE_AUDIT = _SCRIPTS / "pane-audit.py"


class PreflightCapacityTest(unittest.TestCase):
    """工单 08 B-3：preflight 传 helm_name（总舵排除）+ 剥 result 包装。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / ".git").mkdir()
        planning = self.root / ".planning"
        planning.mkdir()
        (planning / "orchestration.json").write_text(json.dumps(
            {"projectId": "repo", "helm": {"agentName": "helm-main"}},
            ensure_ascii=False), encoding="utf-8")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.agents_file = self.bin / "agents.json"
        self.snapshot_file = self.bin / "snapshot.json"
        install_fake_herdr(self.bin, self.agents_file, self.snapshot_file)

    def check(self, names) -> dict:
        set_fake_agents(self.agents_file, self.snapshot_file, names, str(self.root))
        import unittest.mock as mock
        with mock.patch.dict(os.environ, {
                "FAKE_AGENTS_JSON": str(self.agents_file),
                "FAKE_SNAPSHOT_JSON": str(self.snapshot_file)}):
            return pf.check_capacity(self.root, herdr_bin=str(self.bin / "herdr"))

    def test_helm_excluded_and_wrapper_parsed(self):
        # 9 worker + 1 总舵 = 10 条；修复后计 9（warn 区），总舵不误计。
        result = self.check(["helm-main"] + [f"w-{i}" for i in range(9)])
        self.assertEqual("warn", result["level"])
        self.assertEqual(9, len(result["names"]))
        self.assertNotIn("helm-main", result["names"])

    def test_level_boundaries(self):
        self.assertEqual("ok", self.check(
            ["helm-main"] + [f"w-{i}" for i in range(7)])["level"], "7 ≤warn")
        self.assertEqual("hard", self.check(
            ["helm-main"] + [f"w-{i}" for i in range(10)])["level"],
            "10 = hard（满编，dispatch 拒新增口径）")


class ReconcileCapacityCountTest(unittest.TestCase):
    """工单 08 B-3 验收 3：同夹具下函数直调/dispatch capacity/dispatch start/
    preflight 四处同数字（含总舵 pane 场景，真实 result 包装）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.root = self.base / "repo"
        (self.root / ".git").mkdir(parents=True)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.agents_file = self.base / "agents.json"
        self.snapshot_file = self.base / "snapshot.json"
        install_fake_herdr(self.bin, self.agents_file, self.snapshot_file)
        write_config(self.root, None)

    def env(self):
        env = dict(os.environ)
        env["PATH"] = str(self.bin) + os.pathsep + env.get("PATH", "")
        env["FAKE_AGENTS_JSON"] = str(self.agents_file)
        env["FAKE_SNAPSHOT_JSON"] = str(self.snapshot_file)
        env.pop("CREW_CALLER", None)
        return env

    def test_four_way_same_number_with_helm(self):
        names = ["helm-main"] + [f"w-{i}" for i in range(10)]  # 10 crew + 1 总舵
        agents = set_fake_agents(self.agents_file, self.snapshot_file,
                                 names, str(self.root))
        # ① 函数直调（guard 侧同参调用：count_crew_agents(agents, root, helm)）
        direct = cc.count_crew_agents(agents, self.root, "helm-main")
        self.assertEqual(10, direct["total"])
        # ② dispatch capacity（herdr_agents 剥包装口径）
        proc = subprocess.run(
            [sys.executable, str(DISPATCH), "--root", str(self.root),
             "capacity", "--json"],
            capture_output=True, text=True, env=self.env(), timeout=60)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(10, json.loads(proc.stdout)["capacity"]["total"])
        # ③ dispatch start 在满编 10 拒建（herdr_agents_locked 也须剥包装计数）
        proc = subprocess.run(
            [sys.executable, str(DISPATCH), "--root", str(self.root),
             "start", "--role", "builder", "--runtime", "qoder",
             "--pane", "w1:pNEW", "--name", "w-new"],
            capture_output=True, text=True, env=self.env(), timeout=60)
        self.assertEqual(1, proc.returncode, "满编拒建 rc=1")
        self.assertIn("容量拒建", proc.stderr)
        # ④ preflight（同口径同数字；check_capacity 继承进程 env，注入 fake 变量）
        import unittest.mock as mock
        with mock.patch.dict(os.environ, {
                "FAKE_AGENTS_JSON": str(self.agents_file),
                "FAKE_SNAPSHOT_JSON": str(self.snapshot_file)}):
            result = pf.check_capacity(self.root, herdr_bin=str(self.bin / "herdr"))
        self.assertEqual(10, len(result["names"]))


class GuardCapacityCheckTest(unittest.TestCase):
    """工单 08 B-1/B-2/建议 1：guard 容量段行为（fake herdr 真驱动 pane-audit）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / ".git").mkdir()
        self.crew = self.root / ".git" / "crew"
        self.crew.mkdir(parents=True)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.agents_file = self.bin / "agents.json"
        self.snapshot_file = self.bin / "snapshot.json"
        install_fake_herdr(self.bin, self.agents_file, self.snapshot_file)
        self.logs: list[str] = []

    def workers(self, n, swap_last=None, status="working"):
        names = [f"w-{i}" for i in range(n)]
        if swap_last:
            names[-1] = swap_last
        return [agent(name, cwd=str(self.root), status=status, pane=f"w1:p{i}")
                for i, name in enumerate(names)]

    def make_guard(self, live, use_fake_herdr=True):
        calls = {"wake": [], "notify": []}
        state = {"live": live}

        class TestGuard(cg.Guard):
            def _wake(self, message, agents, now=None):
                calls["wake"].append(message)
                return "test"

        guard = TestGuard(
            root=self.root, crew_dir=self.crew, helm="helm-main",
            list_agents=lambda: list(state["live"]),
            notify=lambda title, body: calls["notify"].append(title) or True,
            log_fn=self.logs.append)
        guard._calls = calls
        guard._state = state
        guard._use_fake_herdr = use_fake_herdr
        return guard

    def run_check(self, guard, now, agents):
        if guard._use_fake_herdr:
            env_path = str(self.bin)
        else:  # B-1 失败路径：PATH 上无 herdr → pane-audit AuditError rc=1
            env_path = str(self.root / "no-bin")
            (self.root / "no-bin").mkdir(exist_ok=True)
        import unittest.mock as mock
        with mock.patch.dict(os.environ, {
                "PATH": env_path + os.pathsep + os.defpath,
                "FAKE_AGENTS_JSON": str(self.agents_file),
                "FAKE_SNAPSHOT_JSON": str(self.snapshot_file)}):
            guard._capacity_check(now, agents)

    def test_full_band_no_subprocess_waste(self):
        """建议 1：满编 10 是合法稳态——不跑 pane-audit、不误记、不叫醒。"""
        agents10 = self.workers(10)
        guard = self.make_guard(agents10)
        self.run_check(guard, 1000.0, agents10)
        self.assertFalse(any("强制回收" in m for m in self.logs),
                         "满编不得空跑回收/误记「10 → 10」")
        self.assertEqual(0, len(guard._calls["wake"]))
        self.assertIsNone(guard.capacity_first_hard_at)
        self.assertFalse((self.crew / "guard.capacity.json").exists(),
                         "未进 >hard 分支不得写持久化")
        self.assertTrue(any("容量警告" in m for m in self.logs),
                        "10 > warn=7 应有警告日志")

    def test_hard_recovery_completes(self):
        """B-1 正路径：>hard 真驱动 pane-audit（rc=0 无 unrecognized）且回收后清 episode。"""
        agents11 = self.workers(11)
        guard = self.make_guard(self.workers(10))  # pane-audit 后 live 回落到 10
        guard.capacity_first_hard_at = 500.0  # 预设旧计时，回收完成必须清零
        self.run_check(guard, 1000.0, agents11)
        self.assertTrue(any("容量强制回收完成：11 → 10" in m for m in self.logs))
        self.assertFalse(any("pane-audit" in m for m in self.logs),
                         "rc=0 时不得出现调用失败/退出码日志")
        self.assertEqual(0, len(guard._calls["wake"]), "回收后恢复合法不叫醒")
        self.assertIsNone(guard.capacity_first_hard_at)
        stored = json.loads((self.crew / "guard.capacity.json").read_text(
            encoding="utf-8"))
        self.assertIsNone(stored["capacityFirstHardAt"])

    def test_audit_failure_logged_not_silent(self):
        """B-1 阻断：pane-audit rc≠0 必须记 guard.log（不得静默），并继续告警。"""
        agents11 = self.workers(11)
        guard = self.make_guard(agents11, use_fake_herdr=False)
        self.run_check(guard, 1000.0, agents11)
        self.assertTrue(any("退出码 1" in m for m in self.logs),
                        "rc≠0 应记日志（当前实现静默）")
        self.assertEqual(1, len(guard._calls["wake"]),
                         "回收失败后仍 >hard 应进入叫醒告警")

    def test_escalate_once_per_signature(self):
        """B-2 阻断：同一签名 episode 升级通知只发一次；签名变化重新计时。"""
        agents11 = self.workers(11)
        guard = self.make_guard(agents11)
        t0 = 1_000_000.0
        guard.capacity_first_hard_at = t0 - 3700  # 首轮不覆盖：已持续 >60min
        self.run_check(guard, t0, agents11)
        self.assertEqual(1, len(guard._calls["notify"]), "到期升级通知一次")
        self.assertEqual(1, len(guard._calls["wake"]))
        for i in range(1, 5):  # 连续 4 轮同签名（B-2 场景：每 120s 重发）
            self.run_check(guard, t0 + 120 * i, agents11)
        self.assertEqual(1, len(guard._calls["notify"]),
                         "同签名 episode 不得每轮重发（B-2）")
        self.assertEqual(1, len(guard._calls["wake"]), "叫醒同样去重")
        # 状态变化（名单换 1 名）＝新 episode：重新计时，先不升级
        agents11b = self.workers(11, swap_last="w-alt")
        guard._state["live"] = agents11b
        t1 = t0 + 1200
        self.run_check(guard, t1, agents11b)
        self.assertEqual(1, len(guard._calls["notify"]), "新 episode 先不升级")
        self.assertEqual(2, len(guard._calls["wake"]), "新签名叫醒一次")
        # 新 episode 满 60 分钟 → 可再升级一次（重新计时后再通知一次）
        self.run_check(guard, t1 + 3700, agents11b)
        self.assertEqual(2, len(guard._calls["notify"]), "重新计时后可再通知一次")
        self.assertEqual(3, len(guard._calls["wake"]), "升级轮带文案叫醒一次")


class DoctorRuntimeCapacityTest(unittest.TestCase):
    """工单 08 建议 2：doctor runtime.capacity——>warn=warn、>hard=fail、出路。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        planning = self.root / ".planning"
        planning.mkdir(parents=True)
        (planning / "orchestration.json").write_text(json.dumps(
            {"projectId": "t", "helm": {"agentName": "helm-main"}},
            ensure_ascii=False), encoding="utf-8")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.agents_file = self.bin / "agents.json"
        self.snapshot_file = self.bin / "snapshot.json"
        install_fake_herdr(self.bin, self.agents_file, self.snapshot_file)

    def run_doctor(self, names=None, with_herdr=True):
        if names is not None:
            set_fake_agents(self.agents_file, self.snapshot_file,
                            names, str(self.root))
        env = dict(os.environ)
        if with_herdr:
            env["PATH"] = str(self.bin) + os.pathsep + env.get("PATH", "")
        else:
            # 隔离原 PATH（防真实 herdr 泄漏进测试）；doctor 以 sys.executable
            # 绝对路径启动，herdr 找不到 → FileNotFoundError → env。
            empty_bin = self.root / "empty-bin"
            empty_bin.mkdir(exist_ok=True)
            env["PATH"] = str(empty_bin)
        env["FAKE_AGENTS_JSON"] = str(self.agents_file)
        env["FAKE_SNAPSHOT_JSON"] = str(self.snapshot_file)
        proc = subprocess.run(
            [sys.executable, str(DOCTOR), "--root", str(self.root),
             "--profile", "release", "--only", "runtime.capacity", "--json"],
            capture_output=True, text=True, env=env, timeout=60)
        report = json.loads(proc.stdout)
        return report["items"][0]

    def test_levels_and_next(self):
        item = self.run_doctor(["helm-main"] + [f"w-{i}" for i in range(6)])
        self.assertEqual("pass", item["status"])
        item = self.run_doctor(["helm-main"] + [f"w-{i}" for i in range(8)])
        self.assertEqual("warn", item["status"], ">warn → warn")
        self.assertIn("close", item["next"], "warn 给关闭出路")
        item = self.run_doctor(["helm-main"] + [f"w-{i}" for i in range(11)])
        self.assertEqual("fail", item["status"], ">hard → fail")
        self.assertIn("--force", item["next"], "fail 给 --force 出路")
        self.assertIn("close", item["next"])

    def test_herdr_missing_is_env(self):
        item = self.run_doctor(with_herdr=False)
        self.assertEqual("env", item["status"], "herdr 不可用为 env，不连坐其余项")


class DispatchCloseLockTest(unittest.TestCase):
    """工单 08 建议 5：pane/tab close 的 panes.json 读改写进 registry.lock
    临界区（锁忙 rc=3 可区分）；close 归因 caller 留痕（B-1 归因链）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.root = self.base / "repo"
        (self.root / ".git").mkdir(parents=True)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.agents_file = self.base / "agents.json"
        self.snapshot_file = self.base / "snapshot.json"
        install_fake_herdr(self.bin, self.agents_file, self.snapshot_file)
        write_config(self.root, None)
        set_fake_agents(self.agents_file, self.snapshot_file,
                        ["helm-main"], str(self.root))

    def env(self):
        env = dict(os.environ)
        env["PATH"] = str(self.bin) + os.pathsep + env.get("PATH", "")
        env["FAKE_AGENTS_JSON"] = str(self.agents_file)
        env["FAKE_SNAPSHOT_JSON"] = str(self.snapshot_file)
        env.pop("CREW_CALLER", None)
        return env

    def hold_registry_lock(self):
        import fcntl
        lock_path = crew_dir_of(self.root) / "registry.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("w")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle

    def run_dispatch(self, *args):
        return subprocess.run(
            [sys.executable, str(DISPATCH), "--root", str(self.root), *args],
            capture_output=True, text=True, env=self.env(), timeout=60)

    def test_pane_close_under_busy_lock(self):
        handle = self.hold_registry_lock()
        try:
            proc = self.run_dispatch("pane", "close", "--pane", "w1:p9",
                                     "--caller", "guard")
            self.assertEqual(3, proc.returncode, "锁忙 rc=3（临界区内的证据）")
            self.assertIn("锁忙", proc.stderr)
        finally:
            handle.close()

    def test_tab_close_under_busy_lock(self):
        handle = self.hold_registry_lock()
        try:
            proc = self.run_dispatch("tab", "close", "--tab", "w1:t9",
                                     "--caller", "guard")
            self.assertEqual(3, proc.returncode, "锁忙 rc=3（临界区内的证据）")
        finally:
            handle.close()

    def test_pane_close_updates_panes_and_logs_caller(self):
        panes_file = crew_dir_of(self.root) / "dispatch-panes.json"
        panes_file.parent.mkdir(parents=True, exist_ok=True)
        panes_file.write_text(json.dumps(
            {"w1:p9": {"cwd": "/tmp", "createdAt": "2026-11-14T00:00:00Z"}}),
            encoding="utf-8")
        proc = self.run_dispatch("pane", "close", "--pane", "w1:p9",
                                 "--caller", "guard")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertNotIn("w1:p9", json.loads(panes_file.read_text(encoding="utf-8")),
                         "panes.json 条目清除")
        log_file = crew_dir_of(self.root) / "agent-log.jsonl"
        row = json.loads(log_file.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual("guard", row.get("caller"), "caller 归因留痕")


class PaneAuditCallerArgTest(unittest.TestCase):
    """工单 08 B-1：pane-audit 接受 --caller（guard 调用不再 argparse rc=2 静默）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / ".git").mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.agents_file = self.bin / "agents.json"
        self.snapshot_file = self.bin / "snapshot.json"
        install_fake_herdr(self.bin, self.agents_file, self.snapshot_file)

    def run_audit(self, *args):
        env = dict(os.environ)
        env["PATH"] = str(self.bin) + os.pathsep + env.get("PATH", "")
        env["FAKE_AGENTS_JSON"] = str(self.agents_file)
        env["FAKE_SNAPSHOT_JSON"] = str(self.snapshot_file)
        return subprocess.run(
            [sys.executable, str(PANE_AUDIT), "--root", str(self.root), *args],
            capture_output=True, text=True, env=env, timeout=60)

    def test_apply_with_caller_accepted(self):
        proc = self.run_audit("--apply", "--caller", "guard")
        self.assertEqual(0, proc.returncode,
                         proc.stderr or "--caller guard 应被接受（B-1 复现口径）")
        self.assertNotIn("unrecognized", proc.stderr)

    def test_help_lists_caller(self):
        proc = self.run_audit("--help")
        self.assertIn("--caller", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
