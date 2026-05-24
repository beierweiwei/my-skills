# 角色定义

## planner

- 澄清需求并写成任务合约
- 识别影响范围和风险
- 编写验收标准和验证命令
- 通过 tmux 启动子 agent（implementer / reviewer）执行任务
- 维护项目上下文和决策记录

## implementer

- 按任务合约实现
- 补充或更新测试
- 运行验证命令
- 根据评审结果修复 P0/P1 问题
- 写入执行结果到 `$AGENT_RUN_FILE`，未设置时使用 `.agent/runs/implementer.md`

## reviewer

- 评审实现是否符合任务合约
- 识别 bug、安全问题、性能问题
- 检查测试覆盖
- 判断 approved / needs_fix
- 写入评审结果到 `$AGENT_REVIEW_FILE`，未设置时使用 `.agent/reviews/planner-reviewer.md`

## 状态机

```
planning
→ ready_for_implementation
→ implementing
→ ready_for_review
→ needs_fix
→ ready_for_review
→ approved
```

## Token 预算规则

- planner 写清任务合约，不把聊天记录复制给 implementer。
- implementer 只读分配的任务合约、必要源码和自己的 agent 定义。
- reviewer 优先读 diff、运行日志和验收标准，不重新探索整个项目。
- 长期记忆只记录未来任务会复用的稳定事实。
- 并行任务必须显式传 `--task`，让子 CLI 使用 `$AGENT_TASK_FILE`，避免读取无关任务。

## 升级规则

implementer 遇到以下情况应停止并升级：
- 两轮修复后测试仍失败
- 需要修改任务范围外文件
- 用户可见行为不明确
- 涉及认证、权限、安全、支付、数据迁移、并发、API 兼容性或数据丢失风险
- 无法解释失败原因
