# 剧本：release（技能版本发布/升级）

> 技能层通用剧本；项目同名剧本放 `.planning/playbooks/release.md` 覆盖本文件。解析契约与跳过留痕写法见 `README.md`。

## 适用与退出条件

- 适用：技能版本发布/升级——版本号与 CHANGELOG、全量测试与静态检查、同步与 push、用户目录刷新、项目 upgrade/init/doctor、升级后重启与可交接检查、安装后验证技能交付门与发布后维护循环。
- 退出：五处版本一致且检查全绿；同步与 push 完成；用户目录与项目安装均为新版本；升级后 guard 重启验证通过、长驻角色处置与可交接性实测留证；收口回执发出；发布后维护循环结论（clean/changed/blocked）已落盘并写入发布回执。

## Phases

### Phase 1: 版本五处一致
- owner: builder
- [ ] 更新版本五处：SKILL.md frontmatter `version` 与标题、`scripts/init.py` fallback 版本、CHANGELOG.md 新版本段、版本断言测试
- [ ] 用版本断言测试与 grep 复核五处一致，不一致不进入下一步
- **Status:** pending

### Phase 2: 全量测试与静态检查
- owner: builder
- [ ] `PYTHONDONTWRITEBYTECODE=1 uv run --with pytest pytest scripts/tests -q`：0 fail，通过数不低于上一版本基线
- [ ] `python3 scripts/crew-lint.py`、`python3 scripts/crew-lint.py --purity`、`python3 scripts/plan-status.py --selftest`、`python3 scripts/plan-status.py --playbooks templates/playbooks` 全绿
- [ ] **S4 grep 口径**：验收/清零 grep 一律 `grep -rnw '<词>'` 并限定生产目录（本仓 `references/`、`templates/`、`scripts/`，排除 `scripts/tests/` 的反向断言与常量），不裸 grep 全仓
- **Status:** pending

### Phase 3: 同步与 push
- owner: firstmate
- [ ] 技能源本地 commit，回执记录 commit hash；不夹带无关改动（F2）
- [ ] 同步到 my-skills 仓库并 commit；push 前先 `git push --dry-run` 确认提交范围
- [ ] dry-run 无异常后 push master（仅 Captain 已授权的仓）；记录 push 前后 `origin/master` hash
- [ ] dry-run 与 push 输出落 `.planning/<task>/evidence/release/myskills-push.txt`
- **Status:** pending

### Phase 4: 用户目录刷新
- owner: firstmate
- [ ] scoped 刷新：在 my-skills 技能库仓根执行 `bash scripts/update-all.sh --skill agent-crew --agent qoder-cn,claude --skip-setup`（可先加 `--dry-run` 预览）
- [ ] 校验用户目录 SKILL.md `version` 与技能源一致（全部已装技能逐项对照）；命令输出落 `.planning/<task>/evidence/release/user-install.txt`
- [ ] **S2 diff 口径**：安装/比对用 `diff -rq -x __pycache__ -x .pytest_cache`（或等价排除），`__pycache__`/`.pytest_cache` 噪声不算差异；先记缓存基线再比对
- **Status:** pending

### Phase 5: 项目升级与体检
- owner: firstmate
- [ ] `python3 scripts/upgrade.py --apply --confirm`：自动同步 config `skillVersion` 与 manifest（不要手工改 config，避免「版本已更新而脚本仍旧」的假象），记录 backup id；输出落 `.planning/<task>/evidence/release/upgrade-apply.txt`
- [ ] `python3 scripts/init.py --check`（输出落 `evidence/release/init-check.txt`）与 `python3 scripts/doctor.py --root . --profile release --strict --json`（输出落 `evidence/release/doctor-after.json`）：按当前基线核对通过数/失败数；`--strict` 下 warn 也会使 rc=1（既有语义），判定以 fail=0 为准，warn 计数记录并在证据注明触发原因（含 H2/H3 新增 warn），不得因 warn 停发；仅有未解释的 fail 才停，按输出 `next` 处置
- [ ] 项目仓本地 commit（只提交发布相关改动）
- **Status:** pending

### Phase 6: 升级后重启与可交接检查
- owner: firstmate
- [ ] ① 先无锁取证、再启动 guard：记录旧进程 pid/cmdline 并停止 → 在无 guard 持锁时跑 `python3 scripts/crew-guard.py --once --json`（stdout 必须为非空 JSON，落 `evidence/release/guard-once.json`）→ 启动长驻 `python3 scripts/crew-guard.py --helm <当前舵> --interval 120` → 验证 `<crew-dir>/guard.lock` / pid / cmdline 与新代码生效探针（wake 写 `generated_at:` 或 `--help` 新子命令）；长驻实例持锁时第二实例静默 rc=0、不产生输出，不得作为证据；任一失败即停，不得报发布完成
- [ ] ② 长驻角色处置：compose 在启动时快照，升级不热更新已运行会话——helm 换舵（新 tab 新舵、首报后关旧）、firstmate 重建/重派、one-shot worker 下次启动自动取新模板；处置写入发布回执
- [ ] ③ 可交接性实测：`herdr pane read <helm pane> --lines 80` 可见 `ctx Nk/1M`、① 无锁窗口采集的 `--once --json` 非空 JSON、`python3 scripts/dispatch.py compose --role helm --json` rc=0
- [ ] **验证技能交付门（安装后，由 builder/runner 执行）**：项目升级/安装完成后必须重跑，并以新证据为准——行为/脚本改动真实驱动项目验证技能（`<skills>/verify-<app>/` 占位，项目层安装、不随技能分发）对应 feature；规则/模板/提示词文本改动以 grep/lint/schema 测试为主证据 + 安装后 1 个 smoke feature 为辅证。回执写明 feature 名、命令、退出码、证据路径，对应关系不可省；证据落 `.planning/<task>/evidence/release/verify-skill.txt`，清理夹具后 `test -f` 复查证据仍在；项目无验证技能时先按 create-verification-skill 生成并自证跑通一个 feature（外部工程技能，需环境已安装；未安装则暂停该交付门并 ⏸ 上抛 Captain 记录缺口，不得跳过或用人肉清单替代）
- [ ] ④ 证据落 `.planning/<task>/evidence/release/`（`myskills-push.txt`、`user-install.txt`、`upgrade-apply.txt`、`init-check.txt`、`doctor-after.json`、`guard-once.json`、`guard-restart.txt`、`handover-readiness.txt`、`verify-skill.txt`、`maintain-verification.txt`（Phase 7 维护 outcome，产出后补入））；任一未完成或无证据不得报发布收口
- [ ] **S1 外部只读快照**：对外部仓/环境采集只读状态快照时带 `date -Iseconds` 与 `git rev-parse HEAD`（同一证据写明采集时刻与 HEAD）；对比出现漂移先按「外部并发提交」归因（查 reflog/mtime/提交内容），全程只读、不写外部仓
- **Status:** pending

### Phase 7: 发布后维护循环（验证技能）
- owner: firstmate
- [ ] 触发：发布执行完成（最终收口回执前）当轮启动，最迟下一个工作会话；每周复盘兜底；outcome 写入 Phase 8 回执
- [ ] 范围：完整执行 `maintain-verification.md` Phase 1–6；发布后下限 source wave + live pass，不替代该剧本的不可跳过项；运行记录 covered / unreachable / drift / outcome 落 `.scratch/` 不提交，outcome 证据落 `.planning/<task>/evidence/release/maintain-verification.txt`
- [ ] 检查点：Outcome 三选一（clean/changed/blocked）必须明说；changed 交付＝一次本地 commit（不自行 push）；产品回归只报不修（D6；编辑/驱动由 builder/runner 按剧本 owner 执行，firstmate 只编排与收口）
- [ ] 结论落点：outcome 与证据路径写入 Phase 8 收口回执与复盘记录；流程/提示词问题记 `feedback.md`（R5），产品缺陷转产品流；blocked 写明卡点与已覆盖范围
- **Status:** pending

### Phase 8: 收口回执
- owner: firstmate
- [ ] 按 R2 写回执：版本/commit/push/用户目录/init/doctor/guard 新 pid/长驻角色处置/工位处置
- [ ] 更新 plan 的 Status 与 Next Step，刷新看板（`python3 scripts/plan-status.py --write`）；交接前 `--check-fresh`
- [ ] 回执包含 Phase 6 验证技能交付门证据（feature 名/命令/退出码/证据路径）与 Phase 7 维护 outcome（clean/changed/blocked）；两项缺失不得报发布收口
- **Status:** pending

## 跳过规则

- 跳过任何步骤必须留痕：`- [x] <步骤>（跳过：<原因>）`，禁止直接删行。
- 可跳过：无发布动作的纯文档版本可跳过 push 以外的同步步骤（原因写清）；不可跳过：版本一致校验、全量测试与静态检查、验证技能交付门（安装后）、升级后重启与可交接检查、证据落盘、发布后维护循环（完整执行 `maintain-verification.md` Phase 1–6；source wave + live pass 是下限，不替代不可跳过项）。
- 跳过与阶段顺序调整记入该流 plan 的 `## Decisions Made`。
