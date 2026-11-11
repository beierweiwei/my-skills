#!/usr/bin/env python3
"""doctor 测试夹具：临时 skill 源 + 项目仓 + manifest/AGENTS/角色/agent-log。

只用于单测；不触碰真实项目，不写真实项目。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
DOCTOR = SKILL_DIR / "scripts" / "doctor.py"
DISPATCH = SKILL_DIR / "scripts" / "dispatch.py"
INIT = SKILL_DIR / "scripts" / "init.py"
_spec = importlib.util.spec_from_file_location("dispatch_fixture", DISPATCH)
dispatch_mod = importlib.util.module_from_spec(_spec)
sys.modules["dispatch_fixture"] = dispatch_mod
_spec.loader.exec_module(dispatch_mod)
QODER_TEMPLATES = SKILL_DIR / "templates" / "agents" / "qoder"
OPENCODE_TEMPLATES = SKILL_DIR / "templates" / "agents" / "opencode"

MARK_START = "<!-- agent-crew:orchestration:start -->"
MARK_END = "<!-- agent-crew:orchestration:end -->"

CORE_CAPS = {
    "helm": ["read", "dispatch"],
    "firstmate": ["read", "write-docs", "dispatch"],
    "builder": ["read", "write-code", "run-commands"],
    "planner": ["read", "write-docs"],
    "reviewer": ["read", "report"],
    "runner": ["read", "run-commands"],
    "scout": ["read", "report"],
}
CORE_LIFECYCLE = {
    "helm": "persistent", "firstmate": "persistent", "builder": "keep-until",
    "planner": "one-shot", "reviewer": "one-shot", "runner": "one-shot",
    "scout": "one-shot",
}

HERDR_STUB = """#!/usr/bin/env python3
import json
print(json.dumps({"result": {"agents": []}}))
"""

PLAYBOOK_SEEDS = ("autonomous-run.md", "babysit.md", "session-pickup.md", "investigation.md")
PLAYBOOK_BODY = """# 剧本：{name}

## 适用与退出条件

- 适用：doctor fixture 最小剧本。
- 退出：Phase 全部 complete。

## Phases

### Phase 1: {name} 准备
- owner: fixture
- [ ] 首个步骤
- **Status:** pending

## 跳过规则

- 跳过步骤记 `- [x] <步骤>（跳过：<原因>）`。
"""


def write_playbook_library(skill: Path) -> None:
    """最小技能层剧本库（README + 4 种子），供 doctor playbooks.library 用例。"""
    directory = skill / "templates/playbooks"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "README.md").write_text(
        "# 技能层剧本库（fixture）\n\n通用剧本随技能分发，不复制进项目。\n", encoding="utf-8")
    for name in PLAYBOOK_SEEDS:
        (directory / name).write_text(PLAYBOOK_BODY.format(name=name), encoding="utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def text_sha(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if not text.endswith("\n"):
        text += "\n"
    return sha256_bytes(text.encode("utf-8"))


def normalized_block(agents_text: str) -> str:
    lines = agents_text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == MARK_START]
    ends = [i for i, line in enumerate(lines) if line.strip() == MARK_END]
    if len(starts) != 1 or len(ends) != 1:
        raise ValueError("fixture AGENTS 必须恰好一对标记")
    body = [line.rstrip() for line in lines[starts[0] + 1:ends[0]]]
    while body and not body[0]:
        body.pop(0)
    while body and not body[-1]:
        body.pop()
    return "\n".join(body) + "\n"


def block_sha(agents_text: str) -> str:
    return sha256_bytes(normalized_block(agents_text).encode("utf-8"))


def config_hash(config: dict) -> str:
    data = {key: value for key, value in config.items()
            if not key.startswith("_") and key != "skillVersion"}
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_bytes(payload.encode("utf-8"))


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def make_skill(base: Path) -> Path:
    skill = base / "skill"
    for src, dst in (
        (QODER_TEMPLATES, skill / "templates/agents/qoder"),
        (OPENCODE_TEMPLATES, skill / "templates/agents/opencode"),
    ):
        shutil.copytree(src, dst)
    for rel in (
        "templates/agents/qoder/builder.md",
        "templates/agents/qoder/firstmate.md",
        "templates/agents/qoder/reviewer.md",
        "templates/agents/opencode/firstmate.md",
    ):
        path = skill / rel
        path.write_text(path.read_text(encoding="utf-8") + "\nBASE_ONLY_MARKER\n",
                        encoding="utf-8")
    refs = skill / "references"
    refs.mkdir()
    for name in ("crew-rules.md", "crew-acceptance.md", "plan-discipline.md"):
        (refs / name).write_text(f"# {name}\n", encoding="utf-8")
    write_playbook_library(skill)
    (skill / "SKILL.md").write_text(
        "---\nname: agent-crew\nversion: 2.1.0\n---\n# skill\n", encoding="utf-8"
    )
    return skill


def make_linked_worktree(base: Path) -> tuple[Path, Path]:
    """建「主仓 + linked worktree」（真实 git worktree），两处都铺技能源骨架。

    返回 (主仓技能源目录, worktree 技能源目录)。worktree 根的 .git 为文件，
    主仓根的 .git 为目录，用于区分「稳定分发副本」与「开发 worktree」。
    """
    base.mkdir(parents=True, exist_ok=True)
    repo = base / "src-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "README.md").write_text("# src repo\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "seed"], check=True)
    wt = base / "dev-wt"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(wt)], check=True)
    skill_rel = Path("repos/local/engineering/agent-crew")
    for root in (repo, wt):
        skill = root / skill_rel
        (skill / "references").mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: agent-crew\nversion: 2.1.0\n---\n# skill fixture\n",
            encoding="utf-8")
    return repo / skill_rel, wt / skill_rel


class ProjectFixture:
    """一个最小合法项目：skill 源 + 配置 + 角色 + AGENTS 块 + manifest + install-log。"""

    def __init__(self, base: Path, name: str = "proj"):
        self.base = base
        self.root = base / name
        self.root.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.skill = make_skill(base)
        self.config = {
            "projectId": "proj",
            "projectName": "Probe Project",
            "skillRoot": str(self.skill),
            "skillVersion": "2.1.0",
            "portfolioDir": ".planning/portfolio",
            "portfolioMatch": "*portfolio*",
            "board": {"path": "docs/进度追踪.md", "title": "进度追踪", "archive": ""},
            "docNames": {
                "req": "需求.md", "reqPath": "docs/需求.md",
                "accept": "验收.md", "context": "CONTEXT.md",
            },
            "captainDecisions": ["合并/推送/worktree 清理"],
            "redLines": ["原始数据不全量读取，只读必要列/行"],
            "dataDir": "data/raw/",
            "roles": {"enabled": list(CORE_CAPS)},
            "hooks": {
                "paths": {
                    "topology": "docs/orchestration/topology.md",
                    "projectExt": "docs/crew/project-ext",
                },
                "customRoles": [],
                "gatePolicy": {"appendGates": []},
                "projectScripts": [],
            },
        }
        (self.root / ".planning/portfolio").mkdir(parents=True)
        (self.root / ".planning/playbooks").mkdir(parents=True)
        (self.root / ".planning/playbooks/README.md").write_text(
            "# 项目层剧本库（fixture）\n\n项目特有剧本放本目录；同名覆盖技能层。\n",
            encoding="utf-8")
        (self.root / "docs/orchestration").mkdir(parents=True)
        (self.root / "docs/orchestration/topology.md").write_text(
            "# topology\n", encoding="utf-8")
        (self.root / "docs/crew/project-ext").mkdir(parents=True)
        (self.root / "data/raw").mkdir(parents=True)
        self.write_config()
        self.write_role("qoder", "builder")
        self.write_role("qoder", "reviewer")
        self.write_role("opencode", "firstmate")
        self.composed_entries = self.write_composed_agents()
        self.write_agents()
        self.script = self.root / "scripts/dispatch.py"
        self.script.parent.mkdir(parents=True)
        self.script.write_text("#!/usr/bin/env python3\n# managed dispatcher\n",
                               encoding="utf-8")
        self.write_manifest()
        (self.root / "docs/crew/install-log.jsonl").parent.mkdir(parents=True, exist_ok=True)
        (self.root / "docs/crew/install-log.jsonl").write_text(
            json.dumps({"ts": "2026-11-09T00:00:00Z", "action": "init",
                        "result": "ok"}, ensure_ascii=False) + "\n",
            encoding="utf-8")
        self.bin = base / "bin"
        self.bin.mkdir(exist_ok=True)
        self.stub_cli("herdr", HERDR_STUB)
        # 对齐 qodercli 1.1.85 实际输出：总量行 + 分节 + `· Inherit` 后缀
        self.stub_cli("qodercli", "#!/usr/bin/env python3\n"
                                  "print('12 active agents')\nprint()\n"
                                  "print('Project:')\n"
                                  "print('  builder · Inherit')\n"
                                  "print('  reviewer · Inherit')\nprint()\n"
                                  "print('Built-in:')\n"
                                  "print('  Explore · Inherit')\n"
                                  "print('  general-purpose · Inherit')\n")
        self.stub_cli("opencode", "#!/usr/bin/env python3\nprint('firstmate')\n")

    # ---------- 写 fixture ----------

    def write_config(self) -> None:
        write_json(self.root / ".planning/orchestration.json", self.config)

    def role_path(self, runtime: str, role_id: str, custom: bool = False) -> Path:
        if runtime == "qoder":
            rel = Path(".qoder/agents") / ("custom" if custom else "") / f"{role_id}.md"
        else:
            name = f"custom-{role_id}" if custom else role_id
            rel = Path(".opencode/agent") / f"{name}.md"
        return self.root / rel

    def write_role(self, runtime: str, role_id: str, base: str | None = None,
                   custom: bool = False, capabilities: list[str] | None = None,
                   gates: list[str] | None = None, lifecycle: str | None = None,
                   body: str | None = None, frontmatter_extra: str = "",
                   descriptor_extra: dict | None = None) -> Path:
        base = base or role_id
        path = self.role_path(runtime, role_id, custom)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = {
            "id": role_id, "runtime": runtime, "extends": f"skill:{base}",
            "managed": False,
            "capabilities": capabilities or CORE_CAPS[base],
            "gates": gates or [],
            "lifecycle": lifecycle or CORE_LIFECYCLE[base],
            "prompt": path.relative_to(self.root).as_posix(),
        }
        if descriptor_extra:
            descriptor.update(descriptor_extra)
        if runtime == "qoder":
            frontmatter = f"name: {role_id}\ndescription: project {role_id} overlay"
        else:
            frontmatter = f"description: project {role_id} overlay\nmode: primary"
        if frontmatter_extra:
            frontmatter += "\n" + frontmatter_extra
        default_body = f"# {base.capitalize()}\n## 项目补充\nPROJECT_ONLY_MARKER：项目补充。\n"
        path.write_text(
            "---\n" + frontmatter + "\n---\n"
            + "<!-- crew-role\n" + json.dumps(descriptor, ensure_ascii=False) + "\n-->\n"
            + (body if body is not None else default_body),
            encoding="utf-8",
        )
        return path

    def write_composed_agents(self) -> list[dict]:
        entries: list[dict] = []
        for runtime, role in (("qoder", "builder"), ("qoder", "reviewer"),
                              ("opencode", "firstmate")):
            outcome = dispatch_mod.build_compose(self.root, role, runtime)
            if not outcome.ok:
                continue
            desc = dispatch_mod.scalar(
                outcome.base_frontmatter["fields"].get("description", "")) or role
            if runtime == "qoder":
                rel = Path(".qoder/agents/crew") / f"{role}.md"
                descriptor = {
                    "id": f"crew-{role}", "runtime": "qoder", "composed": True,
                    "managed": True, "source": outcome.report.get("baseSource"),
                    "override": outcome.role_path.relative_to(self.root).as_posix(),
                }
                text = ("---\n" f"name: crew-{role}\n"
                        f"description: {json.dumps(desc, ensure_ascii=False)}\n---\n"
                        "<!-- crew-role\n"
                        f"{json.dumps(descriptor, ensure_ascii=False)}\n-->\n"
                        f"{outcome.composed_body}")
            else:
                rel = Path(".opencode/agent") / f"crew-{role}.md"
                text = f"---\n{outcome.base_frontmatter['raw'].strip()}\n---\n{outcome.composed_body}"
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            entries.append({"path": rel.as_posix(), "runtime": runtime, "role": role,
                            "managed": True, "sha256": text_sha(path)})
        return entries

    def write_agents(self, block: str | None = None, outside: str = "# AGENTS\n\n项目自有内容。\n") -> None:
        if block is None:
            block = "\n".join([
                "## 协作编排",
                f"- 规则：`{self.skill}/references/crew-rules.md`（D1–D6）。",
                f"- 验收：`{self.skill}/references/crew-acceptance.md`（A1–A5）。",
                "- 通用规则见技能源；项目决策只写本文件。",
            ])
        (self.root / "AGENTS.md").write_text(
            outside + "\n" + MARK_START + "\n" + block + "\n" + MARK_END + "\n",
            encoding="utf-8",
        )

    def manifest(self) -> dict:
        agents = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        return {
            "manifestVersion": 1,
            "skillVersion": "2.1.0",
            "projectId": "proj",
            "configHash": config_hash(self.config),
            "generatedAt": "2026-11-09T00:00:00Z",
            "entries": [
                {"kind": "file", "path": "scripts/dispatch.py",
                 "sha256": text_sha(self.script), "templateVersion": "2.1.0"},
                {"kind": "block", "path": "AGENTS.md",
                 "marker": "agent-crew:orchestration",
                 "blockSha256": block_sha(agents), "renderVersion": "2.1.0"},
            ],
            "roleStubs": [],
            "composedAgents": list(getattr(self, "composed_entries", [])),
        }

    def write_manifest(self, data: dict | None = None) -> None:
        write_json(self.root / "docs/crew/manifest.json", data or self.manifest())

    # ---------- 运行 ----------

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["PATH"] = str(self.bin) + os.pathsep + env.get("PATH", "")
        return env

    def stub_cli(self, name: str, content: str) -> Path:
        self.bin.mkdir(exist_ok=True)
        path = self.bin / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)
        return path

    def run(self, script: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(script), "--root", str(self.root), *args],
            capture_output=True, text=True, env=self.env(), cwd=str(self.root),
        )

    def doctor(self, *args: str) -> subprocess.CompletedProcess:
        return self.run(DOCTOR, *args)

    def doctor_json(self, *args: str) -> tuple[int, dict]:
        proc = self.doctor(*(args + ("--json",)))
        try:
            report = json.loads(proc.stdout)
        except ValueError:
            raise AssertionError(
                f"doctor stdout 非 JSON：rc={proc.returncode}\n"
                f"stdout={proc.stdout}\nstderr={proc.stderr}"
            ) from None
        return proc.returncode, report

    def item(self, report: dict, item_id: str) -> dict:
        for item in report["items"]:
            if item["id"] == item_id:
                return item
        raise AssertionError(f"缺少检查项 {item_id}：{[i['id'] for i in report['items']]}")

    def compose(self, role: str, runtime: str) -> tuple[int, dict]:
        proc = self.run(DISPATCH, "compose", "--role", role, "--runtime", runtime, "--json")
        return proc.returncode, json.loads(proc.stdout)

    def write_agent_log(self, rows: list[dict]) -> Path:
        path = self.root / ".git/crew/agent-log.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                        encoding="utf-8")
        return path

    def commit(self) -> None:
        subprocess.run(["git", "-C", str(self.root), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-qm", "fixture"], check=True,
        )

    def snapshot(self) -> dict[str, str]:
        return {
            path.relative_to(self.root).as_posix(): sha256_bytes(path.read_bytes())
            for path in sorted(self.root.rglob("*"))
            if path.is_file() and ".git/" not in path.relative_to(self.root).as_posix()
        }


def strip_role_meta(text: str) -> str:
    """去掉 frontmatter 与 crew-role 描述符，返回正文（S9-02 正文 diff 用）。"""
    lines = text.splitlines()
    body_start = 0
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                body_start = index + 1
                break
    body = "\n".join(lines[body_start:])
    import re
    return re.sub(r"<!--\s*crew-role\s*\{.*?\}\s*-->", "", body, flags=re.S).lstrip("\n")
