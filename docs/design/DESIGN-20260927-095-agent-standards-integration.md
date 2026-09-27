# DESIGN-20260927-095 — Agent-Engineering-Standards v0.14.0 接入

## Context

FunClip-main 当前有 5 阶段 SOP（`docs/sop/01..05`）+ Claude Code 工具配置（`.claude/`）+ Skill 体系（`slirn/` 子模块），但：

1. `CLAUDE.md` 虽是项目入口，**仅**对 Claude Code 工具生效（其他 Agent 工具不识别）
2. SOP 无版本锁定，团队成员 / CI / 不同机器可能漂移
3. SOP 缺漏：`delivery.md` 根因优先段落、`collaboration.md` 信息分类标签、`agent-runtime-security.md` / `data-governance.md` / `frontend-quality.md` 等领域规范
4. ADR / 事故复盘 / 技术债治理缺统一模板

`D:\Slirn\WorkSpaces\PriProjs\Agent-Engineering-Standards` 已发布到 **v0.14.0**（Git tag 校验通过，工作区干净），提供 29 个公共规范副本 + 一套 PowerShell 同步脚本。本次任务把公共规范基线接入 FunClip-main，让规范升级可一次同步。

## 决策摘要（已确认）

| 项 | 选择 |
|---|---|
| 公共规范来源 | `D:\Slirn\WorkSpaces\PriProjs\Agent-Engineering-Standards` v0.14.0 |
| 安装方式 | `sync-standards.ps1`（标准仓库自带脚本，含 tag 校验 + SHA-256 锁定） |
| 唯一 Agent 入口 | **新建 `AGENTS.md`**（受管区块由同步脚本维护） |
| CLAUDE.md 角色 | 降级为 **Claude Code 工具适配层**（顶部加迁移说明 + §11 接入记录，**保留**全部原内容） |
| 项目事实 | 新建 `.agent/project-context.md`（手填 FunClip 技术栈 / 命令 / 架构边界） |
| 项目适配 | 新建 `.agent/standards-profile.md`（6 类关系映射：adopt / extend / specialize / deviate / not-applicable / legacy-gap） |
| 版本锁定 | 新建 `.agent/standards.lock.json`（v0.14.0 + `sourceCommit=4b1763f` + 29 文件 SHA-256） |
| 试点反馈（备用） | 新建 `.agent/feedback/README.md`（仅占位，未启用试点反馈流程） |
| 现有 SOP | **保留** `docs/sop/01..05`，映射为 `adopt` 关系 |
| `funclip/` 上游源码 | **不动**（要改去上游 PR） |
| `slirn/` 子模块 | **不动**（改去 `../slirn-standalone/`） |

## 关键决策（ADR）

### 决策 1：是否引入 `AGENTS.md` 作为新入口？

**备选方案**：
- A. 保持 `CLAUDE.md` 为唯一入口（不引入 `AGENTS.md`）
- B. 引入 `AGENTS.md` 作为唯一入口，`CLAUDE.md` 降级为适配层 ✅
- C. 引入 `AGENTS.md` 但不修改 `CLAUDE.md`（双入口冲突）

**选定 B**：理由 — `standards/adoption.md` §「规范来源与执行入口」明确要求 `AGENTS.md` 是**唯一** Agent 执行入口；其他工具入口（如 CLAUDE.md、.cursor/rules）只能作为适配层，**不得**定义第二套权威规则。C 方案违反此原则。

### 决策 2：现有 5 阶段 SOP 是保留还是重写？

**备选方案**：
- A. 删除 `docs/sop/01..05`，改用公共 `delivery.md` / `verification.md`
- B. 保留 `docs/sop/01..05`，映射为 `adopt` 关系（与公共规范等价） ✅
- C. 保留但内容改写为公共规范镜像

**选定 B**：理由 — 5 阶段 SOP 是项目已沉淀 87 个 REQ + 17 个 VERIFICATION 的实战文档，与 `delivery.md` 形态一致；删除会丢失项目历史与肌肉记忆；改写会破坏现有 commit 引用。`adopt` 关系明确"直接采用公共规范"，保留项目条款同时声明与公共规范的等价关系。

### 决策 3：`docs/REQM/` 与 SOP §1 提到的 `requirements/` 不一致，如何处理？

**现状**：`docs/sop/01-requirements.md` § 产出 写 `requirements/REQ-<id>.md`，但实际存储在 `docs/REQM/REQ-YYYYMMDD-NNN-*.md`（大写后缀，与上游命名约定不同）。

**选定**：**暂不统一**，记为 `legacy-gap`。理由 — 87 个历史 REQ 文件已存在，重命名会破坏 git history 引用（DESIGN、VERIFICATION 都引用了 REQ 文件名）。在 `project-context.md`「项目特有规则」中注明 `docs/REQM/` 为实际位置，待未来迁移窗口再统一。

### 决策 4：`docs/design/` 复用为 ADR 目录还是另开 `docs/adr/`？

**备选方案**：
- A. 复用 `docs/design/`，ADR 段落内嵌 `DESIGN-*.md` 关键决策部分 ✅
- B. 新开 `docs/adr/` 目录，按 NNN 编号独立 ADR 文件

**选定 A**：理由 — `decisions.md` 公共规范允许 ADR 落地的任何稳定格式；项目已有 37 个 `DESIGN-*.md` 包含 ADR 风格的「关键决策」段落（背景 / 备选 / 取舍 / 后果）；新开目录会形成两条决策流。`standards-profile.md` § 等价适配明确声明此关系。

### 决策 5：`memory/` 用户级记忆是否与 `.agent/project-context.md` 重复？

**选定**：**职责分离，不重复**。理由：
- `.agent/project-context.md` = 公共规范入口要求的项目事实（提交到仓库，团队共享，版本受控）
- `memory/funclip-*.md` = 用户级自动记忆（不提交，按用户 / 项目 / 反馈分类，跨会话持久化）

`memory/` 中 `funclip-recovery-pattern.md`、`funclip-bg-alpha-white-frame.md` 等散落技术债条目映射为 `specialize` 关系（与 `incident-management.md` / `technical-debt.md` 等价）。

## 实施步骤（已完成）

| 步骤 | 操作 | 验证 |
|---|---|---|
| 1 | pre-check：tag 校验 + 工作区干净 | `git rev-parse v0.14.0^{commit}` = HEAD ✓ |
| 2 | `sync-standards.ps1 -Check` | 28 个 managed files 列出会复制 ✓（实际 29） |
| 3 | `sync-standards.ps1` 实际安装 | 29 个规范 + AGENTS.md + project-context.md + standards-profile.md + lock.json + feedback/README.md ✓ |
| 4 | 手填 `.agent/project-context.md`（164 行） | FunClip 技术栈 / 命令 / 架构边界 / 治理记录 ✓ |
| 5 | 手填 `.agent/standards-profile.md`（90 行，18 条映射） | 6 类关系全部使用 ✓ |
| 6 | `CLAUDE.md` 顶部加迁移说明 + §11 接入记录 | 保留全部原 §1-§10 内容 ✓ |
| 7 | 全面验证 | 锁文件 / 受管区块 / 文件计数 / 适配层 / hotword 未触动 / 依赖可导入 / pytest collect-only ✓ |
| 8 | 独立 commit `2cecd87`（35 文件，+2508 行） | `git log --oneline -3` 确认 ✓ |

## 影响范围

**新增**：
- `AGENTS.md`
- `.agent/standards/*.md`（29 文件）
- `.agent/standards.lock.json`
- `.agent/project-context.md`
- `.agent/standards-profile.md`
- `.agent/feedback/README.md`

**修改**：
- `CLAUDE.md`（顶部加 13 行迁移说明 + §11 接入记录；保留原 §1-§10）

**不动**：
- `funclip/`（上游源码）
- `slirn/`（submodule）
- `docs/sop/01..05`（保留为 `adopt` 关系）
- `.claude/settings.json` / `settings.local.json`（保留）
- `docs/REQM/` / `docs/design/` / `docs/verification/`（保留）
- `memory/`（保留为 `specialize` 关系）

## 验收标准

| 验收项 | 验证方式 | 结果 |
|---|---|---|
| 公共规范基线 29 文件全部安装 | `ls .agent/standards/*.md \| wc -l` = 29 | ✅ |
| AGENTS.md 受管区块正确 | `grep -c "agent-standards:begin/end"` 各 1 | ✅ |
| 版本锁定文件正确 | `standards.lock.json` 含 v0.14.0 + sourceCommit + 29 SHA-256 | ✅ |
| 项目事实完整 | `.agent/project-context.md` 164 行覆盖技术栈/命令/架构/治理 | ✅ |
| 项目适配映射完整 | `.agent/standards-profile.md` 18 条映射 + 3 条等价适配 + 4 条 legacy-gap | ✅ |
| CLAUDE.md 降级为适配层 | 顶部「入口迁移」说明 + §11 接入记录，原 §1-§10 保留 | ✅ |
| Hotword 工作未触动 | commit history 中 hotword 仍在 `e62b6f9` 单独 commit | ✅ |
| 依赖可导入 | `import funasr, gradio, moviepy` OK | ✅ |
| 测试套件可收集 | `pytest --collect-only` 收集 1008 tests | ✅ |
| 工作区干净提交 | `2cecd87` 一个独立 commit | ✅ |

## 后续工作（已识别）

按 `.agent/standards-profile.md` § 待确认事项 + legacy-gap：

| 工作 | 关系 | 截止 | 状态 |
|---|---|---|---|
| 补 `docs/sop/01` 信息分类标签（`[已确认]/[待确认]/[建议]`） | `collaboration.md` adopt | 2026-Q4 | 待办 |
| 补 `docs/sop/04-review.md` 根因优先段落 | `delivery.md` legacy-gap | 2026-Q4 | 待办 |
| 评估 `logging.md` 统一日志 | `logging.md` legacy-gap | TBD | 评估中 |
| 评估 `Agent-Acceptance-Platform` 接入 | `acceptance-automation.md` legacy-gap | TBD | 评估中 |
| 评估前端指标（axe-core / Lighthouse） | `frontend-quality.md` legacy-gap | TBD | 评估中 |
| 新会话验证 Agent 真的会读 AGENTS.md | USER-GUIDE §6.2 | 立即 | 下一步 |

## 关联

- 公共规范基线：`.agent/standards/`
- 项目事实：`.agent/project-context.md`
- 项目适配表：`.agent/standards-profile.md`
- 锁文件：`.agent/standards.lock.json`（v0.14.0 + `sourceCommit=4b1763faf6b607685ae967dbb34a3e29cf0e98bb`）
- Commit：`2cecd87 chore(agent-standards): 接入 Agent-Engineering-Standards v0.14.0`
- 公共规范来源：`D:\Slirn\WorkSpaces\PriProjs\Agent-Engineering-Standards`