# VERIFICATION-20260930-094 - 短视频多素材混剪工作台

## 1. 验收范围

验证独立短视频项目、素材上传、LLM/本地分镜、时间线编辑、9:16 FFmpeg
渲染、异步状态、产物预览与长视频回归。

## 2. 自动验证

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 短视频专项 | `pytest tests/test_short_video_service.py tests/test_short_video_api.py -q` | 11 passed |
| 执行历史兼容 | `pytest tests/test_execution_history.py -q` | 33 passed |
| 全量回归 | `pytest tests/ -q` | 1073 passed |
| Python 静态检查 | `ruff check short_video_* execution_history ...` | 通过 |
| 前端语法 | `node --check short_video.js; node --check router.js` | 通过 |
| 规范校验 | `standards verify --target .` | valid=true |

## 3. 真实渲染验证

使用临时生成的红色/蓝色视频、图片 B-roll 和 WAV BGM，走真实 FFmpeg
异步渲染闭环。结果为：

- 状态：`done`
- 输出：1080x1920
- 视频流：H.264
- 音频流：AAC
- 滤镜链路包含：`xfade`、`overlay`、`ass`、`amix`

## 4. 浏览器验证

使用项目 Edge CDP 自动化：

1. 登录并进入“短视频混剪”。
2. 创建项目并自动导入任务成片。
3. 使用本地规则生成 3 个分镜版本。
4. 渲染一个版本。
5. 页面出现可播放的成片元素。
6. 删除测试项目，未留下运行时项目数据。

验收截图：

[20-short-video-mixer.png](../images/manual/20-short-video-mixer.png)

## 5. 已知限制

- 当前是结构化编辑，不是拖拽式逐帧 NLE。
- 尚未支持人脸/主体跟踪、节拍自动卡点。
- 外部 LLM 真实调用取决于用户模型配置；自动化测试使用 mock 验证结构化和降级路径。
- 当前短视频项目归属单个任务，跨任务公共素材库留待后续。
