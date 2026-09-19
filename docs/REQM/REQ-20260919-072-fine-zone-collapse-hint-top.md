# REQ-20260919-072 精剪面板·说明上移 + 双区域可折叠

## Context

精剪视频面板当前结构：

1. **upload_html**（红色框 1）— 6 个素材上传卡片（粗剪视频/字幕/封面/背景/参考图/背景音乐）
2. **slirn-form-hint**「📐 位置坐标（X/Y）和缩放…」（蓝色说明）
3. **bg_detect_block**（红色框 2）— 背景图区域检测
4. combined_actions_bar（操作栏）
5. slirn-form-hint「💡 上传 5 个素材后…」
6. preview_box / fine_cols_html / import_modal

用户反馈：

1. 蓝色「说明」不在醒目位置，应挪到面板**最顶端**
2. 两个红色框（素材上传 + 背景图区域检测）占垂直空间大，应可折叠，默认折叠
3. （追加）人员统计条的「跳过已删除」checkbox 长得像细长 pill（磕碜）

## 验收标准

- AC-1：蓝色「📐 位置坐标…」hint 出现在面板最顶部（在「精剪视频·素材合成器」标题之后、所有上传卡片之前）
- AC-2：「精剪视频·素材合成器」区域（含 6 个上传卡片）包在 `<details>` 内，默认**关闭**
- AC-3：「🎨 背景图区域检测（4 角点 + 宽高）」区域包在 `<details>` 内，默认**关闭**
- AC-4：summary 标题保留原色块（红/橙）风格 + 添加 ▸/▾ 折叠图标
- AC-5：summary 右侧带状态徽章（已上传 N/6 / 已检测 WxH）
- AC-6：「跳过已删除」checkbox 是 16×16 方框（不是被 72px 输入框宽度拉长的 pill）

## 方案

### 1. 说明上移（[slirn_home/app.py:2955-2974](slirn_home/app.py#L2955)）

把 `📐 位置坐标…` hint 从 upload_html 后挪到 pane-title 后、第一个 `<details>` 之前。

### 2. 双区域包 `<details>`（[slirn_home/app.py:2960-2969](slirn_home/app.py#L2960)）

```python
f'<details class="slirn-fine-section" id="slirn-fine-uploads-details">'
f'<summary class="slirn-fine-section-summary">📁 上传素材（6 项）{_upload_status_badge}</summary>'
f'{upload_html}'
f'</details>'
f'<details class="slirn-fine-section" id="slirn-fine-bg-detect-details">'
f'<summary class="slirn-fine-section-summary">🎨 背景图区域检测（4 角点 + 宽高）{_bg_status_badge}</summary>'
f'{bg_detect_block}'
f'</details>'
```

状态徽章：

| 区域 | 状态条件 | 徽章 |
|---|---|---|
| 上传 | 0/6 | ⏳ 0/6 已上传 |
| 上传 | 1-5/6 | ⚠️ N/6 已上传 |
| 上传 | 6/6 | ✅ 6/6 已上传 |
| 背景检测 | 未上传 bg | ⏳ 未上传背景图 |
| 背景检测 | 已上传未检测 | ⚠️ 未检测 |
| 背景检测 | 已检测 | ✅ 已检测 W×H |

### 3. CSS（[slirn_home/static/home.css:2379](slirn_home/static/home.css#L2379)）

```css
.slirn-fine-section { margin: 10px 0; }
.slirn-fine-section > summary.slirn-fine-section-summary {
  display: flex; align-items: center; gap: 8px;
  padding: 10px 14px; cursor: pointer; list-style: none;
  font-weight: 600; user-select: none;
  border-radius: 6px;
  background: rgba(128,128,128,0.04);
  border: 1px solid var(--border, rgba(128,128,128,0.18));
  font-size: 13.5px; color: var(--text-primary);
  transition: background-color 0.12s;
}
.slirn-fine-section > summary.slirn-fine-section-summary::-webkit-details-marker { display: none; }
.slirn-fine-section > summary.slirn-fine-section-summary::before {
  content: '▸'; font-size: 11px; color: var(--text-muted);
  transition: transform 0.15s; display: inline-block; width: 12px;
}
.slirn-fine-section[open] > summary.slirn-fine-section-summary::before { transform: rotate(90deg); }
.slirn-fine-section-status {
  font-size: 11.5px; font-weight: 400;
  color: var(--text-muted); margin-left: auto;
}
#slirn-fine-uploads-details > summary { border-left: 3px solid #3178c6; }
#slirn-fine-bg-detect-details > summary { border-left: 3px solid #f59e0b; }
```

### 4. checkbox pill bug 修复（[slirn_home/static/home.css:1665-1710](slirn_home/static/home.css#L1665)）

`.slirn-cut-spk-find input` 与 `.slirn-rev-spk-find input` 把所有 `input` 子元素
（包括 checkbox）都设了 `width: 72px`，checkbox 被拉成长条 pill。

修复：把两条规则改为 `:not([type="checkbox"])`，让全局 `input[type=checkbox]`
（16×16）的样式生效。

```diff
- .slirn-cut-spk-find input {
+ .slirn-cut-spk-find input:not([type="checkbox"]) {
    width: 72px; padding: 2px 8px; border-radius: 6px;
    border: 1px solid var(--border); font-size: 12px;
  }
- .slirn-rev-spk-find input {
+ .slirn-rev-spk-find input:not([type="checkbox"]) {
    width: 72px; padding: 2px 8px; border-radius: 6px;
    border: 1px solid var(--border); font-size: 12px;
  }
```

注：原代码 1669 行注释已写「不套 72px 输入框宽度（REQ-20260918-041 起由全局复选框规则接管尺寸）」，但 CSS 选择器漏写 `:not` — 这次正好补上。

### 5. 文件清单

| 文件 | 改动 |
|---|---|
| [slirn_home/app.py:2847-2974](slirn_home/app.py#L2847) | bg_detect 状态徽章；hint 上移；2 个 `<details>` 包裹 |
| [slirn_home/static/home.css:2379](slirn_home/static/home.css#L2379) | 新增 `.slirn-fine-section` + summary 样式 |
| [slirn_home/static/home.css:1665-1710](slirn_home/static/home.css#L1665) | checkbox pill bug 修复（2 处 `:not`） |
| [tests/test_workbench.py](tests/test_workbench.py) | 4 个新测试 |

## 测试

| 测试 | 验证 |
|---|---|
| `test_render_fine_cut_zone_hint_is_at_top` | hint 在 title 后、uploads 前（AC-1） |
| `test_render_fine_cut_zone_uploads_wrapped_in_details` | upload_html 被 details 包裹，无 open（AC-2） |
| `test_render_fine_cut_zone_bg_detect_wrapped_in_details` | bg_detect 被 details 包裹，无 open（AC-3） |
| `test_css_spk_find_input_rule_excludes_checkbox` | `:not([type=checkbox])` 限定（AC-6） |

```
pytest tests/ -q     # 523 通过（519 + 4 新）
```

## 不做的事

- ❌ 不自动展开（用户明确说"默认折叠"）
- ❌ 不持久化折叠状态（刷新页面重置为折叠）
- ❌ 不改 summary 内色条（保留原 #3178c6 蓝 + #f59e0b 橙，仅加折叠图标）
- ❌ 不改 checkbox 的全局打勾样式（沿用 db5cee9 自绘 ::after 模式）

## 真机验证

1. 重启 slirn → 进任意任务的精剪视频面板
2. 顶部第一行：标题 + 「📐 位置坐标…」说明（蓝色）
3. 红色框 1：「📁 上传素材（6 项）▸ ⏳ 0/6 已上传」默认折叠
4. 红色框 2：「🎨 背景图区域检测（4 角点 + 宽高）▸ ⏳ 未上传背景图」默认折叠
5. 点开红色框 2 → 检测后徽章变为「✅ 已检测 1586×995」
6. 进字幕修订 / 切分修剪阶段 → 「跳过已删除」checkbox 现在是 16×16 方框 ✓

