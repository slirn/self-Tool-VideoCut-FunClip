# Standards Profile — FunClip-main

> 本文件记录本项目对公共规范（Agent-Engineering-Standards v0.14.0）的补充、适配、例外和历史差距，不复制公共规范正文。冲突时按 `.agent/standards/adoption.md` 优先级处理，实质性冲突由项目负责人批准。

## 入口与优先级

- **唯一 Agent 入口**：`AGENTS.md`
- **公共规范基线**：`.agent/standards/`（v0.14.0，`sourceCommit=4b1763f`）
- **项目事实**：`.agent/project-context.md`
- **项目适配与例外**：本文件
- **冲突优先级**（参见 `standards/adoption.md` §「生效规范与优先级」）：
  1. 法律法规、合同、安全与合规要求
  2. 组织级强制规范
  3. 公共规范中的强制规则和安全红线
  4. 项目特有规范（包括补充、加严和等价替换）
  5. 经批准的临时例外
  6. 单个任务中的实现决策

## 工具适配

| 工具入口 | 适配方式 | 是否存在独立规则正文 |
| --- | --- | --- |
| `CLAUDE.md` | 顶部加「入口迁移」说明，指向 `AGENTS.md`；保留 Claude Code 特有的权限 / Hooks / Skill 索引引用 | 否（仅作适配层） |
| `.claude/skills/INDEX.md` | Skill 调用索引，指向 `slirn/skill/<name>/SKILL.md` | 否（仅 Skill 列表，不是规范） |
| `.claude/settings.json` + `settings.local.json` | Claude Code 工具权限白名单 + PostToolUse/Stop Hooks | 否（工具配置，不是协作规则） |
| `.claude/skills/setup-junctions.{ps1,sh}` | 创建本地 Skill junction 让 Claude Code 发现 Skill（不入库） | 否 |

## 规范映射

| 规则编号 / 主题 | 公共规范要求 | 项目现状 | 关系 | 处理方案 | 负责人 | 截止时间 |
| --- | --- | --- | --- | --- | --- | --- |
| `collaboration.md` 信息分类 | 已确认 / 待确认 / 建议 / 可自主决定 | `docs/sop/01` 隐含但未明确标注 | `adopt` | 在 SOP 中显式标注三类标签 | 项目负责人 | 2026-Q4 |
| `delivery.md` 分阶段交付 | 需求 → 方案 → 切片 → 验收 | `docs/sop/01..05` 已完整实现 | `adopt` | 保持现有 SOP 不变 | — | — |
| `delivery.md` 根因优先 | 复现 → 定位根因 → 影响面 → 源头修复 → 回归验证 | 5 阶段 SOP 未明确段落 | `legacy-gap` | 在 `docs/sop/04-review.md` 补充根因优先小节 | 项目负责人 | 2026-Q4 |
| `verification.md` 验收场景 | 每条标准 = 验证方式 + 结果 + 证据 | `docs/sop/05` 已完整实现 | `adopt` | 保持 | — | — |
| `quality-gates.md` 风险分级评审 | 按 low/medium/high/max 决定评审 effort | `docs/sop/04` 已通过 `/code-review <effort>` skill | `extend` | 在 SOP 中交叉引用 `quality-gates.md`；保持现有 effort 级别 | 项目负责人 | 2026-Q4 |
| `decisions.md` ADR 模板 | 决策背景 / 取舍 / 后果 / 生命周期 | `docs/design/DESIGN-*.md` 含"关键决策"段落（背景/取舍/后果） | `specialize` | 保留项目约定；关键决策段落对齐 ADR 模板；不另开 ADR 目录 | — | — |
| `technical-debt.md` 技术债治理 | 问题 / 风险 / 负责人 / 期限 / 退出条件 | 散落在 `docs/REQM/` 与 memory/ 中 `funclip-*.md` 条目 | `specialize` | 维护现有约定；需要时引用 `templates/decision-record.md` 模板 | — | — |
| `incident-management.md` 事故复盘 | 止损 → 根因 → 纠正项 → 验证闭环 | `memory/funclip-recovery-pattern.md`（不复用 `git reset --hard`） | `specialize` | 现有约定与公共规范等价；继续维护 | — | — |
| `agent-runtime-security.md` | 身份 / 工具权限 / 提示注入 / 上下文隔离 / 审计 | `.claude/settings.json/.local.json` 部分覆盖（权限白名单 + Hooks） | `extend` | 保留 settings.json 工具白名单；不引入新工具权限 | — | — |
| `logging.md` 统一日志 | 统一日志 API + 流程追踪 + 诊断页面 | 无统一日志 API；`funclip/` 内置 logging + `slirn_home/` 各服务 logging + `execution_history.py` | `legacy-gap` | 当前通过 memory/ + REQM 追踪；评估是否引入统一日志 | 项目负责人 | TBD |
| `data-governance.md` 数据治理 | 分类 / 最小化 / 共享 / 保留 / 删除 / 脱敏 / 泄漏响应 | 用户数据 = 本地 JSON（用户名 + pbkdf2 hash + cookie session）；视频数据 = 本地处理 | `extend` | 用户密码 pbkdf2 200000 迭代；session HttpOnly + SameSite=Lax；视频数据零上传 | 项目负责人 | 持续 |
| `slo-resilience.md` SLO 容灾 | SLI/SLO / 错误预算 / 容量 / 备份恢复 / RTO/RPO / 演练 | 单进程 Gradio 桌面工具 | `not-applicable` | 无 SLO 概念 | — | — |
| `acceptance-automation.md` 验收自动化 | 自动审查 / 功能验收 / 安全验收 / 48/72h / 影子 | 仅手动 pytest + Stop Hook | `legacy-gap` | 评估是否接入 `Agent-Acceptance-Platform v0.2.0` | 项目负责人 | TBD |
| `release-provenance.md` 发布溯源 | 版本 / tag / 来源 commit / 构建产物 / 复现 | git tag + Conventional Commits + 历史 bundle 备份 | `extend` | 现有约定符合公共规范最低要求 | — | — |
| `frontend-quality.md` 前端质量 | 可访问性 / 国际化 / 响应式 / 性能预算 / 安全 | `slirn_home/static/` 手写 CSS/JS；无设计令牌 / 组件库 | `legacy-gap` | 评估响应式 / 可访问性 / 性能预算 | 项目负责人 | TBD |
| **Skill 体系**（项目特有） | 公共规范未规定 Skill | `.claude/skills/INDEX.md` + `slirn/` submodule（6 个 Skill） | `extend` | 公共规范不约束 Skill；保留项目体系 | — | — |
| **slirn submodule**（项目特有） | 公共规范不假设 submodule | `slirn/` git submodule（指向 `../slirn-standalone/`） | `extend` | 公共规范不约束；保留 | — | — |
| **CLAUDE.md vs AGENTS.md**（入口迁移） | `AGENTS.md` 为唯一入口 | CLAUDE.md 是项目入口，148 行 | `extend` | CLAUDE.md 降级为 Claude Code 适配层（顶部加迁移说明，保留原内容） | 项目负责人 | 已完成 |

## 等价适配（specialize）

| 公共要求 | 项目机制 | 等价性说明 | 验证证据 |
| --- | --- | --- | --- |
| `decisions.md` ADR 决策记录 | `docs/design/DESIGN-*.md` 中"关键决策"段落（背景 / 取舍 / 后果 / 拒绝方案理由） | 与 `decisions.md` 模板对齐；37 个 DESIGN 文件已包含 ADR 风格段落 | `docs/design/DESIGN-20260919-061-fine-cut-material-compositor.md` 等实例 |
| `incident-management.md` 事故复盘 | `memory/funclip-recovery-pattern.md`（不复用 `git reset --hard`，按 E2E 蓝图完整重做） | 与公共规范"止损 → 根因 → 纠正项 → 验证"等价 | memory 中现有条目（如 `funclip-bg-alpha-white-frame.md`、`funclip-bgm-e2e-corrupt-video.md`） |
| `technical-debt.md` 技术债 | `docs/REQM/REQ-*.md` 追踪 + memory/ 中 `funclip-*.md` 散落记录 | 公开规范要求"问题 / 风险 / 负责人 / 期限 / 退出条件"；项目用 REQ 历史 + memory 散落实现 | `docs/REQM/REQ-20260923-NNN` + memory/funclip-*.md 现有条目 |

## 已批准例外（deviate）

> 无。任何潜在例外将在项目负责人批准后填入此节。

## 历史差距（legacy-gap）

| 差距 | 受影响范围 | 当前风险 | 迁移切片 | 验证方式 | 负责人 | 截止时间 |
| --- | --- | --- | --- | --- | --- | --- |
| `delivery.md` 根因优先段落缺失 | `docs/sop/04-review.md` | 评审时可能停留在表面症状 | 在 §4-review 中加入"根因优先"小节（参考 `standards/delivery.md` 根因优先） | 文档 diff + 下次 REQ 评审验证 | 项目负责人 | 2026-Q4 |
| `logging.md` 统一日志缺失 | 全项目（funclip + slirn_home） | 跨服务事件追踪靠文档 + memory，不结构化 | 第一步：评估是否需要；第二步：在 `slirn_home/` 引入轻量日志 API | 新增日志场景 E2E | 项目负责人 | TBD |
| `acceptance-automation.md` 平台未接入 | 测试 + 验收流程 | 仅手动 pytest，无独立审查证明 / 48h 运行 | 评估 `Agent-Acceptance-Platform v0.2.0` 是否适配本项目 | 试点 + 决策 | 项目负责人 | TBD |
| `frontend-quality.md` 前端指标缺失 | `slirn_home/static/` | 无可访问性 / 响应式 / 性能度量 | 评估引入 axe-core / Lighthouse CI | 报告分数 + 性能预算 | 项目负责人 | TBD |
| `architecture-fitness.md` 模块依赖检查缺失 | `funclip/` + `slirn/` + `slirn_home/` | 依赖规则靠文档约束，无自动 lint | 评估 `standards architecture check` 是否可加 funclip 边界规则 | CI 跑通 | 项目负责人 | TBD |

## 待确认事项

1. **`acceptance-automation.md` / `Agent-Acceptance-Platform` 是否接入？**（本任务范围外，需要单独评估）
2. **`logging.md` 统一日志是否在本项目有意义？**（桌面工具，事件量低，需评估成本/收益）
3. **`frontend-quality.md` 前端指标（axe-core / Lighthouse）引入计划？**
4. **决策记录是否拆出独立 ADR 目录？**（当前复用 `docs/design/`，若团队习惯改变可拆出）
5. **CLAUDE.md 是否需要把 Claude Code 特有内容迁到独立文件（如 `CLAUDE-INTEGRATION.md`）？**（当前"CLAUDE.md 适配层 + AGENTS.md 入口"结构清晰，暂不需要）

## 升级路径

公共规范升级时（参见 `standards/adoption.md` §「公共规范升级」）：

1. 对比新增、修改和删除的规则
2. 只重新评估受影响的映射、适配和例外
3. 更新本文件
4. 通过 `sync-standards.ps1` 更新 `AGENTS.md` 受管区块 + `.agent/standards/*.md`
5. 在业务项目中提交规范副本、锁定文件、受管区块和适配表变更

当前锁定版本：`v0.14.0`（sourceCommit `4b1763faf6b607685ae967dbb34a3e29cf0e98bb`）