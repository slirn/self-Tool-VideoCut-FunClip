# REQ-20260916-016 — 粗剪合成（可选阶段）

## 需求原话

> 把精简字幕阶段的名称改为粗剪合成，这一步是根据前一阶段完成的字幕信息，合成一版视频文件，看一下整体的效果。这一步骤为可选步骤，生成之后可以查看预览效果

## 诊断

原第 5 阶段「精剪字幕」（key `fine_subtitle`，状态名 `FINE_SUBTITLE_DONE`）是零实现占位——只出现在 `_WB_STAGES` 表里，面板渲染「规划中」。用户要的其实是：切分修剪之后立刻能看到**成片效果**——把保留区间真正拼成一版视频。直接按新语义实现该阶段，改名「粗剪合成」。

## 关键决策

| 决策 | 理由 |
|---|---|
| **concat demuxer + 重编码**（libx264 veryfast crf23 + aac 128k + faststart），弃用 `-c copy` | 实测任务 001（44min 源）：copy 路线 29s 完成但**拼接处 DTS 非单调数百次**（长 GOP 中位 14.4s + 997 边界所致）→ 播放异常；重编码 361s 完成、997 边界总偏差 +1.01s（≈1ms/段，字幕级精度）、**解码校验全净** |
| 合并口径与切分面板一致 | `load_cutlist(saved)` → `build_cutlist(sub_meta, rev, manual_marks, actions)` → `effective_keep_units`——与面板 stats、执行口径同源，所见即所合成 |
| 后台线程 + 进度轮询（对齐 asr_service 范式） | 6 分钟不能阻塞页面；`_JOBS` + 防重入 + daemon 全兜底；进度从 ffmpeg `-progress pipe:1` 的 `out_time_us` 解析（≤2Hz 节流） |
| 可选性 = 产物驱动 done + **不推进任务状态** | 不合成不影响后续阶段；阶段状态只看 `rough_compose.mp4` 是否存在（服务重启不丢——status 端点产物在即回 done）；tasklib 在 slirn-standalone 不可改，状态名沿用 `FINE_SUBTITLE_DONE` 仅作 rank 标记 |
| 过期提示 | 产物生成后若切分决策/修订再更新（saved_at > 产物 mtime）→ 黄条「⚠️ 切分决策在合成之后有更新」提示重新合成 |
| 原子落盘 | 先写 `.part.mp4`，成功 `os.replace`；ffconcat 列表用后即删；超时 1800s 兜底 |

## 实现

| 改动 | 说明 |
|---|---|
| `slirn_home/compose_service.py`（新建） | `compose_rough_cut`（ffconcat inpoint/outpoint + 重编码 + 进度回调）、`merge_intervals`（缝隙 <1ms 合并——保留单元接缝不产生无谓切点）、`rough_compose_path`、`start_compose`/`job_status` 后台 job 层 |
| `app.py` 阶段表 | `fine_subtitle` → `("rough_compose", "FINE_SUBTITLE_DONE", "粗剪合成", "🎥", …)`；阶段条加「可选」徽章（仅此阶段） |
| `_render_rough_compose_zone` | 守卫（未修订/未决策/无字幕 → guide 按钮去对应阶段）；stats「保留单元 N 个（合并连续段后 M 个切点）· 预计成片时长」；产物在 → 预览播放器 + 元信息 + 过期提示；`🎬 合成粗剪视频`/`重新合成` 按钮 + 进度状态条 + hint（口径/耗时说明） |
| `POST /slirn/api/compose_rough` | 守卫链（任务/修订/全决策/字幕/区间非空/视频在）→ 同口径 intervals → `start_compose`；已在跑返回 running |
| `POST /slirn/api/compose_rough_status` | job 轮询；服务重启后 job 丢失但产物在 → 回 done（前端可恢复） |
| 视频 ASGI `?src=rough_compose` | 预览播放器直接走既有 `/slirn/api/video/`（Range/206 支持） |
| JS `rcCompose`/`rcInjectPreview` | 启动 → 2s 轮询 → 按钮实时百分比「🎬 合成中… N%」；完成 toast ✅ + 注入/刷新预览播放器 + `bindSpeedControl`（REQ-20260916-014 倍速控件同款）；失败恢复按钮 + 错误 toast |
| `home.css` | `.slirn-wb-stage-optional` 可选徽章（紫系小 pill，与试听条/倍速控件同风格） |

## 验证

- `pytest tests/ -q` → **162 passed**（`test_workbench_layout` 断言更新：占位 4 → 3 + 粗剪合成区真实化）；`ruff check .` → clean。
- **离线合成验证**（`work/REQ-20260916-016-rough-compose/_service_test.py`，走 compose_service 真实代码路径，输出仅落 work/）：
  - Phase A（合成 30s 源）：3 区间 → 20.01s/3 段 ✅；**防重入**（运行中二次 start 返回 False）✅；job_status 返回副本不受外部改动污染 ✅
  - Phase B（001 真实数据全量）：1681 保留单元 → 合并 997 段 → 预计 2094.07s；合成结果 **2095.08s / 191.3MB / 386.7s**，偏差 +1.01s（≈1ms/段）；**解码校验 rc=0 零 stderr**；`.part`/`.list.ffconcat` 无残留 ✅
- **E2E 只读**（CDP headless，`_e2e_test.py`，chrome_cdp_034/9351）八项全过：阶段条含「🎥 粗剪合成」+「可选」徽章（唯一可选阶段，紧跟切分修剪）；面板 stats「保留单元 1681 个（合并连续段后 997 个切点）· 预计成片时长 34:54」+ 合成按钮（data-action=compose-rough）+ hint；无产物 → 无预览无过期条；`GET video?src=rough_compose` → 404；切分面板不受扰；status 端点无 job 无产物正确报错；截图存档；**零写请求**（不点合成，api 仅 workbench）。

## 关联

- 前置：REQ-20260916-011（切分执行口径——合成直接消费 `effective_keep_units`）、REQ-20260916-014（倍速控件——预览播放器复用）
- 提交：`feat(home): 粗剪合成可选阶段——按切分保留区间合成粗剪视频预览 (REQ-20260916-016)`
