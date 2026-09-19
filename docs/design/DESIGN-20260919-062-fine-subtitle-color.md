# DESIGN-20260919-062 精剪视频·字幕文字颜色可设置

## Context

REQ-20260919-062：让用户能设置字幕文字本身颜色（不只是描边/背景）。

## 现状

| 位置 | 现状 |
|---|---|
| [slirn_home/app.py:1492-1503](slirn_home/app.py#L1492) | `_FINE_FONT_DEFAULTS` 9 个字段，**无 color** |
| [slirn_home/app.py:1596-1624](slirn_home/app.py#L1596) | `_ass_force_style` 输出无 `PrimaryColour=...` |
| [slirn_home/app.py:1955](slirn_home/app.py#L1955) | `_get_fine_compose` 用 `setdefault("font", ...)`，但旧 fc.font 已有的话 setdefault 不会改 → 旧任务缺 color 字段 |
| [slirn_home/app.py:2445-2507](slirn_home/app.py#L2445) | `_render_fine_cut_zone` font HTML 块：无 color picker |
| [slirn_home/app.py:4601-4621](slirn_home/app.py#L4601) | `save_fine_font` 用 `_FINE_FONT_DEFAULTS.keys()` 作白名单 — 加 color 后自动放行 |
| [slirn_home/static/router.js](slirn_home/static/router.js) | `data-font-key="..."` 已覆盖所有 input 类型（含 color picker） |

## 数据流

1. 用户在 color picker 选 `#FFFF00`（黄）
2. JS 收集 `data-font-key="color"` 的 value → `font: {color: "#FFFF00", ...}`
3. `save_fine_font` 接收，`allowed = _FINE_FONT_DEFAULTS.keys()`，`fc["font"]["color"] = "#FFFF00"`
4. 下次渲染 → `_ass_force_style(fc["font"])` 输出 `PrimaryColour=&H0000FFFF`（BGR + alpha &H00）
5. libass 用黄色渲染字符 → 输出视频里字幕真的是黄字

## 实施

### 1. `slirn_home/app.py` — 加 `color` 字段（数据层）

**`_FINE_FONT_DEFAULTS`（1492-1503）**：
```python
_FINE_FONT_DEFAULTS = {
    "size":         36,
    "color":        "#FFFFFF",   # ← 新增：字幕文字本身颜色（libass PrimaryColour）
    "stroke_width": 2,
    "stroke_color": "#000000",
    "bg_enabled":   False,
    ...
}
```

**`_ass_force_style`（1596-1624）**：在 `parts = [f"FontName=..."]` 后插入：
```python
# REQ-20260919-062：颜色 = libass PrimaryColour
# #RRGGBB → &H00BBGGRR（ASS 用 BGR，alpha 在前 &H00 = 不透明）
tc = font.get("color", "#FFFFFF").lstrip("#")
if len(tc) == 6:
    tc_bgr = tc[4:6] + tc[2:4] + tc[0:2]
    parts.append(f"PrimaryColour=&H00{tc_bgr.upper()}")
```

放在 `FontSize` 之后、`Bold` 之前 —— 与 ASS spec 一致。

### 2. `slirn_home/app.py` — 迁移（`_get_fine_compose`）

旧任务 `fc.font.color` 缺失 → 补 `#FFFFFF`。位置：`_get_fine_compose` 末尾、`_schema` 迁移块**之前**：

```python
# REQ-20260919-062 v19：旧任务 fc.font 缺 color 字段 → 补 #FFFFFF
_f_color = fc.get("font", {})
if "color" not in _f_color:
    _f_color["color"] = "#FFFFFF"
```

放在 1980 行（数字字段类型规整）之前的合适位置。

### 3. `slirn_home/app.py` — UI 加 color picker

在 `_render_fine_cut_zone` font HTML 块（2445-2507）的「描边颜色」之前插入：

```html
<div class="slirn-fine-font-row">
  <span class="slirn-fine-font-label">文字颜色</span>
  <input type="color" class="slirn-fine-font-color" data-font-key="color"
         value="{font["color"]}">
</div>
```

CSS `.slirn-fine-font-color` 已存在（描边/背景颜色用），无需新增。

### 4. `slirn_home/app.py` — `save_fine_font` 白名单

白名单是 `set(_FINE_FONT_DEFAULTS.keys())`，加 color 后**自动放行**，无需改 save_fine_font。

但要防御：picker 返回 `#FFFFFF` 是合法值；前端可能会传 `"#fff"`（3 位）。**`save_fine_font` 里加一道正则校验**：

```python
import re
_HEX_OK = re.compile(r"^#[0-9A-Fa-f]{6}$")
for k, v in font.items():
    if k in allowed:
        if k in ("color", "stroke_color", "bg_color") and not _HEX_OK.match(str(v)):
            continue  # 丢弃非法 hex，保持原值
        fc["font"][k] = v
```

（之前 stroke_color / bg_color 也没校验，顺手补齐。）

### 5. `slirn_home/static/router.js`

不需要改：已有 `data-font-key` 委托 + `save_fine_font` 自动包含 color。

### 6. `tests/test_workbench.py` — 单元测试

| 测试 | 验证 |
|---|---|
| `test_ass_force_style_emits_primary_colour` | 给 color=`#FFFF00` → 输出含 `PrimaryColour=&H0000FFFF` |
| `test_ass_force_style_default_color_is_white` | 不设 color → 输出含 `PrimaryColour=&H00FFFFFF` |
| `test_ass_force_style_color_bgr_swap` | 测 R/G/B 三个分量都被正确 BGR 交换 |
| `test_get_fine_compose_migrates_missing_color_to_white` | 旧任务 fc.font 缺 color → 补 `#FFFFFF` |
| `test_render_fine_cut_zone_includes_color_picker` | HTML 含 `data-font-key="color"` + type="color" |
| `test_save_fine_font_persists_color` | 调 save_fine_font({color: "#FFFF00"}) → fine_compose.json 落盘 color 字段 |
| `test_save_fine_font_rejects_invalid_hex_color` | 传 `color: "red"` / `"#fff"` → 字段不变（保留原值） |

## 风险与边界

| 风险 | 处理 |
|---|---|
| 用户选了白色 → 还是看不见 | **预期行为**；用户在 PPT 上应该选深色（黑/红）。picker 默认白 |
| 旧任务迁移漏 color → 输出变白（默认行为） | 兼容；本来就是默认 |
| 前端 picker 返回 `#fff`（3 位简写） | save_fine_font 正则拒绝，保持原值 |
| 用户改 stroke_color / bg_color 后 UI 不刷 | 已通过 data-font-key 委托处理 |
| ASS PrimaryColour 颜色空间 | 测试覆盖 BGR 交换 |

## 不做的事

- ❌ 不预设智能反色（用户明确要 picker）
- ❌ 不动 bg_enabled / bg_color / stroke_color 任何已有字段
- ❌ 不改 `_FINE_LAYOUT_DEFAULTS`（颜色属 font）

## 文件清单

| 文件 | 改动 |
|---|---|
| [slirn_home/app.py:1492](slirn_home/app.py#L1492) | `_FINE_FONT_DEFAULTS` 加 `color` |
| [slirn_home/app.py:1596](slirn_home/app.py#L1596) | `_ass_force_style` 输出 `PrimaryColour` |
| [slirn_home/app.py:1955](slirn_home/app.py#L1955) | `_get_fine_compose` 迁移补 color |
| [slirn_home/app.py:2445](slirn_home/app.py#L2445) | `_render_fine_cut_zone` 加 color picker |
| [slirn_home/app.py:4601](slirn_home/app.py#L4601) | `save_fine_font` 加 hex 校验 |
| [tests/test_workbench.py](tests/test_workbench.py) | 加 7 个测试 |