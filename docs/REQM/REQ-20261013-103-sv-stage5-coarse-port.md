# REQ-20261013-103 — 短视频 Stage 5 粗剪按长视频 compose_rough 整套搬（C2）

> 父任务：[REQ-20261013-102 §2.3 C2](REQ-20261013-102-sv-coarse-refine-finalize-port.md)
> 设计：[DESIGN-20261013-103](../design/DESIGN-20261013-103-sv-stage5-coarse-port.md)
> 状态：🚧 实现中

## 解决什么问题

短视频 Stage 5 粗剪当前只有「同步阻塞的 run_stage4 + 每条 highlight 一个 `<video>` 卡片」。

参照长视频 compose_rough 四件套（`compose_rough` 异步 + `compose_rough_status` 轮询 + `compose_rough_delete` 同步删除 + `compose_rough_preview_subs` 连续 SRT 预览）整套搬过来，让短视频粗剪与长视频粗剪功能对齐。

## 端点

| 方法 | 路径 | 功能 |
|---|---|---|
| POST | `/slirn/api/short_video_stage5_export` | 异步启动粗剪合成（仿 `compose_rough`） |
| POST | `/slirn/api/short_video_stage5_export_status` | 进度轮询（仿 `compose_rough_status`） |
| POST | `/slirn/api/short_video_stage5_delete` | 同步删除粗剪成片（仿 `compose_rough_delete`） |
| POST | `/slirn/api/short_video_stage5_preview_subs` | 跨 N 条粗剪拼连续 SRT（仿 `compose_rough_preview_subs`） |

## 验收标准（与 DESIGN §5 对齐）

1. 端点齐：stage5_export / stage5_export_status / stage5_delete / stage5_preview_subs 全部 200 + 正常响应
2. 异步进度可轮询：启动后 setInterval 1.5s 拿到 progress 0-100；done 时返回 stage5_coarse 完整 state
3. UI 改造：`_stage5_coarse` 顶部状态卡 + 3 个新按钮（复制/下载连续 SRT + 删除）可见
4. 单条 SRT 操作：每个 clip 卡片内「📋 复制 SRT」+「⬇ 下载 SRT」可工作
5. 删除：清 stage4/coarse_NN.{mp4,srt} + 写历史 + 推进 reset_downstream_stages
6. 短/长隔离：调本子任务端点**不**写长视频 `tasks/<tid>/outputs/...` 任何文件
7. 质量门：ruff 工作树 == HEAD（零新增）、node --check router.js 通过、全量 pytest ≥ 现有基线 + C2 新增 ≥ 5 测试通过
8. 7861 重启：Python 改动 → TaskStop + 重启

## 闭环记录

| 项 | 值 |
|---|---|
| REQ 文档 | REQ-20261013-103-sv-stage5-coarse-port.md（本文件） |
| DESIGN 文档 | DESIGN-20261013-103-sv-stage5-coarse-port.md |
| 提交号 | 7ae40d8 |
| push ref-update | `599cf47..7ae40d8  main -> main` |
| 7861 PID | bzm9zqpnq（SLIRN_AUTH_BYPASS=1） |
| 测试 | 1195 passed（基线 1186 + C2 新增 9）+ 4 端点 smoke 200 |
| 验收 | 8 项 ✅ 全部通过（见下） |
| 下一子任务 | D1（Stage 6 字幕优化按长视频搬） |

### 验收逐条

1. ✅ 端点齐：4 端点全部 200 + 正常响应（smoke 已验）
2. ✅ 异步进度可轮询：smoke 启动后 status 端点返 running=true + state=running
3. ✅ UI 改造：`_stage5_coarse` 顶部状态卡（slirn-rc-status-card + 进度条）+ 3 个新按钮（重新合成 / 删除 / 连续 SRT 复制 / 连续 SRT 下载）
4. ✅ 单条 SRT 操作：每个 clip 卡片内「📋 复制 SRT」+「⬇ 下载 SRT」可见
5. ✅ 删除：delete_coarse 清 stage4/coarse_NN.{mp4,srt} + stage5/preview_subs.srt + 写 KIND_SHORT_VIDEO_COARSE_DELETE 历史 + 推进 reset_downstream_stages（测试覆盖）
6. ✅ 短/长隔离：调 delete_coarse 后长视频 outputs/ 文件数不变（测试 `test_delete_coarse_does_not_touch_long_video_outputs` 覆盖）
7. ✅ 质量门：ruff 106→104（零新增）、node --check 通过、全量 pytest 1195 passed
8. ✅ 7861 重启：原 b3dh9r95p / b3gwu93ia 停掉，新进程 bzm9zqpnq（带 SLIRN_AUTH_BYPASS=1）

## 与已闭环任务的关系

- 父任务：REQ-20261013-102（599cf47）
- 紧跟 [[REQ-20261013-101]]（快捷键顶栏）之后
- 下一串行任务：D1（Stage 6 字幕优化按长视频搬）
