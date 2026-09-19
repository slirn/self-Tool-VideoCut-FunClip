# REQ-20260919-061 — 精剪视频·四素材合成器（位置/缩放/字幕字体）

## 用户原话

> 精剪视频阶段，是把粗剪视频、字幕文件、封面图片、背景图片这四个素材合并到一个视频当中。所以在这里需要上传视频文件、字幕文件、封面图片、背景图片的上传功能。另外还要上传一个参考位置关系图，然后让大模型按照这个关系图摆放前面四个素材的相对位置，然后把每个素材的位置信息反馈到页面中，并可以由用户进行修改，调整相对位置。这里不仅涉及到位置，还涉及到了缩放。对于字幕，还涉及到字体相关的设置，你可以收集剪映相关的可以对字体进行的设置的一些参数。例如加边框、加背影背景。你先挑3~5项加进来，然后我根据需要再调整

## 现状

[slirn_home/app.py:1368](slirn_home/app.py#L1368) `fine_cut` stage 在 wb 中**目前无渲染函数**，仅在 stage 列表里占位（走 `_pane_planned(i, desc)` 返回"待实现"）。

上游 FunClip 有基础视频拼接，但**没有图层叠加/位置/缩放/字幕字体样式**。

## 核心思路

精剪视频 = 在粗剪视频流上 **叠加 3 个图层 + 内嵌字幕**：

```
┌──────────────────────────┐ ← 输出视频（1080p 默认）
│  ┌──── 封面（可关）────┐ │
│  │   x,y,w,h,scale     │ │
│  └─────────────────────┘ │
│  ┌──── 背景（可选底）──┐ │
│  │   x,y,w,h,scale     │ │
│  └─────────────────────┘ │
│  ██████ 粗剪视频 ██████  │  ← 主视频（必填，通常占满）
│  ┌──── 字幕文字 ────────┐ │  ← SRT 内嵌 + 字体样式
│  │ 36px 思源黑体 描边   │ │
│  └─────────────────────┘ │
└──────────────────────────┘
```

## 5 个素材（4 必备 + 1 参考）

| 素材 | 类型 | 必填 | 用途 |
|---|---|---|---|
| 粗剪视频 | mp4 | ✅ | 主视频流 |
| 字幕文件 | srt | ✅ | 内嵌到视频底部（或自定义位置） |
| 封面图片 | png/jpg | ⚪ 可选 | 叠加图层（可关闭） |
| 背景图片 | png/jpg | ⚪ 可选 | 当视频比例与输出不一致时填充背景 |
| 参考位置关系图 | png/jpg | ⚪ 可选 | 大模型解读 → 输出 4 素材坐标 |

## 关键决策（已与用户确认）

| 决策 | 选择 | 备注 |
|---|---|---|
| 预览机制 | **按需生成预览图**（推荐） | 调整数值不渲染，点「🎬 生成预览」按钮才调 ffmpeg 渲染当前帧 |
| 参考图解析 | **调用系统已配置的多模态模型**（Qwen-VL） | 用户原话："调用在系统中已经配置的模型，所以这个模型要求具有多模态功能" |
| 字体设置 5 项 | 字体大小 + 描边 + 背景框 + 字体粗细/对齐 + 字体本身 | 剪映常见设置；后续可按需增加阴影/行间距等 |
| 视频覆盖范围 | **不铺满画布**（占左侧 70%） | 用户原话："视频并不是把完整的区域都展现出来，而是要展现主要的授课内容区，而右侧的人员区是与背景图片重复的，需要被遮挡住" |
| 视频源裁剪 | **从源画面里再截一个矩形**（默认 16:9，可自定义位置/大小） | 用户原话："会从视频整个画面中截取其中的一个矩形范围…这个截取的矩形也要有对应的位置和大小信息"。预设：全幅 / 16:9 居中 / 1:1 居中 |
| video/subtitle 来源 | **两个按钮常驻可用**（手动上传 / 自动获取上游），最后一次操作决定 source | 用户原话："可以选择上传文件，也可以默认从粗剪合成和优化字幕结果获取"。不再禁用对方按钮，无需手动切换 |

## 验收标准

### 数据结构
| # | 验收点 | 验证方式 |
|---|---|---|
| 1 | 5 个素材的上传 UI（粗剪视频/SRT/封面/背景/参考图） | E2E |
| 2 | 每个素材独立存储路径（task.json 或 wb 文件） | 评审 |
| 3 | 4 个素材（视频/字幕/封面/背景）每个有位置 `x,y` + 缩放 `scale` + 开关 `enabled` | E2E |
| 4 | 位置用**归一化坐标** 0-1（如 `x=0.1` = 10% 宽），跨分辨率自适应 | 评审 |
| 5 | 参考图 + 「🤖 AI 智能布局」按钮 → 调多模态模型 → 自动填充 4 素材位置/缩放 | E2E |
| 6 | 大模型返回 JSON 格式：`{video:{x,y,scale,enabled}, subtitle:{...}, cover:{...}, bg:{...}}` | 评审 |

### 位置/缩放编辑
| # | 验收点 | 验证方式 |
|---|---|---|
| 7 | 每个素材有 3 个控件：X 滑块（0-1）/ Y 滑块（0-1）/ 缩放滑块（0.1-2.0） | E2E |
| 8 | 滑块变化实时更新数值显示，但不触发渲染（按需预览） | E2E |
| 9 | 每个素材有"启用"复选框（封面/背景默认开，视频/字幕默认开） | E2E |
| 10 | 数值修改自动持久化（与现有 wb 持久化模式一致） | E2E |

### 字体设置（5 项）
| # | 验收点 | 验证方式 |
|---|---|---|
| 11 | 字体大小（px，范围 12-96，默认 36） | E2E |
| 12 | 描边（宽度 px + 颜色，默认 0px/无描边） | E2E |
| 13 | 背景框（开关 + 颜色 + 透明度 + 圆角 px，默认关） | E2E |
| 14 | 字体粗细（开关，对应 bold）+ 屏幕对齐（左/中/右，默认中） | E2E |
| 15 | 字体本身（系统字体下拉，默认思源黑体） | E2E |

### 视频源裁剪
| # | 验收点 | 验证方式 |
|---|---|---|
| 25 | 视频有 4 个额外裁剪字段：crop_x / crop_y / crop_w / crop_h（归一化 0-1） | 评审 |
| 26 | 3 个预设按钮：📐 全幅 / 🎯 16:9 居中 / ⬛ 1:1 居中，点一下应用 | E2E |
| 27 | crop_w / crop_h 变化时实时显示当前矩形比例（h/w） | E2E |
| 28 | 裁剪字段持久化（与位置/缩放同一保存流程） | E2E |

### video/subtitle 自动获取上游
| # | 验收点 | 验证方式 |
|---|---|---|
| 29 | video 卡有「📥 自动获取（rough_compose.mp4）」按钮（仅上游存在时显示） | E2E |
| 30 | subtitle 卡有「📥 自动获取（optimized_subs.srt）」按钮 | E2E |
| 31 | 点自动获取 → 调 `/slirn/api/auto_pick_upstream_material` → 写 `materials.{kind}.source="auto"` | E2E |
| 32 | 上游不存在时返回友好错误（提示用户先完成上游阶段） | E2E |
| 33 | 自动获取后，「📤 上传文件」按钮仍然可用（点上传会切换 source=upload） | E2E |
| 34 | 上传文件后，「📥 自动获取」按钮仍然可用（点获取会切换 source=auto） | E2E |
| 35 | 当前来源以徽章显示在卡片标题旁：「📥 自动获取」或「📤 手动上传」 | E2E |

### 预览与导出
| # | 验收点 | 验证方式 |
|---|---|---|
| 16 | 「🎬 生成预览」按钮 → 调 ffmpeg overlay 渲染当前前 3 秒 → 显示在面板 | E2E |
| 17 | 「💾 导出最终视频」按钮 → 渲染完整粗剪视频时长 + 字幕 → 写到 `output/` | E2E |
| 18 | 字幕字体样式（5 项）正确应用到 ffmpeg drawtext 滤镜 | E2E |
| 19 | 参考图解析失败时降级：弹 toast「AI 解析失败，请手动调整」+ 保留上次数值 | E2E |
| 20 | 调整预览参数 → 不自动渲染（按需机制）— 验证 CPU 空闲时 ffmpeg 不被调用 | E2E |

### 持久化与重渲
| # | 验收点 | 验证方式 |
|---|---|---|
| 21 | 刷新 wb 后所有素材位置/缩放/字体设置完整恢复 | E2E |
| 22 | 切到其他任务再回来，数值仍正确 | E2E |
| 23 | 跨刷新（关浏览器重开）后位置/字体仍正确 | E2E |
| 24 | wb 重渲不影响 fine_cut 数值（与其他 pane 数据正交） | 评审 |

## 数据结构（在 task.json 中新增 `fine_compose` 字段）

```json
{
  "fine_compose": {
    "materials": {
      "video": {"path": "tasks/20260918-022/cut/rough.mp4", "type": "video"},
      "subtitle": {"path": "tasks/20260918-022/subtitle/optimized.srt", "type": "srt"},
      "cover": {"path": "tasks/20260918-022/upload/cover.png", "type": "image"},
      "bg": {"path": "tasks/20260918-022/upload/bg.png", "type": "image"},
      "reference": {"path": "tasks/20260918-022/upload/reference.png", "type": "image"}
    },
    "layout": {
      "video":    {"x": 0.0,  "y": 0.0,  "scale": 1.0, "enabled": true},
      "cover":    {"x": 0.05, "y": 0.05, "scale": 0.3, "enabled": true},
      "bg":       {"x": 0.0,  "y": 0.0,  "scale": 1.0, "enabled": false},
      "subtitle": {"x": 0.5,  "y": 0.9,  "scale": 1.0, "enabled": true}
    },
    "font": {
      "size": 36,
      "stroke_width": 2,
      "stroke_color": "#000000",
      "bg_enabled": false,
      "bg_color": "#000000",
      "bg_opacity": 0.6,
      "bg_radius": 4,
      "bold": true,
      "align": "center",
      "family": "Noto Sans CJK SC"
    }
  }
}
```

## 架构

### 新增上传 endpoint
- `POST /slirn/api/upload_fine_material_form` — multipart/form-data（form 字段：task_id/kind/file）
- 文件存到 `tasks/{tid}/upload/{kind}_{filename}`
- 存路径到 `task.json:fine_compose.materials.{key}.path` + `source="upload"`

### 新增自动获取 endpoint
- `POST /slirn/api/auto_pick_upstream_material` — JSON（task_id/kind）
- video → `tasks/{tid}/outputs/rough_compose.mp4`（compose_service.rough_compose_path）
- subtitle → `optimize_service.build_srt(segments)` → `tasks/{tid}/tmp/optimized_subs.srt`
- 上游不存在时返回 `_err("上游无粗剪合成产物…")`，前端 toast 提示
- 写 `materials.{kind}.source="auto"`

### 大模型解析 endpoint
- `POST /slirn/api/parse_reference_layout`
- 入参：参考图路径
- 调用：dashscope 多模态模型（Qwen-VL-Plus/Max）
- Prompt：要求返回 JSON `{video, subtitle, cover, bg}` 每项 `{x, y, scale, enabled}`
- 输出：直接写回 `task.json:fine_compose.layout`

### 预览/导出 endpoint
- `POST /slirn/api/render_fine_preview` — 渲染前 3 秒，返回 mp4 base64 或临时 URL
- `POST /slirn/api/export_fine_video` — 渲染完整视频，存到 `output/`

### wb 渲染
- 新增 `_render_fine_cut_zone(task_id, t, mgr)` 函数
- 在 `_render_workbench` 的 `panes` 字典里注册 `fine_cut` key

### 前端交互
- 在 `slirn_home/static/home.css` 加 fine_cut pane 样式
- 在 `slirn_home/static/router.js` 加：上传 handler、滑块 change handler、预览按钮 handler、参考图 AI 按钮 handler

## 多模态 Prompt 设计

```
你是视频素材布局助手。用户上传了一张参考位置关系图（草图/截图/示意图都可能），
里面展示了 4 个素材应该放在哪个相对位置：
- 粗剪视频（主视频，通常占中间大面积）
- 字幕（SRT 文件内嵌到视频上的文字）
- 封面图片（小图，叠加层）
- 背景图片（视频比例不一致时填充背景）

请分析图片，输出 4 个素材的位置和缩放（归一化坐标 0-1，原点在左下浮）：
{
  "video":    {"x": 0.5, "y": 0.5, "scale": 1.0, "enabled": true},
  "subtitle": {"x": 0.5, "y": 0.9, "scale": 1.0, "enabled": true},
  "cover":    {"x": 0.1, "y": 0.9, "scale": 0.2, "enabled": true},
  "bg":       {"x": 0.5, "y": 0.5, "scale": 1.0, "enabled": false}
}

只返回 JSON，不要其他文字。如果图不清楚，scale 都填 1.0，enabled 根据可见性判断。
```

## 字体 5 项与 ffmpeg drawtext 对应

| UI 设置 | ffmpeg drawtext 参数 |
|---|---|
| 字体大小 | `fontsize=36` |
| 描边宽度/颜色 | `borderw=2:bordercolor=black` |
| 背景框 | `box=1:boxcolor=black@0.6:boxborderw=4`（圆角 ffmpeg 不支持，用 boxborderw 近似） |
| 字体粗细 | **不支持**：用 `font=NotoSansCJK-Bold` 切换字体文件 |
| 对齐 | `x=(w-text_w)/2`（左/中/右分别计算） |
| 字体本身 | `fontfile=/path/to/font.ttf` 或 `font=family_name` |

## 范围

**In**：
- 新增 `_render_fine_cut_zone` 函数（[slirn_home/app.py](slirn_home/app.py) 新增约 200 行）
- 新增 `fine_compose` 数据结构（task.json）
- 新增 3 个 API endpoint（upload/parse/render）
- 多模态模型集成（dashscope）
- 前端：上传/滑块/预览/AI 解析按钮 UI
- CSS 样式（fine_cut pane）
- 单元测试（mock dashscope）
- pytest（保存/读取 roundtrip）

**Out**：
- ❌ 不做视频转码/HDR/调色（保持原画质）
- ❌ 不做音频混音（用粗剪原音）
- ❌ 不做多字幕轨道（单字幕）
- ❌ 不做实时拖动预览（按需）
- ❌ 不做模板预设（用户原话没说，先不做）
- ❌ 不改 `funclip/` 上游（fork 项目）

## 风险与处理

| 风险 | 处理 |
|---|---|
| 多模态模型无 key | 检测 dashscope API key，没 key 时 AI 按钮置灰 + tooltip「请先配置 DashScope API Key」 |
| 多模态返回非 JSON | prompt 强调"只返回 JSON"；解析失败时用正则提取 JSON 块；再失败 → toast「请重试」 |
| ffmpeg 路径不存在 | 启动时检测 ffmpeg；缺失时「生成预览」按钮置灰 + tooltip |
| 字幕字体文件缺失 | 默认用项目 `font/STHeitiMedium.ttc`（已存在 55 MB） |
| 上传大文件阻塞 | 前端分片上传（>10MB）；后端流式写入 |
| 跨分辨率位置错位 | 归一化坐标 0-1；输出按目标分辨率（如 1920×1080）乘 |
| 预览渲染慢（>3s） | 加进度条；超过 10s 提示「请缩小视频或减少素材」 |
| 与字幕合成阶段（REQ-019）冲突 | fine_cut 是新阶段，独立位置，不复用现有 SRT 内嵌逻辑 |

## SOP 阶段

1. ✅ REQ 文档（本文）
2. ⏳ DESIGN：详细字段定义、API 签名、UI mockup、ffmpeg 滤镜模板
3. ⏳ 实施：后端 → 前端 → CSS → 测试
4. ⏳ 评审：code-review + pytest
5. ⏳ 验证：E2E 真机手测

## 用户补充 · x/y 改为像素坐标（归一化 0-1 反直觉）

### 原话

> 还有一点我觉得很奇怪，为什么那个坐标 X，Y 的位置是以 1 为单位的百分数，而不是像素数

### 改动（向后兼容：旧数据自动迁移）

| 字段 | 旧 | 新 |
|---|---|---|
| `layout.{video,subtitle,cover,bg}.x` | 0.0–1.0 | 0–1920 px（基于 1920×1080 设计空间） |
| `layout.{video,subtitle,cover,bg}.y` | 0.0–1.0 | 0–1080 px |
| `layout.video.crop_*` | 0.0–1.0 | **保持 0-1**（源视频比例，与画布坐标正交） |
| `layout.*.scale` | 0.1–2.0 | **保持不变**（相对画布的缩放系数） |

**设计空间**：所有 x/y/scale 都基于 1920×1080 画布；当用户切到 720p 输出时，ffmpeg 在末尾统一 scale 到 1280×720（WYSIWYG：滑块拖到什么位置，画面就在哪）。

**迁移策略**：`fine_compose.json` 加 `_schema` 字段。`_schema < 2`（或缺失）视为旧数据，读时把每个 layout 的 x/y 乘以 (1920, 1080)，然后落回磁盘（升级到 `_schema: 2`）。

**默认值的换算**（保留旧默认值在 1080p 上的视觉位置）：
- video.x: 0 → 0 px；video.y: 0 → 0 px
- subtitle.x: 0.35 → 672 px；subtitle.y: 0.9 → 972 px
- cover.x: 0.7 → 1344 px；cover.y: 0.85 → 918 px
- bg.x/y: 0 → 0 px

### AI 解析 prompt 同步

> 输出 4 个素材的位置（**1920×1080 画布的像素坐标**，原点在左上角）：x: 0-1920，y: 0-1080

后端收到 AI 输出后，做 0-1 校验护栏，再乘以 (1920, 1080) 落盘。

## 不确定项（待 DESIGN 阶段定）

1. 输出分辨率：1080p (1920×1080) 默认？可让用户选？
2. 字幕字体默认用什么（项目已有 STHeitiMedium.ttc）？
3. 参考图大模型 prompt 是否要支持中文/英文双语？
4. 导出视频格式：mp4 h264 默认？
5. 预览渲染时间长度：3 秒？5 秒？完整视频前 30 秒？