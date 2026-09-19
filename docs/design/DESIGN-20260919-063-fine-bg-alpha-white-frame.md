# DESIGN-20260919-063 精剪视频·bg 图 RGBA alpha 透明区误显示白色

## Context

REQ-20260919-063：解决 bg 图 RGBA alpha=0 区域被误渲染成白色的问题。

## 现状（修复前）

```python
# 修复前的实现（_run_fine_render 内联 + _build_fine_filter 两处复制）
if bg_input_idx >= 0:
    chain.append(f"[{bg_input_idx}:v]scale={W}:{H},setsar=1[bg]")
else:
    chain.append(f"color=size={W}x{H}:color=black:rate=30[bg]")
cur = "[bg]"
```

**问题**：
1. bg 图透明像素（alpha=0） → ffmpeg 没有显式处理 alpha → 用 RGB 值渲染
2. 透明像素的 RGB=255,255,255 → 渲染成白色
3. 视频覆盖不到的地方 → 透出 bg 图白色透明区 = 用户看到的"白框"

## 修复方案

**核心思路**：在 bg 图下面垫一层黑底，让透明像素合理显示黑色；提取 helper 函数避免两处复制粘贴 bug。

```python
def _build_bg_layer_chain(bg_idx: int, W: int, H: int) -> list[str]:
    """REQ-20260919-063：bg 图透明区黑底 alpha 合成链。"""
    if bg_idx >= 0:
        return [
            f"color=size={W}x{H}:color=black:rate=30[bg_b]",
            f"[{bg_idx}:v]scale={W}:{H},setsar=1[bg_img]",
            "[bg_b][bg_img]overlay=eof_action=pass[bg]",
        ]
    return [f"color=size={W}x{H}:color=black:rate=30[bg]"]
```

**关键点**：
1. **黑底 `[bg_b]`**：先有黑色 canvas
2. **不强制 format**：PNG 解码默认保留 alpha（ffmpeg format filter 不支持 'auto' 值）
3. **`overlay=eof_action=pass`**：overlay 按 alpha 合成 + bg 比视频长时继续显示

## 为什么不用 `format=auto` 强制保留 alpha？

ffmpeg 的 format filter 不支持 'auto' 作为参数值（实测 `Parsed_format_3 ... Invalid pixel format 'auto'`）。正确做法：PNG 解码默认保留 alpha，overlay 滤镜会自动用 alpha 合成，无需 format filter。

## 为什么提取 helper 函数？

`_build_fine_filter` 之前有同款内联 bg chain（line 1683-1692），但实际渲染走的是 `_run_fine_render` 内联 chain（line 1810-1817）—— `_build_fine_filter` 没人调用。两处复制粘贴 bug 风险大，所以提取 `_build_bg_layer_chain` helper，两处统一调用，3 个单元测试直接打 helper。

## 实施

### 1. `slirn_home/app.py:_build_bg_layer_chain` (新函数，line 1640)

```python
def _build_bg_layer_chain(bg_idx: int, W: int, H: int) -> list[str]:
    if bg_idx >= 0:
        return [
            f"color=size={W}x{H}:color=black:rate=30[bg_b]",
            f"[{bg_idx}:v]scale={W}:{H},setsar=1[bg_img]",
            "[bg_b][bg_img]overlay=eof_action=pass[bg]",
        ]
    return [f"color=size={W}x{H}:color=black:rate=30[bg]"]
```

### 2. `slirn_home/app.py:_run_fine_render` (line ~1810)

bg 渲染分支改为：
```python
chain.extend(_build_bg_layer_chain(bg_idx, W, H))
```

### 3. `slirn_home/app.py:_build_fine_filter` (line ~1683)

同款替换（保持代码一致）。

### 4. `tests/test_workbench.py` — 新增 3 个测试

| 测试 | 验证 |
|---|---|
| `test_build_bg_layer_chain_with_bg_uses_black_canvas_under_image` | bg layer 的 filter chain 含 `color=...[bg_b]` + `[bg_img]` + `[bg_b][bg_img]overlay=eof_action=pass[bg]` 模式 |
| `test_build_bg_layer_chain_without_bg_uses_black_canvas_only` | 无 bg 图时仍是纯黑底（行为不变） |
| `test_build_bg_layer_chain_does_not_lose_alpha_for_rgba_bg` | chain 中不能含 `format=yuv420p` 或 `format=rgb24`（强制转 RGB 会丢 alpha） |

### 5. E2E 验证（已通过，重启 slirn 后）

- 用户任务 20260918-022 → 重新生成预览（mp4 mtime 更新到 1789825201.9）
- t=5.0s 取一帧 → x=960 y=1050 = RGB(1,1,1) 黑色（修复前是 RGB(255,253,255) 白色）
- t=5.0s → x=1700 y=1075 = RGB(15,10,7) 黑色（bg image 右下角，不受 alpha 影响）
- t=5.0s → x=960 y=900 = RGB(240,247,245) 白色（slide 内容白底，不受影响）

## 风险与边界

| 风险 | 处理 |
|---|---|
| 旧用户 bg 图是 RGB（无 alpha）| PNG 解码无 alpha → overlay 直接 RGB 合成 → 行为不变 |
| 视频比 bg 流短 | `eof_action=pass` 让 bg 继续显示（保持原行为） |
| 用户主动禁用 bg layer | 走 else 分支，纯黑底（行为不变） |
| format filter 在某些 ffmpeg 版本行为差异 | 不用 format filter，避免所有版本问题 |
| bg 图分辨率不一致 | scale=W:H 已处理 |

## 不做的事

- ❌ 不动用户的 bg 图（用户的素材不能改）
- ❌ 不动 cover 渲染
- ❌ 不动 video overlay 逻辑
- ❌ 不引入新的 ffmpeg 选项（去掉了 format=auto，改用 PNG 默认 alpha 行为）

## 文件清单

| 文件 | 改动 |
|---|---|
| [slirn_home/app.py:1640](slirn_home/app.py#L1640) | 新增 `_build_bg_layer_chain` helper |
| [slirn_home/app.py:~1810](slirn_home/app.py#L1810) | `_run_fine_render` 改用 helper |
| [slirn_home/app.py:~1683](slirn_home/app.py#L1683) | `_build_fine_filter` 改用 helper |
| [tests/test_workbench.py](tests/test_workbench.py) | 3 个新测试（`_build_bg_layer_chain_*`） |