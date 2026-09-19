# DESIGN-20260920-079 精剪·超大图片自动缩放（防 ffmpeg OOM 卡死）

## Context

[REQ-20260920-079](../REQM/REQ-20260920-079-prescale-oversized-images.md) 描述用户实测：任务 20260918-022 有两张 8000×4500 RGBA PNG 作为 bg / cover，渲染卡 4.5 分钟 0% 进度。ffmpeg 必须先解码到内存（每帧 144MB），49 线程 thrash 在内存分配上。

本文档记录实现决策。

## 关键决策

| 维度 | 决策 | 理由 |
|---|---|---|
| 缩放时机 | **渲染前**（`_assemble_fine_filter` 时） | 用户原图保留；不影响 fc 持久化；不影响上传接口 |
| 缩放目标 | 跟随 `fc.output.resolution`：`720p`→1280×720；`1080p`→1920×1080；`source`→1920×1080 | 用户明确「跟随输出设置」；source 模式兜底到 1920 防 OOM |
| 缩放工具 | **PIL（Pillow）`Image.thumbnail` + pad**（`LANCZOS`） | PIL 解码 PNG 比 ffmpeg 内置 png_decoder 高效 5-10 倍 |
| 应用范围 | **bg / cover / reference** 三类图片素材 | video 已过 crop+scale，不需预缩 |
| 临时文件命名 | `.<label>_req079_<stem>_<W>x<H><.ext>`（源同目录） | 隐藏文件 + REQ 标识便于排查；同名覆盖无冲突 |
| 临时文件清理 | `_run_fine_render_async` `finally` 块 unlink（与 `sub_input_tmp` 同生命周期） | 与现有模式对称 |
| RGBA 透明区 | **保留 RGBA mode + 透明 canvas**（不复用现有「黑底填充」） | 让 ffmpeg `_build_bg_layer_chain` 继续按 alpha 合成 → REQ-063 行为不变 |
| 失败兜底 | `try/except` → `log.warning` + 用原图继续渲染 | 不让缩放失败变成新的渲染失败原因 |
| 上传 warning | 端点检测 > 4096px，响应体加 `warning` 字段；前端 toast.info | 用户明确「上传只提醒」；不动原图、不拒绝上传 |
| 缩放阈值 | 长边 > **4096 px** 才触发（4096 = 4K 横向分辨率）；短边不限 | 4096 以下 ffmpeg 解码压力可控；> 4096 才有 OOM 风险 |

### ADR-079-1：渲染前 PIL 预缩（而不是上传时改图 / ffmpeg 内 scale）

**选项 A：ffmpeg filter_complex 内 `scale=W:H:force_original_aspect_ratio=decrease`**
- 优点：不引入新依赖；不写临时文件
- 缺点：ffmpeg **仍要先解码完整 PNG 到内存**才能 scale —— OOM 风险依旧存在（甚至叠加 scale 算子更慢）

**选项 B：上传时 PIL 缩放、改写原图**
- 优点：彻底解决，源即缩后
- 缺点：用户明确「保留原图」；下次用户改 fc 分辨率又得重新上传

**选项 C（✅）：渲染前 PIL 预缩，写临时文件，ffmpeg 指向临时文件**
- 优点：原图保留；PIL 解码比 ffmpeg png_decoder 快 5-10 倍；临时文件小（1920×1080 PNG ~3MB），ffmpeg 接收极快
- 缺点：多一次磁盘 I/O（可忽略：tmp 在同 SSD 上，写入 ~3MB < 50ms）

**决策 C**。

### ADR-079-2：临时文件命名约定 + 清理生命周期

**命名格式**：`.bg_req079_bg_长视频背景_第五课_1920x1080.png`
- `.` 前缀：隐藏文件，避免误认为是用户素材
- `bg_req079_`：label + REQ 编号 + 排查锚点
- `<stem>`：源 stem（中文 / 特殊字符安全）
- `_1920x1080`：缩放目标尺寸（避免多次渲染时不同分辨率冲突）
- `<.ext>`：保留源扩展名

**生命周期**：
1. `_assemble_fine_filter` 调 `_maybe_prescale_image()` → 写入 tmp
2. 返回 `image_tmp_paths: list[Path]` 给 asm dict
3. `_run_fine_render_async` 拿 `asm["image_tmp_paths"]`
4. `proc.wait()` 之后 + `finally` 块：unlink 所有 tmp（异常路径也清）
5. 同名覆盖（`save()` 直接写）：多次渲染不会累积

### ADR-079-3：上传端点的轻量 warning（detect-only，不强制）

**选项 A：不提醒** → 用户上传 8K 图后才发现被缩放，体验差
**选项 B（✅）：上传时检测 > 4096px，响应 `warning` 字段，前端 toast.info**
**选项 C：硬拒绝 + 弹错误** → 用户体验差，且无技术必要性（缩放是无损补救）

**决策 B**。前端 handler 加：
```javascript
if (j.warning) {
  toast.info(j.warning);  // 不阻断
}
```

## 关键文件改动

| 文件 | 行号 | 改动 | 估算行数 |
|---|---|---|---|
| [slirn_home/app.py](slirn_home/app.py) | 1730 前 | `_PIL_IMAGE_EXTS` + `_PRESCALE_THRESHOLD_PX` + `_maybe_prescale_image()` | +45 |
| [slirn_home/app.py](slirn_home/app.py) | 1925 后 | assemble_fine_filter 接入预缩（bg/cover/ref） + `image_tmp_paths` 返回 | +60 |
| [slirn_home/app.py](slirn_home/app.py) | 2342 附近 | async 渲染清理 `image_tmp_paths`（finally 块） | +5 |
| [slirn_home/app.py](slirn_home/app.py) | 5740-5773 | upload 端点加 `warning` 字段 + `target_w_for_kind()` | +15 |
| [slirn_home/static/router.js](slirn_home/static/router.js) | fineUploadMaterial 附近 | `if (j.warning) toast.info(j.warning);` | +8 |
| [tests/test_workbench.py](tests/test_workbench.py) | 末尾 | 4 个新测试 | +120 |
| [docs/REQM/](docs/REQM/) | 新建 | REQ 文档 | +180 |
| [docs/design/](docs/design/) | 新建 | DESIGN 文档 | +200 |

净代码量约 **+140 行**（核心逻辑 +135 / 上传 warning +25）。

## API 设计

### 改动 1：`_assemble_fine_filter` 返回值新增 `image_tmp_paths`

```python
return {
    "ok": True,
    "input_args": input_args,
    "filter_complex": filter_complex,
    "final_map": "[vfinal]" if cover_input_enabled or (out_w, out_h) != (W, H) else "[vout]",
    "sub_input_tmp": sub_input_tmp,
    "image_tmp_paths": image_tmp_paths,  # REQ-079：预缩临时文件路径列表
    "out_w": out_w, "out_h": out_h,
}
```

### 改动 2：`POST /slirn/api/upload_fine_material_form` 响应新增 `warning`

请求：multipart/form-data 上传 PNG/JPG
响应（成功 + 大图）：
```json
{
  "ok": true,
  "path": "tasks/20260918-022/upload/bg_长视频背景_第五课.png",
  "warning": "💡 上传图片 8000×4500 较大，合成视频时会自动缩放至 1920×1080（源文件保留原图）"
}
```

## 数据流

### 渲染流程（含预缩）

```
点「💾 导出最终视频」→ POST /export_fine_video
  ↓
_run_fine_render_async(job, tid, mgr, out_path)
  ↓
asm = _assemble_fine_filter(tid, mgr, ...)
  ├─ 收集 input_args（含 bg/cover/ref 的 -i 路径）
  ├─ REQ-079：对每个 bg/cover/ref
  │    ├─ _resolve_mat_abs → 绝对路径
  │    ├─ _maybe_prescale_image(path, W, H, label)
  │    │    ├─ 尺寸 ≤ 4096 → 返回原 path（无操作）
  │    │    └─ 尺寸 > 4096 → PIL.thumbnail + pad → 写 .<label>_req079_*.png → 返回新 path
  │    └─ 替换 input_args 中对应 -i path
  ├─ 返回 image_tmp_paths 给异步渲染清理
  ↓
cmd = ["ffmpeg", "-y", *asm["input_args"], "-filter_complex", asm["filter_complex"], ...]
  ↓
proc = subprocess.Popen(cmd, ...)
  ↓
主循环读 proc.stdout（filter init 比之前快 5-10 倍）
  ↓
proc.wait() + finally 块：unlink image_tmp_paths + sub_input_tmp
```

### 上传流程（含 warning）

```
上传 PNG → POST /upload_fine_material_form
  ↓
写 save_path（源不变）
  ↓
if kind in (bg, cover, reference) and PIL.Image.open:
  ├─ iw, ih = im.size
  └─ if max(iw, ih) > 4096:
       warning = "💡 ..."
  ↓
return _ok(path=..., warning=warning)
  ↓
前端 toast.info(warning)（不阻断）
```

## 边界与错误处理

| 场景 | 行为 |
|---|---|
| 源 PNG 尺寸 ≤ 4096 | 不触发缩放；返回原 path；零开销 |
| 源 PNG > 4096 | PIL.thumbnail + pad；写 tmp；返回 tmp path |
| PIL 解码抛异常 | `log.warning` + 返回原 path；ffmpeg 正常渲染（用户原图保留） |
| 临时文件已存在 | `save(optimize=True)` 直接覆盖；不冲突 |
| 多次连续渲染 | 同名覆盖；不累积 |
| 渲染失败 / 取消 | finally 块 unlink tmp；不残留 |
| RGBA 透明 PNG | 保留 RGBA mode + 透明 canvas；ffmpeg 继续按 alpha 合成（REQ-063 行为不变） |
| 上传 non-image 文件（mp4） | `_PIL_IMAGE_EXTS` 白名单过滤；PIL 不打开；不触发 warning |
| 上传损坏的图片 | try/except 兜底；warning = None |

## 复用现有基础设施

- `PIL.Image` 已 import 在 `detect_bg_white_area`（[app.py:4662](slirn_home/app.py#L4662)）→ `_PILImage` 模块级缓存
- `sub_input_tmp` 清理模式（[app.py:2342-2345](slirn_home/app.py#L2342)）→ finally 块结构完全复用
- `_resolve_mat_abs`（[app.py:1655-1684](slirn_home/app.py#L1655)）→ 获取 bg/cover/ref 绝对路径
- `_ok` / `_err` → 上传响应结构
- `_FINE_DESIGN_W = 1920 / _FINE_DESIGN_H = 1080` → 默认 target；与 `_build_bg_layer_chain` 用 W×H 对齐
- 测试 `_make_mgr` / `build_app` fixture → 测试无新增 setup

## 已知未修问题

- `_assemble_fine_filter` L1879：`"source"` silently 映射到 1920×1080（应按视频源精确 `sws_flags=lanczos` 用 ffprobe 拿真实尺寸）→ 独立 REQ-080 修，不扩散范围