#!/usr/bin/env python3
"""2.2.13 规则/模板切片防回退断言（A 验证技能交付门、D FB-023 凭据红线、E FB-025 派单根因、C 维护检查）。

自动检查器取舍（FB-023）：凭据外泄主要发生在运行期把凭据/会话文件复制进沙箱、
平台、共享目录或线程工作区，路径多由变量/环境构造，纯静态扫描无法判定目标目录；
本切片不新增 crew-guard/crew-lint 执行器（共享资产，另切片维护），由本文件的字面量
复制扫描（scripts/ + templates/ 提示词）拦截已知危险形态，其余行为由
firstmate/runner 模板自检兜底。扫描器覆盖正反例。
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RULES = "references/crew-rules.md"
HELM = (
    "templates/agents/qoder/helm.md",
    "templates/agents/opencode/helm.md",
)
FIRSTMATE = (
    "templates/agents/qoder/firstmate.md",
    "templates/agents/opencode/firstmate.md",
)
HELM_FIRSTMATE = (*HELM, *FIRSTMATE)
RUNNER = (
    "templates/agents/qoder/runner.md",
    "templates/agents/opencode/runner.md",
)
FIRSTMATE_RUNNER = (*FIRSTMATE, *RUNNER)
CHANGED = (RULES, *HELM_FIRSTMATE, *RUNNER)

# S1/S6：关键句原文快照，防止措辞被改写后关键词仍绿。
D1_SNAPSHOT = (
    "- **D1 工单五段**：每张工单必须写齐目标、现状入口、验收断言、边界规则、回执要求五段；"
    "现状入口必须含「上一版为什么没生效（根因）」——返工/修复/升级类工单必填，"
    "首版/新建写「无（首版）」；缺段或缺根因不得派出（无根因不派活）。"
)
F6_SNAPSHOT = (
    "- **F6 prove-it-works**：结论只认真实运行证据（测试、复现、真实数据回放、可执行命令输出）；"
    "运行态证据分两类：行为/脚本改动必须驱动项目验证技能（`verify-<app>`）对应 feature 的真实驱动证据；"
    "规则/模板/提示词文本改动以 grep/lint/schema 测试为主证据 + 安装后 1 个 smoke feature 为辅证，"
    "并在回执写清 feature 名、命令 + stdout/stderr + 退出码、证据路径。"
    "diff、接口 200、自述完成只作旁证；无验证技能先按 create-verification-skill 生成"
    "（外部工程技能，需环境已安装；未安装则暂停该交付门并 ⏸ 上抛 Captain 记录缺口，"
    "不得跳过或用人肉清单替代），不以人工核对代替（pstack 原名 prove-it-works）。"
)
X4_SNAPSHOT = (
    "- **X4 不碰客户业务数据与凭据**：原始数据不全量读取，只取必要列/行；证据脱敏或哈希；"
    "客户敏感数据不得以原始形态入库。凭据/会话文件只允许存 `.scratch/`"
    "（0600，用 `install -m 600` 或 `chmod 600` 落盘）或平台 env；"
    "禁止复制进沙箱、平台、共享目录或线程工作区；"
    "「平台」指平台/CI 的密钥管理（环境变量或密钥服务）。"
    "「涉凭据」＝复制/移动/持久化/新增凭据或修改权限，必须上抛 Captain，不得代决；"
    "经 Captain 授权的平台 env 读取用于执行不算涉凭据。X4 优先于 F12。"
    "能自动扫的凭据风险做成检查器；无执行器时以角色模板断言 + 测试兜底"
    "（取舍：静态扫描只做技能源回归，不覆盖变量构造的运行时复制路径，行为靠模板自检覆盖）。"
)

CREDENTIAL_BAN_PHRASES = {
    "templates/agents/qoder/firstmate.md": "不复制进沙箱、平台、共享目录、线程工作区",
    "templates/agents/opencode/firstmate.md": "不复制进沙箱、平台、共享目录、线程工作区",
    "templates/agents/qoder/runner.md": "不在沙箱、平台、共享目录、线程工作区留凭据",
    "templates/agents/opencode/runner.md": "不在沙箱、平台、共享目录、线程工作区留凭据",
}

# 字面量凭据文件名扫描（.example/.sample/.template 视为示例文件放行）。
CREDENTIAL_NAMES = (
    ".env",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "auth.json",
    "credentials.json",
    "cookie.jar",
    "cookies.txt",
    "session.json",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    ".git-credentials",
    ".aws/credentials",
    ".docker/config.json",
    ".kube/config",
)
COPY_OP_RE = re.compile(r"\b(?:cp|rsync|copy|copy2|copyfile|copytree)\b")
ALLOWED_SUFFIXES = (".example", ".sample", ".template")
# I：审计口径＝角色/模型的赋值（backend/model 键、具体模型 ID）；runtime 名
# （qoder/opencode/claude）与安装命令目标不在此列。
MODEL_OR_BACKEND_RE = re.compile(
    r"(?i)\b(?:qwen|gpt|sonnet|opus|haiku|gemini|grok|glm|deepseek|kimi|minimax|llama|mistral|o1|o3)\b"
    r"|\b(?:backend|model)\s*[:=]"
    r"|--(?:model|backend)\b"
)


def read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def load_crew_lint():
    path = REPO_ROOT / "scripts" / "crew-lint.py"
    spec = importlib.util.spec_from_file_location("crew_lint_2213", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def find_credential_copy_violations(text: str) -> list[str]:
    """返回「复制 + 字面量凭据文件」行；变量构造的路径不在静态扫描范围。"""
    violations: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not COPY_OP_RE.search(line):
            continue
        for name in CREDENTIAL_NAMES:
            for match in re.finditer(re.escape(name), line):
                if line[match.end():].startswith(ALLOWED_SUFFIXES):
                    continue
                violations.append(f"{lineno}: {line.strip()}")
                break
            else:
                continue
            break
    return violations


class VerificationGateTest(unittest.TestCase):
    """A. F6：运行态证据＝项目验证技能对应 feature 的真实驱动证据。"""

    def test_f6_runtime_evidence_contract(self):
        text = read(RULES)
        for keyword in (
            "项目验证技能",
            "verify-<app>",
            "对应 feature 的真实驱动证据",
            "行为/脚本改动必须驱动项目验证技能",
            "规则/模板/提示词文本改动",
            "grep/lint/schema 测试为主证据",
            "smoke feature 为辅证",
            "feature 名、命令 + stdout/stderr + 退出码、证据路径",
            "diff、接口 200、自述完成只作旁证",
            "create-verification-skill",
        ):
            self.assertIn(keyword, text, f"{RULES} 缺少：{keyword}")

    def test_f6_snapshot(self):
        self.assertIn(F6_SNAPSHOT, read(RULES))

    def test_create_verification_skill_is_external(self):
        text = read(RULES)
        for keyword in (
            "外部工程技能",
            "需环境已安装",
            "未安装",
            "⏸ 上抛 Captain",
            "不得跳过或用人肉清单替代",
        ):
            self.assertIn(keyword, text, f"{RULES} 缺少：{keyword}")

    def test_each_create_verification_reference_has_external_skill_note(self):
        lines = [
            line for line in read(RULES).splitlines() if "create-verification-skill" in line
        ]
        self.assertTrue(lines, f"{RULES} 未找到 create-verification-skill 引用")
        for line in lines:
            self.assertIn("外部工程技能", line)
            self.assertIn("⏸ 上抛", line)

    def test_no_source_chain_residue(self):
        paths = sorted((REPO_ROOT / "references").rglob("*.md"))
        paths += sorted((REPO_ROOT / "templates" / "agents").rglob("*.md"))
        paths.append(REPO_ROOT / "templates" / "playbooks" / "README.md")
        for path in paths:
            rel = path.relative_to(REPO_ROOT).as_posix()
            text = path.read_text(encoding="utf-8")
            with self.subTest(rel=rel):
                self.assertNotIn("真源链", text)
                self.assertNotIn("人肉核对清单", text)

    def test_helm_firstmate_close_distinguishes_text_changes(self):
        for rel in HELM_FIRSTMATE:
            text = read(rel)
            for keyword in (
                "行为/脚本改动必须实测项目验证技能对应 feature",
                "规则/模板/提示词文本改动",
                "grep/lint/schema 测试为主证据",
                "smoke feature 为辅证",
                "feature 名",
                "命令 + stdout/stderr + 退出码",
            ):
                self.assertIn(keyword, text, f"{rel} 缺少：{keyword}")

    def test_helm_firstmate_close_requires_feature_drive(self):
        for rel in HELM_FIRSTMATE:
            text = read(rel)
            for keyword in (
                "实测",
                "项目验证技能",
                "对应 feature",
                "命令 + stdout/stderr + 退出码",
                "旁证",
            ):
                self.assertIn(keyword, text, f"{rel} 缺少：{keyword}")


class RootCauseDispatchTest(unittest.TestCase):
    """E. FB-025：D1/D2 与派活模板必填根因，无根因不派活。"""

    def test_d1_d2_require_root_cause(self):
        text = read(RULES)
        for keyword in (
            "上一版为什么没生效（根因）",
            "返工/修复/升级类工单必填",
            "首版/新建写「无（首版）」",
            "无根因不派活",
            "根因属上下文",
            "不属实现步骤",
        ):
            self.assertIn(keyword, text, f"{RULES} 缺少：{keyword}")

    def test_d1_snapshot_and_closure(self):
        text = read(RULES)
        self.assertIn(D1_SNAPSHOT, text)
        for keyword in ("D1 优先于本表", "根因：成立", "根因：修正为", "闭环"):
            self.assertIn(keyword, text, f"{RULES} 缺少：{keyword}")

    def test_dispatch_templates_require_root_cause(self):
        for rel in HELM_FIRSTMATE:
            text = read(rel)
            with self.subTest(rel=rel):
                self.assertIn("上一版为什么没生效（根因）", text)
                self.assertIn("无根因不派活", text)


class CredentialRedlineTest(unittest.TestCase):
    """D. FB-023：凭据存放边界、复制禁区、上抛与检查器取舍。"""

    def test_x4_credential_clause(self):
        text = read(RULES)
        self.assertIn(X4_SNAPSHOT, text)
        for keyword in (
            "凭据/会话文件",
            ".scratch/",
            "0600",
            "install -m 600",
            "chmod 600",
            "禁止复制进沙箱、平台、共享目录或线程工作区",
            "「平台」指平台/CI 的密钥管理",
            "「涉凭据」＝复制/移动/持久化/新增凭据或修改权限",
            "经 Captain 授权的平台 env 读取用于执行不算涉凭据",
            "X4 优先于 F12",
            "检查器",
        ):
            self.assertIn(keyword, text, f"{RULES} 缺少：{keyword}")

    def test_firstmate_runner_credential_selfcheck(self):
        for rel in FIRSTMATE_RUNNER:
            text = read(rel)
            for keyword in ("凭据/会话文件", ".scratch/", "0600", "上抛", "不动他人凭据"):
                self.assertIn(keyword, text, f"{rel} 缺少：{keyword}")
            for zone in ("沙箱", "平台", "共享目录", "线程工作区"):
                self.assertIn(zone, text, f"{rel} 缺少落点：{zone}")
            self.assertIn(CREDENTIAL_BAN_PHRASES[rel], text, f"{rel} 禁投落点不全")

    def test_credential_copy_checker_flags_positive_examples(self):
        samples = (
            'cp -a ~/.qoder/auth.json "$sandbox/"',
            'shutil.copy(Path.home() / ".env", shared_dir)',
            "rsync -a ~/.netrc host:/shared/crew/",
            'cp "$HOME/.ssh/id_rsa" "$thread_workspace/"',
            'cp "$HOME/.qoder/session.json" "$platform_workspace/"',
            'cp ~/.aws/credentials "$sandbox/"',
            'cp ~/.git-credentials "$thread_workspace/"',
            'cp ~/.docker/config.json "$shared/"',
            'cp ~/.kube/config "$platform/"',
            'cp ~/.ssh/id_ecdsa "$sandbox/"',
        )
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertTrue(find_credential_copy_violations(sample), sample)

    def test_credential_copy_checker_allows_negative_examples(self):
        samples = (
            'cp -a "$PROJECT_ROOT/README.md" "$sandbox/README.md"',
            'shutil.copy(skill / ".env.example", dest)',
            'cp templates/.npmrc.template "$sandbox/"',
            "rsync -a docs/ host:/shared/docs/",
        )
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertEqual(find_credential_copy_violations(sample), [], sample)

    def test_repo_prompts_and_scripts_have_no_literal_credential_copy(self):
        # 取舍：只拦 scripts 与 templates 提示词里的字面量凭据复制；变量构造的运行时路径由模板自检兜底。
        paths: list[Path] = []
        for pattern in ("*.py", "*.sh"):
            paths += sorted((REPO_ROOT / "scripts").glob(pattern))
        paths += sorted((REPO_ROOT / "templates").rglob("*.md"))
        for path in paths:
            rel = path.relative_to(REPO_ROOT).as_posix()
            text = path.read_text(encoding="utf-8", errors="replace")
            with self.subTest(rel=rel):
                self.assertEqual(find_credential_copy_violations(text), [], rel)
        self.assertGreater(len(paths), 0, "未找到可扫描的脚本/提示词")


class MaintenanceLoopTest(unittest.TestCase):
    """C. helm 复盘路由接入验证技能维护检查。"""

    def test_helm_templates_route_verification_maintenance(self):
        for rel in HELM:
            text = read(rel)
            for keyword in (
                "验证技能维护检查",
                "maintain-verification-skill",
                "clean/changed/blocked",
            ):
                self.assertIn(keyword, text, f"{rel} 缺少：{keyword}")


class ScopeTest(unittest.TestCase):
    """技能资产零业务/客户专名（复用 test_2212 口径）。"""

    def test_changed_assets_have_no_case_terms(self):
        for rel in CHANGED:
            text = read(rel)
            with self.subTest(rel=rel):
                self.assertNotIn("CASE", text)
                self.assertNotIn("扫码", text)


class TicketTemplateTest(unittest.TestCase):
    """D1：工单模板提示根因必填，且不超行数预算。"""

    def test_ticket_template_requires_root_cause_within_budget(self):
        rel = "templates/_templates/ticket.md"
        text = read(rel)
        self.assertIn("上一版为什么没生效（根因）", text)
        self.assertIn("首版写「无（首版）」", text)
        budgets = dict(load_crew_lint().BUDGETS)
        self.assertIn(rel, budgets)
        self.assertLessEqual(
            len(text.splitlines()), budgets[rel], f"{rel} 超过预算 {budgets[rel]}"
        )


# H 规则侧逐项快照：在正确的行内断言，删任一项即红。
HANDOVER_WHITELIST_ITEMS = (
    "只读白名单＝`python3 scripts/plan-status.py --brief` 一屏",
    "`python3 scripts/plan-status.py --handover` 摘要",
    "活跃流 plan 的 Next Step/Status（grep）",
    "feedback 待复盘条目（grep）",
    "`<crew-dir>/guard.handoff`",
    "看板仅按关键词 grep",
)
HANDOVER_BUDGET_ITEMS = (
    "交接必读 ≤32KB",
    "含 `--handover` 输出与必读文件",
    "超阈值先摘要/关键词 grep",
    "写明只读段",
    "`Decisions`/`Deviation`/`Backlog` 不整读",
    "项目文档（AGENTS/CONTEXT）只读与本次交接相关章节，不整读",
)


class HandoverBudgetTest(unittest.TestCase):
    """H1/H4：交接只读白名单 + 读取量 ≤32KB 预算。"""

    def test_helm_templates_handover_whitelist_and_budget(self):
        for rel in HELM:
            text = read(rel)
            whitelist = next(
                line for line in text.splitlines() if "交接读取范围（硬）" in line
            )
            self.assertIn("只读白名单", whitelist)
            for item in HANDOVER_WHITELIST_ITEMS:
                self.assertIn(item, whitelist, f"{rel} 白名单缺项：{item}")
            budget = next(
                line for line in text.splitlines() if "读取量预算（硬）" in line
            )
            for item in HANDOVER_BUDGET_ITEMS:
                self.assertIn(item, budget, f"{rel} 读取量预算缺项：{item}")

    def test_rules_f11_has_read_budget(self):
        text = read(RULES)
        line = next(line for line in text.splitlines() if "**F11 " in line)
        for keyword in ("读取量预算", "plan-status.py --brief", "32KB",
                        "Decisions", "Deviation", "Backlog", "不整读"):
            self.assertIn(keyword, line, f"F11 缺少：{keyword}")

    def test_plan_discipline_handover_rule_has_read_budget(self):
        text = read("references/plan-discipline.md")
        line = next(
            line for line in text.splitlines() if line.startswith("4. **交接一屏优先**")
        )
        for keyword in ("白名单", "plan-status.py --brief", "32KB",
                        "Decisions", "Deviation", "Backlog", "不整读"):
            self.assertIn(keyword, line, f"§5.1 第 4 条缺少：{keyword}")


class ModelSelectionTest(unittest.TestCase):
    """I：角色→模型选择口径；剧本正文不硬编码模型/后端。

    审计范围：仅技能层随分发剧本正文（templates/playbooks/*.md，README 文档除外）；
    项目层 .planning/playbooks/ 由项目自管，不在技能源测试范围。
    审计口径：角色/模型的赋值（backend/model 键、具体模型 ID）；安装命令里的
    runtime 目标（如 --agent qoder-cn,claude）不属角色模型，不算命中。
    """

    def test_helm_firstmate_model_selection_contract(self):
        for rel in HELM_FIRSTMATE:
            text = read(rel)
            for keyword in (
                "`.planning/orchestration.json` 顶层 `agents`",
                "剧本级角色 > 全局角色 > default",
                "`model` 为空＝通道默认",
                "不传 `--model`",
                "`python3 scripts/dispatch.py start --model <model>`",
                "剧本文本不得硬编码模型/后端",
                "实际 backend/model 记录到回执/工位",
                "backend=claude",
                "`python3 scripts/crew-claude.py run ...`",
                "不手拼 claude flag",
                "不走 dispatch start",
                "--runtime 仅 qoder/opencode",
            ):
                self.assertIn(keyword, text, f"{rel} 缺少：{keyword}")

    def test_playbooks_readme_model_selection(self):
        text = read("templates/playbooks/README.md")
        for keyword in (
            "配置位置",
            "`.planning/orchestration.json` 顶层 `agents`",
            "{qoder,opencode,claude}",
            "builder/planner/reviewer/adversary/scout/runner",
            "剧本级角色 > 全局角色",
            "`agents.roles.<role>`",
            "`agents.playbooks.<playbook>.roles.<role>`",
            "`agents.default`",
            "`model` 为空＝该通道默认模型",
            "`python3 scripts/dispatch.py start --model <model>`",
            "空 model 不传",
            "切换/切回",
            "1–2 行",
            "缺配置回落",
            "`qoder`",
            "只声明 owner 角色",
            "不写模型/后端",
            "实际 backend/model 记录到回执/工位",
            "审计口径",
            "项目层 `.planning/playbooks/` 由项目自管",
            "安装命令的 runtime 目标",
            "claude 通道",
            "backend=claude",
            "firstmate 派 Runner",
            "crew-claude.py run",
            "不走 `dispatch start`",
            "`dispatch start --runtime` 只支持 qoder/opencode",
        ):
            self.assertIn(keyword, text, f"README 缺少：{keyword}")
        self.assertNotIn("`agents.playbooks.<playbook>.<role>`", text)

    def test_playbooks_have_no_hardcoded_model_or_backend(self):
        # 审计对象：技能层随分发剧本正文；README 文档与项目层剧本不在范围。
        for path in sorted((REPO_ROOT / "templates" / "playbooks").glob("*.md")):
            if path.name == "README.md":
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            text = path.read_text(encoding="utf-8")
            matches = [match.group(0) for match in MODEL_OR_BACKEND_RE.finditer(text)]
            with self.subTest(rel=rel):
                self.assertEqual(matches, [], f"{rel} 命中硬编码模型/后端：{matches}")


class NotifyActiveStateTest(unittest.TestCase):
    """S3：notify 目标活跃态判定口径。"""

    def test_notify_active_state_contract(self):
        text = read(RULES)
        for keyword in (
            "notify 目标活跃态判定",
            "`idle/working`",
            "`strip().lower()`",
            "缺失/空白",
            "done/unknown/blocked",
            "`--agent` 显式指定",
        ):
            self.assertIn(keyword, text, f"{RULES} 缺少：{keyword}")


class LintTest(unittest.TestCase):
    """技能静态校验与纯净度检查必须全绿。"""

    def test_crew_lint_passes(self):
        for args in ((), ("--purity",)):
            result = subprocess.run(
                [sys.executable, str(REPO_ROOT / "scripts" / "crew-lint.py"), *args],
                capture_output=True,
                text=True,
                cwd=REPO_ROOT,
            )
            self.assertEqual(
                result.returncode,
                0,
                f"crew-lint {' '.join(args)} 失败：{result.stdout}{result.stderr}",
            )


class BudgetTest(unittest.TestCase):
    """角色模板行数预算（复用 crew-lint BUDGETS 常量）。"""

    def test_templates_within_budget(self):
        budgets = dict(load_crew_lint().BUDGETS)
        for rel in (*HELM_FIRSTMATE, *RUNNER):
            with self.subTest(rel=rel):
                self.assertIn(rel, budgets)
                lines = len(read(rel).splitlines())
                self.assertLessEqual(lines, budgets[rel], f"{rel} {lines} 行 > {budgets[rel]}")


if __name__ == "__main__":
    unittest.main()
