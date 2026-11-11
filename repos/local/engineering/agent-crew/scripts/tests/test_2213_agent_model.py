#!/usr/bin/env python3
"""2.2.13 I 切片：角色→模型配置（init 默认/深合并、dispatch --model 透传与记录、doctor agents.config）。

行为断言：init 产物读盘、dispatch argv/注册表/日志/JSON、doctor --only 的 status/detail/next。
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TESTS_DIR = Path(__file__).resolve().parent
_SCRIPTS = _TESTS_DIR.parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclass 注解解析需要模块已注册
    spec.loader.exec_module(module)
    return module


init_mod = _load("init_agent_model", _SCRIPTS / "init.py")
dispatch_mod = _load("dispatch_agent_model", _SCRIPTS / "dispatch.py")

from doctor_fixtures import ProjectFixture  # noqa: E402

EXPECTED_ROLES = ["builder", "planner", "reviewer", "adversary", "scout", "runner"]


def valid_agents() -> dict:
    return {
        "default": {"backend": "qoder", "model": ""},
        "roles": {role: {"backend": "qoder", "model": ""} for role in EXPECTED_ROLES},
        "playbooks": {},
    }


class InitAgentsConfigTest(unittest.TestCase):
    """init：默认结构生成 + 存量配置深合并回落。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "proj"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True,
                       capture_output=True)

    def _write_config(self, data: dict) -> None:
        path = self.root / ".planning/orchestration.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_default_config_structure(self):
        agents = init_mod.DEFAULT_CONFIG["agents"]
        self.assertEqual({"backend": "qoder", "model": ""}, agents["default"])
        self.assertEqual(EXPECTED_ROLES, list(agents["roles"]))
        for role in EXPECTED_ROLES:
            self.assertEqual({"backend": "qoder", "model": ""}, agents["roles"][role])
        self.assertEqual({}, agents["playbooks"])

    def test_init_generates_agents_in_config(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = init_mod.run(["--root", str(self.root), "--yes"])
        self.assertEqual(0, code, out.getvalue())
        config = json.loads(
            (self.root / ".planning/orchestration.json").read_text(encoding="utf-8"))
        self.assertEqual("qoder", config["agents"]["default"]["backend"])
        self.assertEqual(set(EXPECTED_ROLES), set(config["agents"]["roles"]))
        self.assertEqual({}, config["agents"]["playbooks"])

    def test_existing_config_without_agents_falls_back_to_default(self):
        self._write_config({"projectId": "proj"})
        cfg, existing = init_mod.load_config(self.root)
        self.assertEqual(init_mod.DEFAULT_CONFIG["agents"], cfg["agents"])
        self.assertNotIn("agents", existing)

    def test_partial_agents_deep_merge_keeps_default_roles(self):
        self._write_config({
            "projectId": "proj",
            "agents": {"roles": {"builder": {"backend": "claude", "model": "opus"}}},
        })
        cfg, _existing = init_mod.load_config(self.root)
        self.assertEqual({"backend": "claude", "model": "opus"},
                         cfg["agents"]["roles"]["builder"])
        self.assertEqual({"backend": "qoder", "model": ""},
                         cfg["agents"]["roles"]["reviewer"])
        self.assertEqual({"backend": "qoder", "model": ""}, cfg["agents"]["default"])
        self.assertEqual({}, cfg["agents"]["playbooks"])


def fake_outcome(root: Path, runtime: str = "qoder") -> "dispatch_mod.ComposeOutcome":
    return dispatch_mod.ComposeOutcome(
        report={"composedSha256": "sha", "baseSource": "templates",
                "baseSha256": "bsha", "overlayPath": "overlay.md",
                "overlaySha256": "osha"},
        root=root,
        rendered_base_body="BASE\n",
        rendered_overlay_body="OVERLAY\n",
        composed_body="# composed\n",
        role_id="builder",
        runtime=runtime,
        skill_root=root / "skill",
        base_path=root / "skill/templates/agents/qoder/builder.md",
        capabilities=["read"],
        gates=[],
    )


class DeliverModelTest(unittest.TestCase):
    """dispatch deliver：--model 透传 -m；空/None 不传。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)

    def _root(self, runtime: str) -> Path:
        root = self.base / "repo"
        rel = (Path(".qoder/agents/crew") / "builder.md" if runtime == "qoder"
               else Path(".opencode/agent") / "crew-builder.md")
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# composed\n", encoding="utf-8")
        return root

    def _deliver(self, runtime: str, model: str | None) -> list[str]:
        root = self._root(runtime)
        calls: list[list[str]] = []
        with mock.patch.object(dispatch_mod, "run_herdr",
                               side_effect=lambda argv: calls.append(argv) or ""):
            if runtime == "qoder":
                dispatch_mod.deliver_qoder(
                    fake_outcome(root), "proj-builder", "wX:p1",
                    self.base / "run", None, model)
            else:
                dispatch_mod.deliver_opencode(
                    fake_outcome(root, "opencode"), "proj-builder", "wX:p1",
                    self.base / "run", model)
        return calls[0]

    def test_qoder_passes_model_after_double_dash(self):
        argv = self._deliver("qoder", "gpt-x")
        self.assertIn("-m", argv)
        self.assertEqual("gpt-x", argv[argv.index("-m") + 1])
        self.assertGreater(argv.index("-m"), argv.index("--"))

    def test_opencode_passes_model_after_double_dash(self):
        argv = self._deliver("opencode", "gpt-x")
        self.assertIn("-m", argv)
        self.assertEqual("gpt-x", argv[argv.index("-m") + 1])
        self.assertGreater(argv.index("-m"), argv.index("--"))

    def test_empty_or_none_model_omits_flag(self):
        for runtime in ("qoder", "opencode"):
            for model in ("", None):
                with self.subTest(runtime=runtime, model=model):
                    argv = self._deliver(runtime, model)
                    self.assertNotIn("-m", argv)

    def test_start_parser_accepts_model(self):
        args = dispatch_mod.build_parser().parse_args(
            ["start", "--role", "builder", "--runtime", "qoder",
             "--pane", "wX:p1", "--model", "gpt-x"])
        self.assertEqual("gpt-x", args.model)


class StartModelRecordTest(unittest.TestCase):
    """cmd_start：model 归一化后传入 deliver，并落 registry/agent-log/--json。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "repo"
        self.root.mkdir()

    def test_start_records_model_everywhere(self):
        outcome = fake_outcome(self.root)
        captured: dict = {}
        registry_saved: dict = {}
        rows: list[dict] = []

        def fake_deliver(outcome_, name, pane, run_dir, permission_mode, model):
            captured.update({"model": model, "name": name})
            return {"delivery": "composed-agent", "composePath": "cp",
                    "promptPath": "pp", "composedPath": "cp2", "agentFile": "af"}

        args = argparse.Namespace(
            role="builder", runtime="qoder", pane="wX:p1", name="proj-builder",
            instance=None, permission_mode=None, model=" gpt-x ", json=True)
        patches = {
            "load_config": mock.Mock(return_value={"skillVersion": "2.1.0"}),
            "config_preflight_errors": mock.Mock(return_value=[]),
            "build_compose": mock.Mock(return_value=outcome),
            "ensure_role_enabled": mock.Mock(return_value=[]),
            "herdr_agent_names": mock.Mock(return_value=[]),
            "deliver_qoder": mock.Mock(side_effect=fake_deliver),
            "crew_dir": mock.Mock(return_value=Path(self._tmp.name) / "crew"),
            "load_registry": mock.Mock(return_value={}),
            "save_registry": mock.Mock(
                side_effect=lambda root_, registry: registry_saved.update(registry)),
            "append_log": mock.Mock(side_effect=lambda root_, row: rows.append(row)),
        }
        out = io.StringIO()
        with mock.patch.multiple(dispatch_mod, **patches):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = dispatch_mod.cmd_start(args, self.root)
        self.assertEqual(0, code)
        self.assertEqual("gpt-x", captured["model"])
        self.assertEqual("gpt-x", registry_saved["proj-builder"]["model"])
        self.assertEqual("gpt-x", rows[-1]["model"])
        self.assertEqual("gpt-x", json.loads(out.getvalue())["model"])


class DoctorAgentsConfigTest(unittest.TestCase):
    """doctor agents.config：结构非法 fail；命令缺失 warn；无 agents skip。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = ProjectFixture(Path(self._tmp.name))

    def set_agents(self, agents: dict) -> None:
        self.fx.config["agents"] = agents
        self.fx.write_config()

    def check(self) -> tuple[int, dict]:
        rc, report = self.fx.doctor_json("--only", "agents.config")
        return rc, self.fx.item(report, "agents.config")

    def test_valid_agents_config_passes(self):
        self.set_agents(valid_agents())
        rc, item = self.check()
        self.assertEqual(0, rc)
        self.assertEqual("pass", item["status"], item["detail"])

    def test_config_schema_accepts_agents_top_key(self):
        # 新顶层键不得被 config.schema 当未知键拒绝
        self.set_agents(valid_agents())
        rc, report = self.fx.doctor_json("--only", "config.schema")
        item = self.fx.item(report, "config.schema")
        self.assertEqual(0, rc)
        self.assertEqual("pass", item["status"], item["detail"])

    def test_missing_agents_skips(self):
        rc, item = self.check()
        self.assertEqual(0, rc)
        self.assertEqual("skip", item["status"], item["detail"])

    def test_invalid_backend_fails_with_next(self):
        agents = valid_agents()
        agents["roles"]["builder"]["backend"] = "gemini"
        self.set_agents(agents)
        rc, item = self.check()
        self.assertEqual(1, rc)
        self.assertEqual("fail", item["status"])
        self.assertIn("backend", item["detail"])
        self.assertIn("agents", item["next"])

    def test_blank_or_non_string_model_fails(self):
        for model in ("  ", 123):
            with self.subTest(model=model):
                agents = valid_agents()
                agents["default"]["model"] = model
                self.set_agents(agents)
                rc, item = self.check()
                self.assertEqual(1, rc)
                self.assertEqual("fail", item["status"])
                self.assertIn("model", item["detail"])

    def test_playbook_unknown_role_fails(self):
        agents = valid_agents()
        agents["playbooks"] = {"release": {"roles": {"ghost": {"model": ""}}}}
        self.set_agents(agents)
        rc, item = self.check()
        self.assertEqual(1, rc)
        self.assertEqual("fail", item["status"])
        self.assertIn("ghost", item["detail"])

    def test_playbook_backend_invalid_fails(self):
        agents = valid_agents()
        agents["playbooks"] = {"release": {"roles": {
            "builder": {"backend": "gemini", "model": ""}}}}
        self.set_agents(agents)
        rc, item = self.check()
        self.assertEqual(1, rc)
        self.assertEqual("fail", item["status"])
        self.assertIn("playbooks.release.roles.builder.backend", item["detail"])
        self.assertTrue(item["next"])

    def test_playbook_model_invalid_fails(self):
        for model in (123, "  "):
            with self.subTest(model=model):
                agents = valid_agents()
                agents["playbooks"] = {"release": {"roles": {
                    "builder": {"backend": "qoder", "model": model}}}}
                self.set_agents(agents)
                rc, item = self.check()
                self.assertEqual(1, rc)
                self.assertEqual("fail", item["status"])
                self.assertIn("playbooks.release.roles.builder.model", item["detail"])

    def test_playbook_backend_missing_command_warns(self):
        agents = valid_agents()
        agents["playbooks"] = {"interrogate": {"roles": {
            "adversary": {"backend": "claude", "model": "opus"}}}}
        self.set_agents(agents)
        env = dict(os.environ)
        env["PATH"] = str(self.fx.bin)  # claude 未安装
        self.fx.env = lambda: env
        rc, item = self.check()
        self.assertEqual(0, rc)
        self.assertEqual("warn", item["status"], item["detail"])
        self.assertIn("claude", item["detail"])
        self.assertIn("安装", item["next"])

    def test_playbook_level_valid_override_passes(self):
        agents = valid_agents()
        agents["playbooks"] = {"interrogate": {"roles": {
            "adversary": {"backend": "opencode", "model": "some-model"}}}}
        self.set_agents(agents)
        rc, item = self.check()
        self.assertEqual(0, rc)
        self.assertEqual("pass", item["status"], item["detail"])

    def test_non_dict_role_entry_fails_without_internal_error(self):
        # S7：非 dict roles 条目不得抛 AttributeError（检查异常），须给可执行 next
        agents = valid_agents()
        agents["roles"]["builder"] = "qoder"
        self.set_agents(agents)
        rc, item = self.check()
        self.assertEqual(1, rc)
        self.assertEqual("fail", item["status"])
        self.assertIn("agents.roles.builder 必须是对象", item["detail"])
        self.assertTrue(item["next"])
        self.assertNotIn("检查异常", item["detail"])

    def test_non_dict_playbook_role_entry_fails(self):
        agents = valid_agents()
        agents["playbooks"] = {"release": {"roles": {"builder": "claude"}}}
        self.set_agents(agents)
        rc, item = self.check()
        self.assertEqual(1, rc)
        self.assertEqual("fail", item["status"])
        self.assertIn("agents.playbooks.release.roles.builder 必须是对象", item["detail"])
        self.assertTrue(item["next"])

    def test_missing_backend_command_warns(self):
        agents = valid_agents()
        agents["default"]["backend"] = "claude"
        self.set_agents(agents)
        env = dict(os.environ)
        env["PATH"] = str(self.fx.bin)  # 只有 herdr/qodercli/opencode stub，claude 缺失
        self.fx.env = lambda: env
        rc, item = self.check()
        self.assertEqual(0, rc)
        self.assertEqual("warn", item["status"], item["detail"])
        self.assertIn("claude", item["detail"])
        self.assertIn("安装", item["next"])


if __name__ == "__main__":
    unittest.main()
