# REQ-20261013-107 — 短视频 Stage 7「详情面板窄宽」actions-bar 按钮竖排修复

> 状态：✅ 已闭环（2026-10-13）
> 父任务：[REQ-20261013-106](REQ-20261013-106-sv-stage7-ui-relayout-by-long-video.md)（11 block 重排已闭环）
> 兄弟任务：父父 [REQ-20261013-105](REQ-20261013-105-sv-stage7-fine-port.md)（C1 端到端 OK）

## 解决什么问题

用户在 2026-10-13 反馈：Stage 7「精剪混编」详情页（workbench 右栏 stage7 pane）在 1280px 视口下被左侧 stage-nav（260px 固定列）挤压成 ~282px 宽，**每个 highlight 卡片内的 `.slirn-fine-actions-bar` 按钮被压成「一字一行」竖排**（"导出最终视频" 显示成"导/出/最/终/视/频"垂直堆叠）。

### 根因诊断

1. `.slirn-sv-pipeline { grid-template-columns: 260px minmax(0, 1fr) }`（home.css:5336）— 左 260px stage-nav 固定，右 1fr pane
2. 1280px 视口下右栏 pane 仅 ~1000px，但 workbench tab 进一步切分了 stage6-clip 列表 + 主 stage7 pane，使 stage7 pane **实际只有 ~280px**
3. `.slirn-stage6-clip { display: flex; flex-direction: column; gap: 6px }`（home.css:6074）— card 强制 column 排
4. `.slirn-fine-actions-bar { flex-wrap: nowrap; overflow-x: auto }`（home.css:3934）— 子元素**强制一行**，容器**横向滚动**
5. `.slirn-btn` 没 `flex-shrink: 0` 且没 `white-space: nowrap` — 容器变窄时 **button 宽度被压缩到 ~16px**，文字被压成**单字一行**

**关键矛盾**：父容器已经窄到 264px（实测），但 `flex-wrap: nowrap` 强制 button 不换行 + `flex-shrink: 1`（默认）允许 button 被无限压缩。结果：3 个 button 各占 ~16-20px 宽，文字"导出最终视频"6 字各占一行 = **视觉上呈"一字一行的竖排"**。

## 解决方案

**3 行 CSS 改动（v1 + v2 + v3 三轮迭代）**，零 Python / JS / 后端改动：

| # | 文件 | 改动 | 原因 |
|---|------|------|------|
| v1 | `slirn_home/static/home.css:3934-3936` | `.slirn-fine-actions-bar` 试用 `flex-wrap: wrap` | wrap 修复"文字竖排"但视觉空荡 |
| v2 | `slirn_home/static/home.css:3934-3936` | 改回 `flex-wrap: nowrap; overflow-x: auto` | nowrap+overflow 解决"空荡"，但暴露根因：card 仅 367px |
| v3 | `slirn_home/static/home.css:6086` | 加 `.slirn-stage4/5/6-clip:only-child { grid-column: 1 / -1 }` | **根因修复**！`.slirn-stage6-list` 是 `grid-template-columns: repeat(auto-fill, minmax(280px, 1fr))`，单 child 时 auto-fill 仍生成多空列，1 child 实际只占 1 列 367px。`grid-column: 1 / -1` 让 only-child 撑满所有列 |
| v2 | `slirn_home/static/home.css:162-163` | `.slirn-btn` 加 `white-space: nowrap; flex-shrink: 0` | button 内部文字保持一行，button 保持自身最小宽度不压缩 |

### v1 → v2 → v3 调整理由

- **v1 wrap**：3 button 换行到 3 行 137px 高，**空荡感**与"按钮竖排"观感相似
- **v2 nowrap + overflow-x: auto**：3 button 横向一行 64px 高，**紧凑但 card 仅 367px 宽**，触发横向滚动（scrollW=540 > w=349）
- **v3 grid-column: 1 / -1**：**根因修复**！auto-fill 在 single-child 情况下生成多空列、child 仍只占 1 列。`grid-column: 1 / -1` 让 only-child 撑满所有 grid 列，card 从 367px 扩到 1122px，actions-bar 从 349px 扩到 1104px

## 验证（2026-10-13）

### 端到端实测（headless Chrome via CDP，1756×2044 — 用户实际视口）

真项目 `20261003-001/sv_20261004_072937_572eb5`：

- **修复前**（用户截图）：card 367px / actions-bar 349px / 3 button 各 16-20px / 文字"导出最终视频"一字一行
- **v1**：3 button 各占一行（h 137px），仍空荡
- **v2**：3 button 单行（h 64px，scrollW=540），紧凑但 card 仍 367px
- **v3（最终版）**：
  - **card: 1122px**（v2 367 → 1122，扩大 3x）
  - **actions-bar 1: w=1104 h=64 n=3 scrollW=1104**（3 button + muted 单行，**无横向滚动**）
  - **actions-bar 2: w=1104 h=64 n=2 scrollW=1104**（2 button + input 单行，**无横向滚动**）
  - 完整一行：紫「💾 导出最终视频」+ 灰「📤 导出参数」+ 灰「📥 导入参数」+「导出后输出在 stage6/final_*.mp4」muted
- 截图 `sv_stage7_v3.png`：card 撑满 1fr，所有 button 横向一行整齐排列

### 回归验证

- pytest 1228 passed（88.98s）零回归
- node --check 3 JS 全过（short_video.js / router.js / pipeline.js）
- ruff：home.css 不被 ruff 处理（跳过）

### 端点不动

- 16 端点全不动（`/slirn/api/short_video_*`）
- 3 helper 全不动（`short_video_fine.py` / `short_video_bg_detect.py` / `short_video_ai_layout.py`）
- short_video.js 派发全不动
- 模板隔离逻辑全不动

## 闭环记录

| 项 | 值 |
|---|---|
| REQ 文档 | REQ-20261013-107-sv-stage7-actions-bar-wrap.md（本文件） |
| 改动文件 | `slirn_home/static/home.css`（v1 + v2 + v3 合并 +7 −2） |
| 7861 PID | bzcsgbsc1（v3 已 restart） |
| 端点 / helper / JS | 零改动 |
| 测试 | 1228 passed（88.98s）零回归 |
| 验证 | 真项目端到端，headless Chrome via CDP，截图确认 |
