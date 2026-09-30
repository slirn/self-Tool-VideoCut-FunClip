# DESIGN-20260930-094 - 短视频多素材混剪工作台

## 1. 背景

长视频模块以六阶段流水线生成一条长成片。短视频需求是“一源多片、多素材、
多轨道、多版本”，状态模型和渲染模型均不同，不能直接复用 `pipeline.json`
或 `fine_compose.json`。

## 2. 关键决策

### D1：独立业务模型，共用平台能力

- 独立：项目状态、时间线、渲染任务、模板和输出目录。
- 共用：认证、任务可见性、LLM 配置、上传、后台 job 模式、执行日志。
- 不修改现有六阶段 `STAGE_ORDER`，不向 `fine_compose.json` 写短视频数据。

### D2：结构化分镜优先，不做自由黑盒生成

LLM 只生成结构化 JSON：

- `title` / `hook` / `cta`
- `segments[]`
- `overlays[]`
- `subtitles[]`
- `bgm_material_id`

渲染器只消费规范化后的结构，确保可编辑、可验证、可重放。

### D3：FFmpeg 作为确定性渲染后端

- 视频片段先统一成 1080x1920 竖屏画布。
- 主片段使用 `xfade` 转场。
- B-roll 使用 `overlay`。
- 标题、Hook、字幕生成临时 ASS 后使用 `ass` 滤镜。
- BGM 与主音轨使用 `amix`。

### D4：本地兜底与外部 LLM 增强共存

项目配置允许 `allow_external_llm`。外部调用失败、未配置密钥或输出非法时，
服务必须使用本地规则生成可渲染分镜，不阻塞主流程。

## 3. 数据模型

```text
tasks/<task_id>/short_video/<project_id>/project.json
tasks/<task_id>/short_video/<project_id>/assets/
tasks/<task_id>/outputs/short_videos/<project_id>/<variant_id>.mp4
```

核心对象：

- `materials[]`：视频、图片、音频素材。
- `config`：画布、时长、版本数、转场、LLM 开关、BGM 音量。
- `storyboard.variants[]`：结构化分镜。
- `render_jobs[]`：异步渲染记录。

## 4. API 边界

沿用项目现有 `/slirn/api/*` 扁平命名和 `{ok, ...}` 响应：

| 端点 | 语义 |
| --- | --- |
| `POST /short_video_list` | 当前用户可见的短视频项目列表 |
| `POST /short_video_create` | 从任务创建项目 |
| `POST /short_video_get` | 打开项目 |
| `POST /short_video_upload_material` | 上传视频/图片/音频 |
| `POST /short_video_ai_storyboard` | LLM 生成或本地兜底分镜 |
| `POST /short_video_save` | 保存配置和人工调整后的分镜 |
| `POST /short_video_render` | 异步渲染一个或多个版本 |
| `POST /short_video_render_status` | 查询渲染状态 |
| `POST /short_video_render_cancel` | 取消渲染 |
| `POST /short_video_delete` | 删除项目及其产物 |
| `GET /short_video_file` | 读取项目素材或成片 |

所有端点继续经过现有认证中间件；带 `task_id` 的请求继续执行任务访问控制。

## 5. 与长视频的集成

```text
长视频六阶段（保持不变）
        |
        | 自动发现 optimize_compose / rough_compose / fine_export
        v
短视频项目（独立状态 + 独立时间线 + 独立渲染）
        |
        +--> 3 个 9:16 版本
```

## 6. 关键文件

- `slirn_home/short_video_service.py`
- `slirn_home/short_video_render.py`
- `slirn_home/short_video_ui.py`
- `slirn_home/static/short_video.js`
- `slirn_home/static/home.css`
- `slirn_home/app.py`

## 7. 验证

- 服务层：项目、素材、分镜、规范化、保存。
- API 层：创建、列表、上传、AI 分镜、保存、渲染状态。
- 渲染层：滤镜构图、ASS 文本、ffprobe 输出属性。
- 前端：真实浏览器创建项目、上传、生成分镜、保存、触发渲染。
- 回归：全量 pytest 和现有长视频工作台测试。

## 8. 未包含能力

- 平台发布。
- 人脸/主体跟踪。
- 节拍检测和自动卡点。
- 跨任务素材池。
- 专业拖拽式逐帧时间线。
