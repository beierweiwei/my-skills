# 全局协作原则

项目内或子目录更具体的 `AGENTS.md` / `CLAUDE.md` 优先；项目命令、技术栈约束和业务规则留在项目级文件。

## 沟通

- 思考与回答用简体中文；代码、命令、标识符、日志、引用维持原语言。
- 技术解释面向人类伙伴：用大白话 + 具体场景 / 约定的领域模型词汇，示例优于生硬术语；机制名（CAS、幂等、派生）先解释再使用。
- 完成目标后总结关键工作；经历多轮失败后，总结可复用的经验。

## 编码守则
### 编码前思考

- 实施前明确假设；有关键不确定性先问。
- 存在多种合理解释时呈现差异，不静默选一种。
- 有更简单方案就指出；对不合理方案提出异议。
- 不清楚就停下列出困惑点，弄清后再动手。

### 简洁优先

- 只写解决问题所需的最少代码：不加未要求的功能、一次性抽象、推测的灵活性、不可能场景的错误处理。
- 200 行能用 50 行清晰实现就重写。自检："资深工程师会觉得过度设计吗？"

### 精准修改

- 只改必须改的，遵循现有代码风格；每一行改动都能追溯到用户请求。
- 清理本次改动产生的孤儿（失效导入/变量/函数）；既有无关死代码只说明、不删。

### 目标驱动

- 把任务转成可验证标准再动手："加校验"→无效输入测试通过；"修 bug"→复现测试通过；"重构 X"→前后测试均绿。
- 多步任务列简短计划，每步写明验证方式。

## 工具与环境

### 搜索与网页

- 如果自带的联网搜索不可用则走走 exa MCP。
- 如果自带的WebFetch服务不可用，则使用哦 playwright-cli抓取

### 网络

- 境外网站/资源统一走本机代理 `http://127.0.0.1:7890`（本机没起代理服务时，按机器环境走局域网代理，地址记在各机器自己的说明里，不进本通用文件），用法：

  ```bash
  export http_proxy=http://127.0.0.1:7890
  export https_proxy=http://127.0.0.1:7890
  export all_proxy=http://127.0.0.1:7890
  ```

- GitHub API 匿名请求可能被限流，取版本信息失败时可改为直接下载 release 资产。
- Linux 软件源、DockerHub 等优先用国内镜像（清华、阿里云）。


## 统一记忆(inone)

- 我的跨 agent 统一记忆(偏好/事实/经验教训/纠错/项目上下文)单向生成在
  `~/inone-memory-dist/`(只读;由 inone 管道重生成,永不手编);
- 开始任务前或需要个人背景时，读 `~/inone-memory-dist/MEMORY-CORE.md`(≤150 行摘要);
- 需要某类型完整记忆时，读 `~/inone-memory-dist/topics/<kind>.md`(kind ∈ semantic|episodic|procedural，scope ∈ user|project:<名>);
- 我说「记住 X」，或会话中出现值得跨会话保留的内容(我的偏好/纠错、你亲历的坑与解法、代码推不出的项目决策)时，使用 inone-memory 技能推送(直接入库即时生效，不当记忆我在溯源页审核工作台删除);
- 永不编辑 `~/inone-memory-dist` 内的文件，永不把内容写回任何 agent 的 auto-memory 目录(各家 agent 的 memory 目录均为只读参考)。

## 本库约定

- 全局指令接入：`scripts/link-agents.*` 把本文件（AGENTS.md）/ CLAUDE.md 链接到各 Agent 全局位置（agents 约定、Claude Code、Codex、Qoder 国内版、opencode、ZCode），改本文件即对所有 Agent 生效。
- 技能按层放在 `skills/<层>/<技能>/`：`engineering`、`productivity` 是生产层（`scripts/install-skills.*` 默认安装），`in-progress` 需点名安装，`deprecated` 只留记录、永不安装。`skills/` 下全是指向 `repos/` 的 junction，由 `scripts/update-skills.*` 统一生成/拉最新（新克隆的机器先跑一次，否则 `skills/` 为空）：上游技能源在 `repos/<仓库>/`，改进上游提 PR 到上游仓库；自研技能源在 `repos/local/<层>/<技能>/`（随库提交），本地定制改这里。
- 技能自带 `scripts/setup.*` 时（如 inone-memory 配置凭据/装全局命令/拉记忆分发），`install-skills.*` 装完自动运行；`-SkipSetup` / `--skip-setup` 跳过，事后可手动重跑（幂等）。
- 生成 spec（to-spec）前：先读 [notes/to-spec-patches.md](notes/to-spec-patches.md) 的两条补丁。
