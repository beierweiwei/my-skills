# 剧本：release（技能版本发布/升级）

> 技能层通用剧本；项目同名剧本放 `.planning/playbooks/release.md` 覆盖本文件。解析契约与跳过留痕写法见 `README.md`。

## 适用与退出条件

- 适用：技能版本发布/升级——版本号与 CHANGELOG、全量测试与静态检查、同步与 push、用户目录刷新、项目 upgrade/init/doctor、升级后重启与可交接检查。
- 退出：五处版本一致且检查全绿；同步与 push 完成；用户目录与项目安装均为新版本；升级后 guard 重启验证通过、长驻角色处置与可交接性实测留证；收口回执发出。

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
- **Status:** pending

### Phase 5: 项目升级与体检
- owner: firstmate
- [ ] `python3 scripts/upgrade.py --apply --confirm`：自动同步 config `skillVersion` 与 manifest（不要手工改 config，避免「版本已更新而脚本仍旧」的假象），记录 backup id；输出落 `.planning/<task>/evidence/release/upgrade-apply.txt`
- [ ] `python3 scripts/init.py --check`（输出落 `evidence/release/init-check.txt`）与 `python3 scripts/doctor.py --root . --profile release --strict --json`（输出落 `evidence/release/doctor-after.json`）：按当前基线核对通过数/失败数，失败按输出 `next` 处置
- [ ] 项目仓本地 commit（只提交发布相关改动）
- **Status:** pending

### Phase 6: 升级后重启与可交接检查
- owner: firstmate
- [ ] ① 先无锁取证、再启动 guard：记录旧进程 pid/cmdline 并停止 → 在无 guard 持锁时跑 `python3 scripts/crew-guard.py --once --json`（stdout 必须为非空 JSON，落 `evidence/release/guard-once.json`）→ 启动长驻 `python3 scripts/crew-guard.py --helm <当前舵> --interval 120` → 验证 `<crew-dir>/guard.lock` / pid / cmdline 与新代码生效探针（wake 写 `generated_at:` 或 `--help` 新子命令）；长驻实例持锁时第二实例静默 rc=0、不产生输出，不得作为证据；任一失败即停，不得报发布完成
- [ ] ② 长驻角色处置：compose 在启动时快照，升级不热更新已运行会话——helm 换舵（新 tab 新舵、首报后关旧）、firstmate 重建/重派、one-shot worker 下次启动自动取新模板；处置写入发布回执
- [ ] ③ 可交接性实测：`herdr pane read <helm pane> --lines 80` 可见 `ctx Nk/1M`、① 无锁窗口采集的 `--once --json` 非空 JSON、`python3 scripts/dispatch.py compose --role helm --json` rc=0
- [ ] ④ 证据落 `.planning/<task>/evidence/release/`（`myskills-push.txt`、`user-install.txt`、`upgrade-apply.txt`、`init-check.txt`、`doctor-after.json`、`guard-once.json`、`guard-restart.txt`、`handover-readiness.txt`）；任一未完成或无证据不得报发布收口
- **Status:** pending

### Phase 7: 收口回执
- owner: firstmate
- [ ] 按 R2 写回执：版本/commit/push/用户目录/init/doctor/guard 新 pid/长驻角色处置/工位处置
- [ ] 更新 plan 的 Status 与 Next Step，刷新看板（`python3 scripts/plan-status.py --write`）；交接前 `--check-fresh`
- **Status:** pending

## 跳过规则

- 跳过任何步骤必须留痕：`- [x] <步骤>（跳过：<原因>）`，禁止直接删行。
- 可跳过：无发布动作的纯文档版本可跳过 push 以外的同步步骤（原因写清）；不可跳过：版本一致校验、全量测试与静态检查、升级后重启与可交接检查、证据落盘。
- 跳过与阶段顺序调整记入该流 plan 的 `## Decisions Made`。
