# Engineering（工程）

日常工程实践技能：测试、审查、设计、调试、重构，以及浏览器自动化等工具技能。

本目录同时放两类技能：**自研**（随库提交，可直接改）与**上游**（junction 指向 `repos/`，由 `scripts/update-skills.*` 拉最新，勿直接改）。

## 自研

- **[experience-kb-init](./experience-kb-init/SKILL.md)**：初始化/补齐项目经验知识库（`docs/经验/`），并接线 AGENTS.md。
- **[project-init](./project-init/SKILL.md)**：初始化项目/客户交付仓（目录、核心文档、项目 AGENTS.md、数据安全），可选接入经验知识库。
- **[agent-crew](./agent-crew/SKILL.md)**：herdr 多 agent 编排/计划看板/角色工位一键装入项目。
- **[create-verification-skill](./create-verification-skill/SKILL.md)**：为 repo 生成 feature map + 六段式验证技能（Launch/Doctor/Drive/Evidence/Cleanup/Helpers），生成后自跑通一个 feature 才交付（pstack MIT fork）。
- **[maintain-verification-skill](./maintain-verification-skill/SKILL.md)**：周期审计验证技能与 feature map，防漂移（pstack MIT fork）。
- **[interrogate](./interrogate/SKILL.md)**：多 adversary 子代理攻击 diff，只报经运行证实的问题、不修复（pstack MIT fork）。
- **[arena](./arena/SKILL.md)**：同题 2 候选并行竞争，rubric 盲评选基底，败者最强 1–2 点手工嫁接，验证后交付（经济档默认：跳过评委、主会话自评；pstack MIT fork）。
- **[correct](./correct/SKILL.md)**：把反复出现的 agent 错误按 架构>类型>lint>测试>文档 阶梯编进 repo 结构，每条规则必配执行器、用真实历史错误回放证明（pstack MIT fork）。
- **[engineering-disciplines](./engineering-disciplines/SKILL.md)**：通用工程纪律集（单技能六节）——断言二元、先磨后干、止损不盲试、运行证实、报告契约、无发现合法+同根因合并；手动调用或主舵点名触发，不在会话中自动触发。

## 上游（junction）

- **mattpocock/skills**：ask-matt、code-review、codebase-design、diagnosing-bugs、domain-modeling、grill-with-docs、implement、improve-codebase-architecture、prototype、research、setup-matt-pocock-skills、tdd、to-spec、to-tickets、triage、wayfinder、wizard
- **DietrichGebert/ponytail**：ponytail、ponytail-audit、ponytail-debt、ponytail-gain、ponytail-help、ponytail-review
- **nextlevelbuilder/ui-ux-pro-max-skill**：ui-ux-pro-max
- **anthropics/skills**：frontend-design
- **microsoft/playwright-cli**：playwright-cli
- **herdrdev/herdr**：herdr
- **wshobson/agents**：e2e-testing-patterns
