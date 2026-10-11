# Deprecated（已淘汰）

不再使用、也**永不安装**的技能在此留一行记录（本层只放记录与说明，不放技能目录）。

- **computer-use / orca-cli / orchestration**：上游仓库已删除，junction 失效，不再恢复。
- **resolving-merge-conflicts**：2026-10-11 移除。上游 v1.3.0 起从插件删除并归档（官方说明无替代，合并冲突由 agent 直接处理），junction 无法再生成。
- **typescript-reviewer**：2026-09-22 移除。上游只以 agent 单文件存在（`affaan-m/everything-claude-code` 的 `agents/typescript-reviewer.md`），不是技能目录，junction 无法引用；需要时按需从上游取，不再入本库。
- MCP 侧的淘汰记录见根 README「已淘汰」一节。

淘汰一个技能 = 从原层删除该目录，并在此写一行：名字、时间、被谁取代或为什么淘汰。
