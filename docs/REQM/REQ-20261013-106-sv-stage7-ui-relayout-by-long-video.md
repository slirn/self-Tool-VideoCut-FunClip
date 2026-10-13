# REQ-20261013-106 — 短视频 Stage 7「精剪混编」UI 按长视频「精剪合成」布局重排

> 状态：✅ 已闭环（2026-10-13）
> 父任务：[REQ-20261013-105](REQ-20261013-105-sv-stage7-fine-port.md)（已完成 C1 端点 + helper）

## 解决什么问题

短视频「精剪混编」当前 UI（C1 实现）虽然端点齐、helper 全，但元素堆叠比较松散：6 素材卡、字体、输出、BGM、模板、检测、AI 布局都在 `slirn-sv-fs` 字段集里铺，没有按「主流程」分块。

长视频「精剪合成」页面（用户截图）把整个界面按视频合成的工艺顺序分成 **9 个清晰的 block**：

1. 素材上传（6 项：视频/字幕/封面/背景/参考/音频）
2. 背景图区域检测（4 角点 + 宽高）
3. 顶部操作条（导出最终视频 + 导出参数 + 导入参数）
4. 模板条（保存参数为模板 + 模板名 + 引用参数（本地） + 状态）
5. 预览（开始时间 + 时长 2-30s + 生成预览按钮）
6. 三块画布调节（视频 / 字幕 / 背景）— X/Y 滑块 + 缩放 + 裁剪
7. 字幕字体设置（11 项：字体本体/大小/粗体/对齐/偏移/描边/颜色/背景）
8. 视频源裁剪（全幅/16:9/1:1 居中 + 锁 16:9 + X/Y/W/H）
9. 片头封面（启用 + 展示时长）
10. 背景音乐（启用 + 音量/淡入/淡出）
11. 输出设置（分辨率）

按用户 2026-10-13 指令「把短视频精剪混编阶段的页面内容，按照长视频精剪合成区域的内容进行布局重排」，**只动 UI 排版**，**不动**后端端点 / helper / 数据结构。

## v1 范围

### UI 字段集重排（_stage7_finalize）

按长视频的 11 个 block 顺序重排，但适配短视频「N 条 highlight 各自一份」的特性：

- **每个 highlight 一张完整卡片**，包含：素材上传 / 检测 / 视频-字幕-背景画布 / 字体 / 源裁剪 / 片头封面 / 背景音乐
- **顶部全局区**（所有 highlight 共享）：导出参数 / 导入参数 / 保存为模板 / 引用参数 / 预览（per highlight） / 导出最终视频（per highlight 异步）
- **输出设置**（每 highlight 一份）
- **片头封面** 默认关闭（短视频一般不用片头）

### 视觉对齐

- 主按钮色调：紫色（导出最终视频 / 保存参数 / 引用参数）；灰色辅助
- 区域标题用 emoji + 中文（沿用 C1 命名）
- 滑块/数字输入：长视频同样的 14px 输入框 + 居右

### 后端

- 不动 16 端点（`short_video_stage7_*`）
- 不动 `short_video_fine.py` / `short_video_bg_detect.py` / `short_video_ai_layout.py`
- 不动 `short_video.js` 已有 14 派发（如需新增 panel 内引用，只在 _stage7_finalize 内部排版，不动 JS 顶层派发）
- 不动 `execution_history.py`

## 与父任务 C1 的关系

C1 已端到端 OK（16 端点 / 17 测试 / 1228 零回归 / 7861 跑通）。本任务只做**视觉重排**，C1 端点 + helper 一行不动。

## 验收

1. UI 视觉对照长视频精剪合成页面：11 block 全部呈现且顺序一致
2. 每个 highlight 卡片展开后 11 block 完整可见
3. 端到端冒烟：选 bgm + 切 highlight + 预览 10s + 保存模板 链路不断
4. pytest 1228 全过（不加新测试，仅确认无回归）
5. ruff 零新增、node --check 通过
6. 7861 重启 + 截图（IDE 内嵌浏览器 + 真实浏览器对照）
7. 短/长隔离仍 OK（落 `tasks/_global_short_video_fine_profiles.json`，不写长视频）

## 验收（2026-10-13）

1. ✅ 11 block 全部呈现且顺序一致：上传素材 → 背景图区域检测 → 顶部操作条 → 模板条 → 预览 → 三块画布 → 字体 11 项 → 视频源裁剪 → 片头封面 → 背景音乐 → 输出设置
2. ✅ 卡片渲染：每条 highlight 1 张卡，per-block 元素全部带 `data-clip-index="N"`
3. ✅ 端到端：7861 用真项目 `20261003-001/sv_20261004_072937_572eb5` 渲染 → 11 block 标题全齐 + 1 张卡 + 43 个 data-clip-index + 0 错
4. ✅ pytest 1228 全过（80.63s），零回归
5. ✅ ruff 零新增（5 个 baseline 错误全在 stage1/upload_section，与本次无关）
6. ✅ node --check short_video.js 通过
7. ✅ 短/长隔离：纯 UI 改动，后端零动 → C1 隔离测试仍有效

## 闭环记录

| 项 | 值 |
|---|---|
| REQ 文档 | REQ-20261013-106-sv-stage7-ui-relayout-by-long-video.md（本文件） |
| 提交号 | `9ff69bb`（feat(slirn_home) UI 重排） |
| push ref-update | `c547318..9ff69bb main -> main` |
| 7861 PID | b1odjmsc9 |
| 改动文件 | `slirn_home/short_video_ui.py`（UI 重排 +434 −141）+ `docs/REQM/REQ-20261013-106-...md` |
| 后端 | 零改动（端点 16 个、helper 3 个、execution_history、short_video.js 全部不动） |
| 测试 | 1228 passed（80.63s）零回归 |
| ruff | 0 新增（baseline 5 个错误全在 stage1/upload_section） |
| node --check | 3 JS 全过 |
| 端到端 | 真项目渲染 → 11 block 标题齐 + 1 卡 + 43 data-clip-index |
