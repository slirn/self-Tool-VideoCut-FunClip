# 存量知识迁移清单（用户级记忆 → 项目仓库）

> 依据 `Agent-Engineering-Standards` v0.15.0 `.agent/standards/knowledge-placement.md`（升级后生效）。
> 迁移对象：Claude 用户级记忆目录中属于项目知识的记录（`~/.claude/projects/<本项目编码>/memory/`）。
> 该目录不在仓库内，删除/精简操作需在本项目的 Claude 会话中执行或经用户确认。

## 判定回顾

换一个开发者、换一台机器、换一个 Agent 工具还有效 → 项目资产 → 迁入仓库；只对个人有效 → 保留用户级。

## 迁移表

### A. 协作规则 → `.agent/standards-profile.md`（或 `AGENTS.md` 受管区块外）

- [ ] `serial-task-execution-rule.md` — 任务串行执行规则迁入 standards-profile；记忆中删除
- [ ] `funclip-workflow-preferences.md` — 5 阶段 SOP + 质量门 + 自动执行偏好：项目协作部分迁入 standards-profile，纯个人偏好部分留在记忆

### B. 项目事实/架构约束 → `.agent/project-context.md`

- [ ] `funclip-project-layout.md` — 与 project-context.md「架构与边界」比对，补缺删重；记忆中删除
- [ ] `slirn-fc-path-prefix-convention.md` — `fc.materials.*.path` 必须含 `tasks/<tid>/` 前缀 → project-context 架构约束
- [ ] `slirn-real-time-vs-persistent-state.md` — 实时/持久状态放置规则 → project-context 架构约束
- [ ] `slirn-multi-entry-state-symmetry.md` — 多入口状态对称要求 → project-context 架构约束
- [ ] `slirn-compose-memory-constraints.md` — 长视频合成内存约束（WinError 1455）→ project-context 验证要求
- [ ] `funclip-save-export-keys-symmetry.md` — 任务级/模板级 export JSON 字段对称约定 → project-context 架构约束
- [ ] `gradio-6-file-api.md` — Gradio 6 文件 API 路径与 403 限制 → project-context 技术栈注意事项
- [ ] `agent-standards-integration.md` — 标准接入事实已由 `.agent/standards.lock.json` 记录；记忆中删除

### C. 需求/执行记录 → `docs/REQM/`（按项目 SOP）

- [ ] `req-049-resume-button-refresh.md` — 核对 `docs/REQM/` 是否已有对应 REQ 文档；缺失则补建，记忆中删除
- [ ] `req-090-combo-debug-panel.md`、`req-091-combo-time-params.md`、`req-091-v2-combo-test-ux-feedback.md`、`req-092-combo-button-rename.md`、`req-093-combo-setinterval-poll.md` — 同上逐条核对
- [ ] `pending-task-execution-log.md` — REQ-081~093 执行历史摘要迁入 `docs/REQM/` 索引或验证记录；记忆中删除

### D. 技术坑 → 测试优先，其次文档

- [ ] `router-js-pitfalls.md` — 确认 `node --check` + 关键函数完整性测试已在 CI/测试入口；文档化于 `docs/`（适用时）
- [ ] `js-edit-showtab-regression.md` — 确认 showTab 回归测试存在
- [ ] `funclip-bg-alpha-white-frame.md` — 确认 bg overlay helper 测试存在
- [ ] `funclip-white-box-subtitle-color.md` — 字幕颜色判定并入上述测试或文档
- [ ] `funclip-bgm-e2e-corrupt-video.md` — 「无 BGM」排查路径写入验证/排障文档
- [ ] `slirn-daemon-thread-nameError-gotcha.md` — 坑转化为测试或 `docs/` 记录

### E. 保留在用户级记忆（个人/环境特性，不改）

- `github-push-retry.md` — 本机网络特性
- `user-previews-in-vscode-browser.md` — 个人 IDE 环境

### F. 精简为指针

- [x] `funclip-sop.md` — 精简为「SOP 见 `docs/sop/`」纯指针，删除复制的阶段表（2026-09-28 执行；MEMORY.md 索引行同步改为指针措辞）
- [ ] `MEMORY.md` — 迁移完成后按剩余条目重建索引（依赖 A–E 先执行，暂不动）

## 收尾验收

- [ ] 记忆目录中仅剩 E 类个人条目和 F 类指针
- [ ] 所有迁入内容已提交 Git，可在 commit 历史追溯
- [ ] `.agent/project-context.md` 与迁移后事实一致，无双源
- [ ] D 类每条坑有对应测试或文档位置
