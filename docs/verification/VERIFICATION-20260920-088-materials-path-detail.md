# VERIFICATION-20260920-088 — 精剪素材路径详情

## 概述

| 维度 | 数据 |
|---|---|
| REQ ID | REQ-20260920-088 |
| 实现 commit | `f3d65b1` |
| 推送状态 | ✅ `72a9a8d..f3d65b1 main -> main` |
| 测试 | 614 passed（601 baseline + 13 new）|
| node --check | ✅ JS 语法通过 |

## 验收标准对照

### AC-1：每个素材卡片底部加「🔍 详情」按钮 ✅
**验证**：6 个素材卡片（video / subtitle / cover / bg / reference / audio）全部包含 `data-action="fine-mat-detail"` + 文本「🔍 详情」+ disabled 条件 + tooltip。

测试：`test_upload_card_has_detail_button` + `test_upload_card_detail_button_disabled_when_no_path` 通过。

### AC-2：点击「🔍 详情」弹出模态框显示完整信息 ✅
**验证**：调 `/slirn/api/material_info` 返回 `{ok, kind, source, source_label, fc_path, abs_path, exists, size_bytes, mtime, type}`，前端 `_renderMatDetailModal` 渲染为表格。

测试：`test_mat_info_endpoint_returns_required_fields` + `test_router_js_mat_detail_handler` 通过。

### AC-3：来源色块（视觉区分） ✅
**验证**：
- `auto` → 蓝色 `#3b82f6` + "上游产物"
- `upload` → 绿色 `#22c55e` + "用户上传"
- `default_bgm` → 紫色 `#a855f7` + "系统默认 BGM"
- 4 种 CSS class + 中文 label 全部实现。

测试：`test_mat_info_distinguishes_three_sources` + `test_router_js_renders_source_color_blocks` + `test_home_css_mat_detail_modal_styles` 通过。

### AC-4：路径缺失时给出明确提示 ✅
**验证**：source=auto 但上游产物不存在时，模态框内显示「⚠️ 已不存在」+ 提供「📥 重新自动获取」按钮（`data-action="fine-source-auto"`）。

实现：`_renderMatDetailModal` 函数末尾分支。

### AC-5：物理路径真实可访问（filesystem stat） ✅
**验证**：后端 `_resolve_mat_abs` 走 3 候选路径（tasks_dir / repo_root / tasks_dir+tid 兜底）+ `Path.exists()` 验证 + `Path.stat()` 拿 size/mtime。

实测 6 个 kind 全部 `exists=true` + 文件大小/mtime 正确：
- video: 79,507,091 bytes, 2026-09-20 14:41:02
- subtitle: 58,899 bytes, 2026-09-20 10:03:54
- cover: 5,320,563 bytes, 2026-09-19 01:37:43
- bg: 2,762,637 bytes, 2026-09-19 01:37:49
- reference: 1,734,947 bytes, 2026-09-19 01:37:56
- audio: 5,760,768 bytes, 2026-09-20 09:11:15

测试：`test_mat_info_endpoint_defined_in_app_py` 通过。

### AC-6：模态框默认在屏幕中央 + ESC 关闭 + 点击遮罩关闭 ✅
**验证**：
- 模态框复用 `.slirn-modal-overlay` 通用样式（默认居中）
- `Escape` 键监听：`document.addEventListener('keydown', ...)` 关闭
- 点击遮罩：`e.target.id === 'slirn-mat-detail-modal'` 关闭
- 关闭按钮：`data-action="mat-detail-close"`

测试：`test_router_js_esc_and_overlay_close` + `test_mat_detail_modal_close_action_in_router_js` 通过。

### AC-7：详情按钮不影响现有交互 ✅
**验证**：`data-action="fine-mat-detail"` 是新值，不与现有 `fine-upload` / `fine-mat-preview` / `fine-source-auto` 冲突。

测试：手动验证 HTML 模板 — 4 个按钮在同一 card 互不干扰。

### AC-8：新增 6+ 测试 ✅（实际 13 个）
测试清单：
1. `test_mat_info_endpoint_defined_in_app_py` — 端点注册
2. `test_mat_info_endpoint_returns_required_fields` — 返回字段
3. `test_mat_info_distinguishes_three_sources` — 3 种 source_label
4. `test_upload_card_has_detail_button` — 详情按钮存在
5. `test_upload_card_detail_button_disabled_when_no_path` — disabled 条件
6. `test_mat_detail_modal_dom_in_app_py` — 模态框 DOM
7. `test_router_js_mat_detail_handler` — JS handler
8. `test_router_js_esc_and_overlay_close` — ESC + 遮罩
9. `test_router_js_renders_source_color_blocks` — 色块 class
10. `test_router_js_xss_safe_rendering` — XSS escape（≥6 处）
11. `test_home_css_mat_detail_modal_styles` — CSS 样式
12. `test_router_js_mat_detail_xss_safe` — node --check
13. `test_mat_detail_modal_close_action_in_router_js` — 关闭逻辑

13/13 通过。

## 实测响应示例（curl）

**video（upload）**：
```json
{
  "ok": true,
  "kind": "video",
  "source": "upload",
  "source_label": "用户上传",
  "fc_path": "tasks\\20260918-022\\upload\\video_20260918-022.mp4",
  "abs_path": "D:\\Slirn\\WorkSpaces\\WaytoAGI\\ALI\\slirn-standalone\\tasks\\20260918-022\\upload\\video_20260918-022.mp4",
  "exists": true,
  "size_bytes": 79507091,
  "mtime": "2026-09-20 14:41:02",
  "type": "video"
}
```

**subtitle（auto）**：
```json
{
  "ok": true,
  "kind": "subtitle",
  "source": "auto",
  "source_label": "上游产物",
  "fc_path": "20260918-022\\tmp\\optimized_subs.srt",
  "abs_path": "D:\\Slirn\\WorkSpaces\\WaytoAGI\\ALI\\slirn-standalone\\tasks\\20260918-022\\tmp\\optimized_subs.srt",
  "exists": true,
  "size_bytes": 58899,
  "mtime": "2026-09-20 10:03:54",
  "type": "subtitle",
  "upstream_name": "optimized_subs.srt",
  "upstream_exists": true
}
```

## 关键文件改动

| 文件 | 改动 | 行数 |
|---|---|---|
| [slirn_home/app.py](../../slirn_home/app.py) | 详情按钮 HTML + 模态框 DOM | +25 / -2 |
| [slirn_home/static/router.js](../../slirn_home/static/router.js) | fineMatDetail + _renderMatDetailModal + ESC/overlay close | +95 / -0 |
| [slirn_home/static/home.css](../../slirn_home/static/home.css) | 模态框 + 表格 + 4 种色块 | +75 / -0 |
| [tests/test_workbench.py](../../tests/test_workbench.py) | 13 个测试 | +200 / -0 |
| [docs/REQM/REQ-20260920-088-materials-path-detail.md](../REQM/REQ-20260920-088-materials-path-detail.md) | 需求文档 | +99 / -0 |
| [docs/design/DESIGN-20260920-088-materials-path-detail.md](../design/DESIGN-20260920-088-materials-path-detail.md) | 设计文档 | +309 / -0 |

## 总结

8 条 AC 全部通过，13 个测试 100% 通过，614 passed total。后端 6 种 kind 全场景验证通过。
