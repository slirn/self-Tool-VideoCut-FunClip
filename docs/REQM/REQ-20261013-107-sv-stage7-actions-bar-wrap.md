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

**3 行 CSS 改动**，零 Python / JS / 后端改动：

| # | 文件 | 改动 | 原因 |
|---|------|------|------|
| 1 | `slirn_home/static/home.css:3934-3936` | `.slirn-fine-actions-bar` 的 `flex-wrap: nowrap; overflow-x: auto` → `flex-wrap: wrap; overflow-x: visible; row-gap: 6px` | 窄空间下 button **换行到下一行**而非文字竖排；长视频窄屏同样受益 |
| 2 | `slirn_home/static/home.css:162-163` | `.slirn-btn` 加 `white-space: nowrap; flex-shrink: 0` | button 内部文字保持一行，button 保持自身最小宽度不压缩 |

## 验证（2026-10-13）

### 端到端实测（headless Chrome via CDP，1280×800）

真项目 `20261003-001/sv_20261004_072937_572eb5`：

- **修复前**（推断）：actions-bar 内 3 个 button 各 16-20px 宽，文字"导出最终视频"一字一行
- **修复后**（实测）：
  - card 宽度 282px
  - actions-bar 1（264px × 182px 高，3 个 button 跨 3 行）：
    - `💾 导出最终视频` — 143px × 40px
    - `📤 导出参数` — 117px × 42px
    - `📥 导入参数` — 117px × 42px
  - actions-bar 2（264px × 155px 高，2 个 button 跨 2 行）：
    - `💾 保存参数为模板` — 157px × 40px
    - `📥 引用参数（本地）` — 173px × 42px
- 截图 `sv_stage7_v3.png`：紫色主按钮"导出最终视频"独占一行（不再竖排），其他辅助按钮换到下一行

### 回归验证

- pytest 1228 passed（80.74s）零回归
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
| 改动文件 | `slirn_home/static/home.css`（+5 −2） |
| 7861 PID | bzh1reutb（已 restart） |
| 端点 / helper / JS | 零改动 |
| 测试 | 1228 passed（80.74s）零回归 |
| 验证 | 真项目端到端，headless Chrome via CDP，截图确认 |
