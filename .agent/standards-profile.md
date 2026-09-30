# Standards Profile — FunClip-main

> 本文件记录本项目对公共规范（Agent-Engineering-Standards v0.21.0）的补充、适配、例外和历史差距，不复制公共规范正文。冲突时按 `.agent/standards/adoption.md` 优先级处理，实质性冲突由项目负责人批准。

## 入口与优先级

- **唯一 Agent 入口**：`AGENTS.md`
- **公共规范基线**：`.agent/standards/`（v0.21.0，`sourceCommit=49b78bb4fb4f6e35ef5eb95be8e81e748aa8cb69`，installMode=`committed`）
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
| `delivery.md` 分阶段交付 | 需求 → 方案 → 切片 → 验收；方案先核对复用模式 | `docs/sop/01..05` 已完整实现；项目已有可复用服务模式，但尚无统一模式清单 | `legacy-gap` | 保持现有 SOP；新增模块先复用已验证模式，并逐步建立模式清单 | 项目负责人 | 2026-Q4 |
| `delivery.md` 功能点清单与使用说明书（v0.20 新增） | 模块开发前先列全功能点；每切片测试通过后同步图文说明书和完成标记 | 现有 REQ / DESIGN / VERIFICATION 与根目录使用说明书未形成“模块清单 ↔ 说明书章节”双向对应 | `legacy-gap` / 新模块 `adopt` | 新模块落 `docs/manual/<module>-checklist.md` 与 `docs/manual/<module>.md`；存量说明书后续迁移 | 项目负责人 | 2026-Q4 |
| `delivery.md` 编码统一 UTF-8（v0.19 新增） | 新建或修改文件统一 UTF-8，工具写文件显式指定编码 | 历史文件编码来源不统一，当前无法证明全仓库一致 | `legacy-gap` / 新改动 `adopt` | 本次触及文件强制 UTF-8；未触及文件不做批量转换 | 项目负责人 | 持续 |
| `delivery.md` 根因优先 | 复现 → 定位根因 → 影响面 → 源头修复 → 回归验证 | 5 阶段 SOP 未明确段落 | `legacy-gap` | 在 `docs/sop/04-review.md` 补充根因优先小节 | 项目负责人 | 2026-Q4 |
| `verification.md` 验收场景 | 每条标准 = 验证方式 + 结果 + 证据 | `docs/sop/05` 已完整实现 | `adopt` | 保持 | — | — |
| `quality-gates.md` 风险分级评审 | 按 low/medium/high/max 决定评审 effort | `docs/sop/04` 已通过 `/code-review <effort>` skill | `extend` | 在 SOP 中交叉引用 `quality-gates.md`；保持现有 effort 级别 | 项目负责人 | 2026-Q4 |
| `decisions.md` ADR 模板 | 决策背景 / 取舍 / 后果 / 生命周期 | `docs/design/DESIGN-*.md` 含"关键决策"段落（背景/取舍/后果） | `specialize` | 保留项目约定；关键决策段落对齐 ADR 模板；不另开 ADR 目录 | — | — |
| `technical-debt.md` 技术债治理 | 问题 / 风险 / 负责人 / 期限 / 退出条件 | 散落在 `docs/REQM/` 与 memory/ 中 `funclip-*.md` 条目 | `specialize` | 维护现有约定；需要时引用 `templates/decision-record.md` 模板 | — | — |
| `incident-management.md` 事故复盘 | 止损 → 根因 → 纠正项 → 验证闭环 | `memory/funclip-recovery-pattern.md`（不复用 `git reset --hard`） | `specialize` | 现有约定与公共规范等价；继续维护 | — | — |
| `agent-runtime-security.md` | 身份 / 工具权限 / 提示注入 / 上下文隔离 / 审计（v0.17 追加：项目知识不得写入用户级目录） | `.claude/settings.json/.local.json` 部分覆盖（权限白名单 + Hooks） | `extend` | 保留 settings.json 工具白名单；不引入新工具权限；知识放置按 `knowledge-placement.md` 行执行 | — | — |
| `knowledge-placement.md`（v0.15 新增） | 项目知识必须入仓库受版本管理位置；用户级记忆只存个人偏好/环境特性；禁止双源；存量须迁移留痕 | 用户级 memory/ 存有 20+ 条项目知识（v0.14 接入前形成，含项目事实/协作规则/REQ 历史/技术坑）；迁移清单已备：`docs/knowledge-migration.md` | `legacy-gap`（存量）/ 新知识立即 `adopt` | 执行迁移清单（A→standards-profile、B→project-context、C→docs/REQM、D→测试优先）；迁移完成前新知识一律直接落仓库 | 项目负责人 | 2026-Q4 |
| `task-intake.md`（v0.16 新增） | 受理模板（类型/目标/不做什么/风险/验收/事实假设）+ 集中询问（≤3 问）+ 受理卡入库 | `docs/sop/01-requirements.md` 需求澄清 + `requirements/REQ-<id>.md`（每条有验收标准）已等价覆盖大部分；受理卡落点即 REQ 文档 | `specialize` | 以 SOP-01 + REQ 文档为受理机制；Agent 受理时对照受理模板补缺项（不做什么/风险初判/≤3 问集中确认） | — | — |
| `adoption.md` 安装模式与分发（v0.17 新增） | installMode 三选一并记入锁文件；`committed` = 全部文件含 `.agent/standards/` 入库 | 本仓库 `.agent/standards/` 已入库（v0.14 起）；锁文件已记录 `installMode=committed` | `adopt` | 保持 `committed`（存量项目默认不变）；`.agent/backups/` 已在 .gitignore 受管区块忽略 | — | — |
| `orchestration.md` 泳道与并行执行（v0.18 新增） | 按触达文件/模块划泳道；写并发 ≤3；隔离工作区；逐条合并和验证 | 当前任务多为单泳道串行，现有 SOP 未定义并行写隔离流程 | `adopt` | 出现多写任务时使用 worktree/等价隔离并记录触达文件；单泳道任务不额外引入并行流程 | — | — |
| `long-running.md` 长任务监督器与会话内长任务（v0.18 / v0.21 新增） | 监督器续跑、独立预算、租约、停滞检测、监督日志；会话内子 Agent 日志可见 | 项目为本地单进程工具，当前未接入外部监督器；也未形成会话内子 Agent 执行日志 | `not-applicable`（当前）/ 按需 `adopt` | 仅当用户明确要求数小时连续或无人值守执行时评估监督器；主 Agent 派发子 Agent 时补 `logs/<task-id>/progress.md` | 项目负责人 | 触发时 |
| `testing.md` 不稳定测试治理（v0.18 新增） | 连跑取证、隔离清单、负责人/时限、禁止直接改产品代码消偶发失败 | 当前没有 flaky 隔离清单或固定治理流程 | `adopt` | 发现 flaky 后建立隔离清单；在短视频模块测试中按此规则执行 | — | 持续 |
| `dev-metrics.md`（v0.18 新增） | 首次验收通过率、返工率、周期时间、每任务回合数从既有记录自动汇总 | 当前有 REQ / VERIFICATION / execution_history，但无统一指标聚合 | `legacy-gap` | 先保留现有记录；后续评估从 REQ/验收记录和任务监督日志中汇总 | 项目负责人 | TBD |
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
| `incident-management.md` 事故复盘 | `memory/funclip-recovery-pattern.md`（不复用 `git reset --hard`，按 E2E 蓝图完整重做） | 与公共规范"止损 → 根因 → 纠正项 → 验证"等价 | memory 中现有条目（如 `funclip-bg-alpha-white-frame.md`、`funclip-bgm-e2e-corrupt-video.md`）— **v0.15 后 memory/ 不再是合规落点，待按迁移清单 D 类转入测试/文档** |
| `technical-debt.md` 技术债 | `docs/REQM/REQ-*.md` 追踪 + memory/ 中 `funclip-*.md` 散落记录 | 公开规范要求"问题 / 风险 / 负责人 / 期限 / 退出条件"；项目用 REQ 历史 + memory 散落实现 | `docs/REQM/REQ-20260923-NNN` + memory/funclip-*.md 现有条目 — **memory 部分待按迁移清单处理** |

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
| `knowledge-placement.md` 用户级记忆存量项目知识未迁移 | Claude 用户级 memory/（20+ 条） | 知识不共享、无版本历史、违反禁止双源；v0.17 升级时审计已确认存在 | 按 `docs/knowledge-migration.md` 迁移清单执行（A 协作规则 / B 项目事实 / C REQ 记录 / D 技术坑转测试） | 清单收尾验收 4 条逐项核对 + `standards verify` | 项目负责人 | 2026-Q4 |

## 待确认事项

1. **`acceptance-automation.md` / `Agent-Acceptance-Platform` 是否接入？**（本任务范围外，需要单独评估）
2. **`logging.md` 统一日志是否在本项目有意义？**（桌面工具，事件量低，需评估成本/收益）
3. **`frontend-quality.md` 前端指标（axe-core / Lighthouse）引入计划？**
4. **决策记录是否拆出独立 ADR 目录？**（当前复用 `docs/design/`，若团队习惯改变可拆出）
5. **CLAUDE.md 是否需要把 Claude Code 特有内容迁到独立文件（如 `CLAUDE-INTEGRATION.md`）？**（当前"CLAUDE.md 适配层 + AGENTS.md 入口"结构清晰，暂不需要）
6. **知识迁移清单（`docs/knowledge-migration.md`）何时执行？**（涉及删除用户级记忆条目，需用户确认后执行；执行前新知识一律直接落仓库，不再写入用户级记忆的项目知识区）

## 升级路径

公共规范升级时（参见 `standards/adoption.md` §「公共规范升级」）：

1. 对比新增、修改和删除的规则
2. 只重新评估受影响的映射、适配和例外
3. 更新本文件
4. 通过 `sync-standards.ps1` 更新 `AGENTS.md` 受管区块 + `.agent/standards/*.md`
5. 在业务项目中按安装模式提交相应文件（见 `standards/adoption.md` §「安装模式与分发」）

### 升级记录

- **2026-09-28 v0.14.0 → v0.17.0**：新增 `knowledge-placement.md` / `task-intake.md` 两份规范与 `adoption.md` 安装模式章节；`agent-runtime-security.md` 追加用户级目录写入禁令；映射表新增 3 行、legacy-gap 新增记忆迁移行；installMode 保持 `committed`（存量默认）。`standards verify` 通过。
- **2026-09-30 v0.17.0 → v0.21.0**：新增 `dev-metrics.md`；`delivery.md` 增加复用优先、UTF-8、功能点清单和图文使用说明书；`orchestration.md` 增加泳道并行；`long-running.md` 增加监督器与会话内长任务；`testing.md` / `quality-gates.md` 增加 flaky 治理。installMode 保持 `committed`，项目上下文与适配表同步更新。

当前锁定版本：`v0.21.0`（sourceCommit `49b78bb4fb4f6e35ef5eb95be8e81e748aa8cb69`，installMode `committed`）
