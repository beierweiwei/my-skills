# 剧本：release（技能版本发布/升级）

> 技能层通用剧本；项目同名剧本放 `.planning/playbooks/release.md` 覆盖本文件。解析契约与跳过留痕写法见 `README.md`。

## 适用与退出条件

- 适用：技能版本发布/升级——版本号与 CHANGELOG、全量测试与静态检查、同步与 push、用户目录刷新、项目 upgrade/init/doctor、升级后重启与可交接检查、安装后验证技能交付门与发布后维护循环。
- 退出：五处版本一致且检查全绿；同步与 push 完成；用户目录与项目安装均为新版本；升级后 guard 重启验证通过、guard 存在核验（`python3 scripts/doctor.py --only runtime.guard` pass 且 detail 带 pid）通过、长驻角色处置与可交接性实测留证；收口回执发出；发布后维护循环结论（clean/changed/blocked）已落盘并写入发布回执；收口/终验报告落盘合规——禁止无引号 heredoc/命令替换；单引号 heredoc 写 `/tmp` 再 `cp`，或 Write 工具。

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
- [ ] **代理不可用判定**：update 阶段 rc≠0 且输出 `Completed with failures` / `Failed to connect ... via <代理地址>` / git `Could not resolve proxy`（2.2.14 实测 http=000 rc=7；`Failed to connect ... port` 的 port 段是目标端口 443，不是代理端口，不作判据）→ 判代理不可用；先换可达 `--proxy URL` 或 `--no-proxy` 重试
- [ ] **回退路径**：回退命令即同一条 scoped 刷新命令（无需手工改跑 install-skills.sh）：带 `--skill` 且所请求技能在本地可用（`repos/local` 或既有缓存）时，`update-all.sh` 自动跳过更新做 scoped install，rc=0 且输出 `update 失败 → 已回退安装（rc=N）`；update 失败信号保留在日志，证据注明「回退」；无 `--skill` 全量、远端技能无本地缓存、安装自身失败时保持非 0，不得静默放行
- [ ] **离线模式**：不需要刷新远端缓存时显式 `bash scripts/update-all.sh --offline --skill agent-crew --agent qoder-cn,claude --skip-setup`（跳过 update 直接安装，rc 取安装结果）；证据写明改用离线的原因
- [ ] **证据要求**：`user-install.txt` 记录最终命令、update 失败信号（如有）、回退/离线判定、rc、安装后版本对照
- [ ] 校验用户目录 SKILL.md `version` 与技能源一致（全部已装技能逐项对照）；命令输出落 `.planning/<task>/evidence/release/user-install.txt`
- [ ] **S2 diff 口径**：安装/比对用 `diff -rq -x __pycache__ -x .pytest_cache`（或等价排除），`__pycache__`/`.pytest_cache` 噪声不算差异；先记缓存基线再比对
- **Status:** pending

### Phase 5: 项目升级与体检
- owner: firstmate
- [ ] `python3 scripts/upgrade.py --apply --confirm`：自动同步 config `skillVersion` 与 manifest（不要手工改 config，避免「版本已更新而脚本仍旧」的假象），记录 backup id；输出落 `.planning/<task>/evidence/release/upgrade-apply.txt`
- [ ] **源新增受管脚本时 upgrade 需跑两次**：项目内旧 upgrade（pre-T34，只认本进程清单）首跑复制完旧清单脚本即 rc=0 并写版本，新脚本漏装。判定：首跑后 `python3 scripts/doctor.py --only install.scripts_source` 报 `项目缺失 N 项：scripts/...`（或首跑输出无 `补登` 而源清单已增）；处置：再运行一次 `python3 scripts/upgrade.py --apply --confirm`（此时项目内 upgrade 已是新版），输出应含 `补登 N 个受管脚本条目` 且 doctor pass；两次 upgrade 输出与 doctor 结果并入 `evidence/release/upgrade-apply.txt`
- [ ] **汇总门（发布前）**：`python3 scripts/release-check.py --dry-run`——聚合版本/安装 diff/upgrade/init/doctor/guard 为 pass/fail 表；发布前允许在途 fail，逐项记录由后续哪一步消除（用户目录刷新/upgrade/提交），不替代上述逐步命令；输出落 `.planning/<task>/evidence/release/release-check-pre.txt`
- [ ] `python3 scripts/init.py --check`（输出落 `evidence/release/init-check.txt`）与 `python3 scripts/doctor.py --root . --profile release --strict --json`（输出落 `evidence/release/doctor-after.json`）：按当前基线核对通过数/失败数；`--strict` 下 warn 也会使 rc=1（既有语义），判定以 fail=0 为准，warn 计数记录并在证据注明触发原因（含 H2/H3 新增 warn），不得因 warn 停发；仅有未解释的 fail 才停，按输出 `next` 处置
- [ ] 项目仓本地 commit（只提交发布相关改动）
- **Status:** pending

### Phase 6: 升级后重启与可交接检查
- owner: firstmate
- [ ] ① 先无锁取证、再启动 guard：记录旧进程 pid/cmdline 并停止 → 在无 guard 持锁时跑 `python3 scripts/crew-guard.py --once --json`（stdout 必须为非空 JSON，落 `evidence/release/guard-once.json`）→ 启动长驻 `python3 scripts/crew-guard.py --helm <当前舵> --interval 120` → 验证 `<crew-dir>/guard.lock` / pid / cmdline 与新代码生效探针（wake 写 `generated_at:` 或 `--help` 新子命令）；长驻实例持锁时第二实例静默 rc=0、不产生输出，不得作为证据；任一失败即停，不得报发布完成
- [ ] **必核 guard 存在（①判据）**：`python3 scripts/doctor.py --only runtime.guard`——status=pass 且 detail 带 pid 才算通过；warn/fail 按输出 `next` 启动 `python3 scripts/crew-guard.py --helm <当前舵> --interval 120` 后复跑；未通过不得报发布收口（输出并入 `guard-restart.txt`）
- [ ] **汇总门（发布后）复跑**：`python3 scripts/release-check.py --dry-run` 必须全 pass（skip 需注明原因），fail 清零才可继续；输出落 `.planning/<task>/evidence/release/release-check-post.txt`
- [ ] ② 长驻角色处置：compose 在启动时快照，升级不热更新已运行会话——helm 换舵（新 tab 新舵、首报后关旧）、firstmate 重建/重派、one-shot worker 下次启动自动取新模板；处置写入发布回执
- [ ] ③ 可交接性实测：`herdr pane read <helm pane> --lines 80` 可见 `ctx Nk/1M`、① 无锁窗口采集的 `--once --json` 非空 JSON、`python3 scripts/dispatch.py compose --role helm --json` rc=0。**双 runtime 判读**：项目同时装 qoder+opencode 时，compose 按 `<crew-dir>/dispatch-agents.json` 中同 role 最新 `startedAt` 记录自动解析现役 runtime（human/JSON 输出 `runtimeResolvedFrom=registry:<agent>`，无需手补 `--runtime`）；若拒绝（无现役记录或最新记录并列），按提示确认现役总舵/该角色实际运行的 runtime 并补 `--runtime qoder|opencode` 后重跑至 rc=0；解析来源（`runtimeResolvedFrom` 或显式 `--runtime`）记入 `handover-readiness.txt`
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
- [ ] 报告落盘：禁止无引号 heredoc/命令替换；单引号 heredoc 写 `/tmp` 再 `cp`，或 Write 工具（无引号 heredoc 内的命令替换会触发 Qoder 权限弹窗并卡住会话）
- [ ] 评审/终验报告必须与回执成对：报告路径/结论/工位处置随回执一并给出，缺回执不得报收口（交叉 FB-041）
- [ ] 按 R2 写回执：版本/commit/push/用户目录/init/doctor/guard 新 pid/guard 存在核验（`doctor --only runtime.guard` 结果）/长驻角色处置/工位处置
- [ ] 更新 plan 的 Status 与 Next Step，刷新看板（`python3 scripts/plan-status.py --write`）；交接前 `--check-fresh`
- [ ] 回执包含 Phase 6 验证技能交付门证据（feature 名/命令/退出码/证据路径）与 Phase 7 维护 outcome（clean/changed/blocked）；两项缺失不得报发布收口
- [ ] 回执含 `python3 scripts/release-check.py --dry-run` 全 pass 结果与 pre/post 两份证据路径（fail/skip 清零或注明原因）
- **Status:** pending

## 跳过规则

- 跳过任何步骤必须留痕：`- [x] <步骤>（跳过：<原因>）`，禁止直接删行。
- 可跳过：无发布动作的纯文档版本可跳过 push 以外的同步步骤（原因写清）；不可跳过：版本一致校验、全量测试与静态检查、验证技能交付门（安装后）、升级后重启与可交接检查、证据落盘、发布后维护循环（完整执行 `maintain-verification.md` Phase 1–6；source wave + live pass 是下限，不替代不可跳过项）。
- 跳过与阶段顺序调整记入该流 plan 的 `## Decisions Made`。
