#!/usr/bin/env python3
"""2.2.16 T43：`scripts/release-check.py` —— 发布就绪聚合检查（只读 pass/fail 表）。

覆盖：
- 全 pass：版本四处一致、安装 diff 干净、upgrade/init/doctor 正常、guard 在线；
- fail：版本不一致（源/项目/用户目录）、安装 diff、upgrade 待办、init/doctor/guard 失败；
- skip：缺用户目录、缺 manifest、缺 doctor/init、非 git 不崩；
- `source.git-clean`：源/my-skills 干净→pass，任一 dirty→fail，my-skills 不可定位→skip；
- `guard.helm`：运行中 guard `--helm` 与 dispatch registry 最新 helm 一致→pass，
  错舵名→fail（实际值 vs 期望值），非 guard 进程/不可解析→skip；
- 退出码 0/1/2/3；`--json` 结构；`--dry-run` 无副作用（夹具树快照不变）；
- `init.py SCRIPT_MANIFEST` 含 `release-check.py`（AMEND：条目随文件同切片加入）。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent.parent
SCRIPTS = SKILL / "scripts"
RELEASE = SCRIPTS / "release-check.py"
PYTHON = sys.executable

ROW_RE = re.compile(r"^(\S+)\s+(PASS|FAIL|SKIP)\s+(.*)$", re.MULTILINE)

# 源与项目共用同一份 init.py 桩：顶层常量供 upgrade.diff_report 的 AST 清单读取，
# __main__ 分支按环境变量模拟 init --check 的退出码（不影响 AST 解析）。
INIT_STUB = '''#!/usr/bin/env python3
import os
import sys

SCRIPT_MANIFEST = ["init.py", "doctor.py"]
SKILL_VERSION_FALLBACK = "2.2.16"

if __name__ == "__main__":
    rc = int(os.environ.get("STUB_INIT_RC", "0"))
    print(f"init: stub rc={rc}")
    sys.exit(rc)
'''

DOCTOR_STUB = '''#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
if "--only" in args:
    status = os.environ.get("STUB_GUARD_STATUS", "pass")
    print(json.dumps({"summary": {status: 1}, "items": [
        {"id": "runtime.guard", "status": status,
         "detail": os.environ.get("STUB_GUARD_DETAIL", "值守 guard 运行中（pid=1）")}]}))
    sys.exit(0 if status == "pass" else 1)
fail = int(os.environ.get("STUB_DOCTOR_FAIL", "0"))
warn = int(os.environ.get("STUB_DOCTOR_WARN", "0"))
summary = {"pass": max(0, 10 - fail - warn), "warn": warn, "fail": fail,
           "skip": 0, "env": 0, "pending": 0}
items = [{"id": f"stub.fail{i}", "status": "fail"} for i in range(fail)]
print(json.dumps({"summary": summary, "items": items}))
sys.exit(1 if (fail or warn) else 0)
'''

CLEAN_ENV = ("STUB_INIT_RC", "STUB_DOCTOR_FAIL", "STUB_DOCTOR_WARN",
             "STUB_GUARD_STATUS", "STUB_GUARD_DETAIL", "CREW_MY_SKILLS_ROOT")


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclass 注解解析需要模块已注册
    spec.loader.exec_module(module)
    return module


def tree_digest(root: Path) -> dict[str, str]:
    """夹具树快照：相对路径 → sha256（含目录外的 meta，验证 --dry-run 零副作用）。"""
    digest: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            try:
                digest[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                digest[rel] = "<unreadable>"
    return digest


class ReleaseCheckTest(unittest.TestCase):
    """夹具：源树 + 用户已装副本 + 非 git 项目根（.crew 回退），doctor/init 为可编程桩。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.source = self.tmp / "skill-source"
        self.installed = self.tmp / "installed-skill"
        self.root = self.tmp / "proj"
        self.make_source()
        shutil.copytree(self.source, self.installed)
        self.make_project()

    # ---------- 夹具 ----------

    def write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def make_source(self) -> None:
        self.write(self.source / "SKILL.md", (
            "---\nname: agent-crew\nversion: 2.2.16\n---\n\n"
            "# agent-crew：多 Agent 编排体系（v2.2.16）\n"))
        self.write(self.source / "CHANGELOG.md",
                   "# Changelog\n\n## 2.2.16 — 2026-11-12\n\n- stub\n")
        self.write(self.source / "scripts/init.py", INIT_STUB)
        self.write(self.source / "scripts/doctor.py", DOCTOR_STUB)

    def make_project(self) -> None:
        self.config = {
            "projectId": "proj",
            "skillRoot": str(self.installed),
            "skillVersion": "2.2.16",
            "helm": {"agentName": "crew-orchestrator-helm"},
        }
        self.write(self.root / ".planning/orchestration.json",
                   json.dumps(self.config, ensure_ascii=False, indent=2) + "\n")
        self.write(self.root / "docs/crew/manifest.json",
                   json.dumps({"skillVersion": "2.2.16", "entries": []},
                              ensure_ascii=False, indent=2) + "\n")
        # 项目受管脚本与源逐字节一致，保证 install.project_source 初始为 pass
        shutil.copytree(self.source / "scripts", self.root / "scripts")
        self.write(self.root / ".crew/guard.lock", f"{os.getpid()}\n")

    def dead_pid(self) -> int:
        try:
            pid_max = int(Path("/proc/sys/kernel/pid_max").read_text().strip())
        except (OSError, ValueError):
            pid_max = 4_194_304
        return pid_max + 1

    @staticmethod
    def git(args: list[str], cwd: Path) -> None:
        subprocess.run(
            ["git", "-C", str(cwd), *args], check=True,
            capture_output=True, text=True,
            env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                 "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"})

    def init_git_repo(self, path: Path) -> None:
        self.git(["init", "-q"], path)
        self.git(["add", "-A"], path)
        self.git(["commit", "-qm", "init"], path)

    def write_registry(self, entries: dict[str, str]) -> None:
        payload = {
            name: {"role": "helm", "agent": name, "startedAt": started_at}
            for name, started_at in entries.items()
        }
        self.write(self.root / ".crew/dispatch-agents.json",
                   json.dumps(payload, ensure_ascii=False))

    def spawn_fake_guard(self, helm: str) -> subprocess.Popen:
        """起一个 argv 含 `crew-guard.py --helm <helm>` 的常驻进程（/proc 可解析）。"""
        proc = subprocess.Popen(
            [PYTHON, "-c", "import time; time.sleep(120)",
             "crew-guard.py", "--helm", helm],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def cleanup() -> None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

        self.addCleanup(cleanup)
        return proc

    def run_cli(self, *args: str, env: dict | None = None,
                cwd: Path | None = None) -> subprocess.CompletedProcess:
        merged = {key: value for key, value in os.environ.items()
                  if key not in CLEAN_ENV}
        if env:
            merged.update(env)
        return subprocess.run(
            [PYTHON, str(RELEASE), *args],
            capture_output=True, text=True, cwd=str(cwd or self.tmp), env=merged)

    def run_check(self, *args: str, env: dict | None = None):
        return self.run_cli("--root", str(self.root), "--source", str(self.source),
                            "--dry-run", *args, env=env)

    @staticmethod
    def rows(proc: subprocess.CompletedProcess) -> dict[str, tuple[str, str]]:
        return {m.group(1): (m.group(2), m.group(3))
                for m in ROW_RE.finditer(proc.stdout)}

    @staticmethod
    def payload(proc: subprocess.CompletedProcess) -> dict:
        return json.loads(proc.stdout)

    # ---------- 全 pass ----------

    def test_all_pass_table_lists_twelve_checks(self):
        proc = self.run_check()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        rows = self.rows(proc)
        expected = {
            "version.source", "version.project", "version.installed",
            "source.git-clean", "install.source_user", "install.project_source",
            "upgrade", "init", "doctor", "guard.lock", "guard.helm", "guard.doctor",
        }
        self.assertEqual(expected, set(rows))
        # 夹具源/项目均非 git、guard.lock 为测试进程（非 crew-guard）→ 两项新检查 skip
        for check_id in ("source.git-clean", "guard.helm"):
            self.assertEqual("SKIP", rows[check_id][0], f"{check_id}: {rows[check_id][1]}")
        for check_id, (status, line) in rows.items():
            if check_id in ("source.git-clean", "guard.helm"):
                continue
            self.assertEqual("PASS", status, f"{check_id}: {line}")
            self.assertIn("next=", line, f"{check_id} 缺下一步提示")
        self.assertIn("pass=10", proc.stdout)
        self.assertIn("fail=0", proc.stdout)

    def test_cache_dirs_excluded_from_install_diff(self):
        self.write(self.installed / "scripts/__pycache__/init.cpython-312.pyc", "x")
        self.write(self.installed / ".pytest_cache/v/cache/lastfailed", "{}")
        proc = self.run_check()
        rows = self.rows(proc)
        self.assertEqual("PASS", rows["install.source_user"][0], proc.stdout)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

    # ---------- fail：版本 ----------

    def test_project_version_mismatch_fails(self):
        self.config["skillVersion"] = "2.2.15"
        self.write(self.root / ".planning/orchestration.json",
                   json.dumps(self.config, ensure_ascii=False))
        proc = self.run_check()
        self.assertEqual(1, proc.returncode)
        status, line = self.rows(proc)["version.project"]
        self.assertEqual("FAIL", status)
        self.assertIn("2.2.15", line)
        self.assertIn("2.2.16", line)

    def test_source_changelog_mismatch_fails(self):
        self.write(self.source / "CHANGELOG.md",
                   "# Changelog\n\n## 2.2.15 — 2026-11-01\n\n- old\n")
        proc = self.run_check()
        status, line = self.rows(proc)["version.source"]
        self.assertEqual("FAIL", status)
        self.assertIn("CHANGELOG", line)

    def test_source_init_fallback_mismatch_fails(self):
        self.write(self.source / "scripts/init.py",
                   INIT_STUB.replace('"2.2.16"', '"2.2.15"'))
        proc = self.run_check()
        status, line = self.rows(proc)["version.source"]
        self.assertEqual("FAIL", status)
        self.assertIn("fallback", line)

    def test_installed_version_mismatch_fails(self):
        self.write(self.installed / "SKILL.md", (
            "---\nname: agent-crew\nversion: 2.2.15\n---\n\n"
            "# agent-crew：多 Agent 编排体系（v2.2.15）\n"))
        proc = self.run_check()
        rows = self.rows(proc)
        self.assertEqual("FAIL", rows["version.installed"][0])
        self.assertEqual("FAIL", rows["install.source_user"][0])
        self.assertEqual(1, proc.returncode)

    # ---------- fail：安装 diff / upgrade ----------

    def test_user_install_extra_file_fails(self):
        self.write(self.installed / "references/extra.md", "leftover\n")
        proc = self.run_check()
        status, line = self.rows(proc)["install.source_user"]
        self.assertEqual("FAIL", status)
        self.assertIn("extra=1", line)

    def test_project_script_drift_fails_both_rows(self):
        with (self.root / "scripts/doctor.py").open("a", encoding="utf-8") as handle:
            handle.write("# local drift\n")
        proc = self.run_check()
        rows = self.rows(proc)
        self.assertEqual("FAIL", rows["install.project_source"][0])
        self.assertNotIn("next=-", rows["install.project_source"][1])
        self.assertEqual("FAIL", rows["upgrade"][0])
        self.assertIn("待升级", rows["upgrade"][1])

    def test_upgrade_manifest_version_mismatch_fails(self):
        self.write(self.root / "docs/crew/manifest.json",
                   json.dumps({"skillVersion": "2.2.15", "entries": []}))
        proc = self.run_check()
        rows = self.rows(proc)
        self.assertEqual("FAIL", rows["upgrade"][0])
        self.assertEqual("PASS", rows["install.project_source"][0], proc.stdout)

    # ---------- fail：guard ----------

    def test_guard_dead_pid_fails(self):
        self.write(self.root / ".crew/guard.lock", f"{self.dead_pid()}\n")
        proc = self.run_check()
        status, line = self.rows(proc)["guard.lock"]
        self.assertEqual("FAIL", status)
        self.assertIn("不活", line)
        self.assertEqual(1, proc.returncode)

    def test_guard_missing_lock_fails(self):
        (self.root / ".crew/guard.lock").unlink()
        proc = self.run_check()
        status, line = self.rows(proc)["guard.lock"]
        self.assertEqual("FAIL", status)
        self.assertIn("缺失", line)

    def test_guard_doctor_warn_is_release_fail(self):
        proc = self.run_check(env={"STUB_GUARD_STATUS": "warn"})
        status, line = self.rows(proc)["guard.doctor"]
        self.assertEqual("FAIL", status)
        self.assertIn("warn", line)

    # ---------- guard.helm：运行中 guard 舵名 vs 现役舵（X-3） ----------

    def test_guard_helm_matches_active_helm_passes(self):
        self.write_registry({
            "crew-orchestrator-helm-6": "2026-11-11T08:20:59Z",
            "crew-orchestrator-helm-7-2": "2026-11-12T02:22:59Z",
        })
        guard = self.spawn_fake_guard("crew-orchestrator-helm-7-2")
        self.write(self.root / ".crew/guard.lock", f"{guard.pid}\n")
        proc = self.run_check()
        status, line = self.rows(proc)["guard.helm"]
        self.assertEqual("PASS", status, proc.stdout + proc.stderr)
        self.assertIn("crew-orchestrator-helm-7-2", line)
        self.assertIn("startedAt=2026-11-12T02:22:59Z", line)
        self.assertEqual(0, proc.returncode, proc.stdout)

    def test_guard_helm_mismatch_fails_and_names_both(self):
        self.write_registry({"crew-orchestrator-helm-7-2": "2026-11-12T02:22:59Z"})
        guard = self.spawn_fake_guard("crew-orchestrator-helm-7")  # 旧舵名
        self.write(self.root / ".crew/guard.lock", f"{guard.pid}\n")
        proc = self.run_check()
        status, line = self.rows(proc)["guard.helm"]
        self.assertEqual("FAIL", status, proc.stdout + proc.stderr)
        self.assertIn("guard --helm=crew-orchestrator-helm-7", line)
        self.assertIn("现役舵 crew-orchestrator-helm-7-2", line)
        next_part = line.split("| next=", 1)[1]
        self.assertIn("crew-orchestrator-helm-7-2", next_part)
        self.assertNotIn("--helm crew-orchestrator-helm --interval", next_part)
        self.assertEqual(1, proc.returncode)

    def test_guard_helm_registry_latest_generation_wins(self):
        self.write_registry({
            "crew-orchestrator-helm-7-2": "2026-11-12T02:22:59Z",
            "crew-orchestrator-helm-7": "2026-11-11T10:44:29Z",
        })
        guard = self.spawn_fake_guard("crew-orchestrator-helm-7")
        self.write(self.root / ".crew/guard.lock", f"{guard.pid}\n")
        status, line = self.rows(self.run_check())["guard.helm"]
        self.assertEqual("FAIL", status, line)

    def test_guard_helm_falls_back_to_config_name(self):
        guard = self.spawn_fake_guard("crew-orchestrator-helm")
        self.write(self.root / ".crew/guard.lock", f"{guard.pid}\n")
        status, line = self.rows(self.run_check())["guard.helm"]
        self.assertEqual("PASS", status, line)
        self.assertIn("配置 helm.agentName", line)

    def test_guard_helm_unresolvable_process_skips(self):
        self.write(self.root / ".crew/guard.lock", f"{os.getpid()}\n")
        status, line = self.rows(self.run_check())["guard.helm"]
        self.assertEqual("SKIP", status, line)
        self.assertIn("不可解析", line)

    # ---------- source.git-clean：源与 my-skills 未提交（S-5） ----------

    def make_my_skills(self, name: str = "my-skills") -> Path:
        path = self.tmp / name
        path.mkdir()
        self.write(path / "README.md", "my-skills\n")
        self.init_git_repo(path)
        return path

    def test_source_git_clean_passes_when_both_clean(self):
        self.init_git_repo(self.source)
        my_skills = self.make_my_skills()
        proc = self.run_check(env={"CREW_MY_SKILLS_ROOT": str(my_skills)})
        status, line = self.rows(proc)["source.git-clean"]
        self.assertEqual("PASS", status, line)
        self.assertIn("均干净", line)

    def test_source_git_clean_fails_on_dirty_source(self):
        self.init_git_repo(self.source)
        self.write(self.source / "scripts/new-uncommitted.py", "print('x')\n")
        my_skills = self.make_my_skills()
        proc = self.run_check(env={"CREW_MY_SKILLS_ROOT": str(my_skills)})
        status, line = self.rows(proc)["source.git-clean"]
        self.assertEqual("FAIL", status, line)
        self.assertIn("技能源未提交", line)
        self.assertEqual(1, proc.returncode)

    def test_source_git_clean_fails_on_dirty_my_skills(self):
        self.init_git_repo(self.source)
        my_skills = self.make_my_skills()
        self.write(my_skills / "scripts/update-all.sh", "dirty\n")
        status, line = self.rows(
            self.run_check(env={"CREW_MY_SKILLS_ROOT": str(my_skills)})
        )["source.git-clean"]
        self.assertEqual("FAIL", status, line)
        self.assertIn("my-skills 未提交", line)

    def test_source_git_clean_skips_when_my_skills_unlocatable(self):
        self.init_git_repo(self.source)
        status, line = self.rows(self.run_check())["source.git-clean"]
        self.assertEqual("SKIP", status, line)
        self.assertIn("无法定位 my-skills", line)
        self.assertIn("技能源工作区干净", line)

    # ---------- fail：init / doctor ----------

    def test_init_check_rc1_fails(self):
        proc = self.run_check(env={"STUB_INIT_RC": "1"})
        status, line = self.rows(proc)["init"]
        self.assertEqual("FAIL", status)
        self.assertIn("rc=1", line)

    def test_init_not_installed_rc2_skips(self):
        proc = self.run_check(env={"STUB_INIT_RC": "2"})
        status, line = self.rows(proc)["init"]
        self.assertEqual("SKIP", status)
        self.assertIn("rc=2", line)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

    def test_doctor_fail_count_reported(self):
        proc = self.run_check(env={"STUB_DOCTOR_FAIL": "2"})
        status, line = self.rows(proc)["doctor"]
        self.assertEqual("FAIL", status)
        self.assertIn("fail=2", line)

    # ---------- skip：缺失组件不崩 ----------

    def test_missing_installed_dir_skips(self):
        self.config["skillRoot"] = str(self.tmp / "no-such-skill")
        self.write(self.root / ".planning/orchestration.json",
                   json.dumps(self.config, ensure_ascii=False))
        proc = self.run_check()
        rows = self.rows(proc)
        self.assertEqual("SKIP", rows["version.installed"][0])
        self.assertEqual("SKIP", rows["install.source_user"][0])
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

    def test_missing_manifest_skips_upgrade_rows(self):
        (self.root / "docs/crew/manifest.json").unlink()
        proc = self.run_check()
        rows = self.rows(proc)
        self.assertEqual("SKIP", rows["upgrade"][0])
        self.assertEqual("SKIP", rows["install.project_source"][0])

    def test_missing_doctor_and_init_scripts_skip(self):
        # 项目缺受管脚本本身是发布未就绪（install diff fail），但对应检查项必须
        # skip 而不崩；缺组件不影响其它行的可判定性。
        (self.root / "scripts/init.py").unlink()
        (self.root / "scripts/doctor.py").unlink()
        proc = self.run_check()
        rows = self.rows(proc)
        self.assertEqual("SKIP", rows["init"][0])
        self.assertEqual("SKIP", rows["doctor"][0])
        self.assertEqual("SKIP", rows["guard.doctor"][0])
        self.assertEqual("PASS", rows["guard.lock"][0])
        self.assertEqual("FAIL", rows["install.project_source"][0])
        self.assertEqual(1, proc.returncode, proc.stdout + proc.stderr)

    # ---------- 输出契约与只读 ----------

    def test_json_output_structure(self):
        proc = self.run_check("--json", env={"STUB_DOCTOR_FAIL": "1"})
        self.assertEqual(1, proc.returncode)
        data = self.payload(proc)
        self.assertTrue(data["dryRun"])
        self.assertEqual(str(self.root), data["root"])
        self.assertEqual(12, len(data["checks"]))
        self.assertEqual(1, data["summary"]["fail"])
        self.assertEqual(1, data["exitCode"])
        ids = {item["id"] for item in data["checks"]}
        self.assertIn("guard.doctor", ids)
        for item in data["checks"]:
            self.assertIn(item["status"], {"pass", "fail", "skip"})
            self.assertIn("value", item)
            self.assertIn("next", item)

    def test_dry_run_has_no_side_effects(self):
        before = tree_digest(self.tmp)
        proc = self.run_check()
        after = tree_digest(self.tmp)
        self.assertEqual(before, after, "--dry-run 不得写仓")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

    def test_missing_root_is_environment_error(self):
        proc = self.run_cli("--root", str(self.tmp / "nope"), "--dry-run")
        self.assertEqual(3, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("不存在", proc.stderr)

    def test_unknown_argument_is_usage_error(self):
        proc = self.run_cli("--bogus")
        self.assertEqual(2, proc.returncode)


class ManifestRegistrationTest(unittest.TestCase):
    """AMEND：release-check.py 的 SCRIPT_MANIFEST 条目随文件同切片加入。"""

    @classmethod
    def setUpClass(cls):
        cls.init = load_module("init_t43", SCRIPTS / "init.py")

    def test_release_check_file_and_manifest_entry(self):
        self.assertTrue(RELEASE.is_file(), "release-check.py 未落地")
        self.assertIn("release-check.py", self.init.SCRIPT_MANIFEST)


if __name__ == "__main__":
    unittest.main()
