# DESIGN-20260917-029 — 说话人分离接入方案

对应 REQ-20260917-029。改动集中在 `slirn_home/`，上游 `funclip/` 零改动。

## 1. 关键决策

| # | 决策 | 理由 |
|---|---|---|
| D1 | 识别走上游既有 SD 分支：`clipper.recog(wav, "Yes", ...)`，模型单例加挂 `spk_model=cam++` | 上游 `videoclipper.py` 的 sd 分支（`return_spk_res=True`）本来就产出逐句 `spk` 标签，这是上游 FunClip Web 界面"区分说话人"的同一链路；自建分离管线则重复造轮子且引入新依赖 |
| D2 | 模型**双懒加载单例**：`_MODEL`（无 spk）/ `_MODEL_SD`（挂 cam++），按需构建；`get_model(sd=False)` 时若 `_MODEL_SD` 已加载则直接复用它（sd_switch='no' 不返回说话人结果，识别不受影响，省一份 paraformer 内存） | 评审修订（原方案"始终挂 spk 一个单例"）：cam++ 未缓存的离线环境关 SD 仍要能生成字幕——懒构建让无 spk 模型不因缺 cam++ 而加载失败；复用规则避免同一进程两份 paraformer |
| D3 | 人员编号 = **1 起始、按首次出现顺序**归一，不复用 FunASR 原始簇标签 | cam++ 簇标签序号无语义（不保证按出现顺序、不保证连续）；"先说话的人 = 人员1"对课程视频最直观。归一后 json/UI/SRT 三处同一编号 |
| D4 | `subtitle.srt` 改为**从 segments 构建**（不再直接落上游 `generate_srt` 的输出） | ① spk 标签要用归一编号，上游输出是原始簇标签；② 上游 `generate_srt` 块间无空行（非标准 SRT），从 segments 构建可输出标准格式；③ segments 本就是 json/UI 的数据源，构建后 srt⇄json⇄UI 天然一致（REQ-20260915-001 AC-5 的延续） |
| D5 | SRT 说话人标签放**序号行**（`3  spk1`），不进文本行 | 与上游 FunClip SD 输出格式同构（下游 FunClip 系工具可识别）；不污染文本行，后续字幕清洗/热词替换按文本处理不受影响 |
| D6 | 开关：生成字幕区的复选框「区分说话人」，默认开，随 `/gen_subtitle` 请求体传 `sd` | 单说话人视频 cam++ 可能误分裂成多人，保留一键关闭的退路；默认开满足"生成时同时分辨"的主诉求；上游 Web 界面同样提供 SD 开关 |
| D7 | `meta.speakers = {"count": K, "stats": [{"spk": n, "sentences": c}, ...]}`，仅 SD 开且有标签时写入 | 纯增量字段；旧 json 无此字段时 UI 静默降级（不显示统计），AC-7 兼容 |
| D8 | 统计口径 = 字幕段数（断句后每行 1 句） | 与 UI 列表行数、`speakers.stats` 总和、subtitle.srt 块数一致，可互相印证 |

## 2. 数据流

```
POST /slirn/api/gen_subtitle {task_id, sd=true}
  └─ asr_service.start_job(..., sd=True)
       ├─ _run_recognition(video, hotwords, sd_switch="Yes")   ← 上游 recog SD 分支
       │     state['sentences'] = [{text, timestamp, spk(簇标签), ...}]
       ├─ segments_from_sentences(...)   → 每段透传 spk_raw
       ├─ assign_speaker_numbers(...)    → 首次出现顺序 → spk(1..K)，剔除 spk_raw
       ├─ speaker_stats(...)             → [{spk, sentences}]
       ├─ segments_to_srt(segments)      → "N  spkK" 序号行 + 标准空行分隔
       └─ 落盘 subtitle.srt / subtitle.json(meta.version=2, speakers=...)
UI 重新渲染 → 行徽标「人员N」(6 色循环) + 统计行「2 位说话人：人员1 21 句、人员2 8 句」
```

## 3. 改动清单

| 文件 | 改动 |
|---|---|
| `slirn_home/asr_service.py` | 模型挂 spk_model；`_local_model_dir` 缓存优先解析（model id 命中本地 modelscope 缓存时传目录路径加载，绕开 funasr 传 id 必联网的版本核对，离线可加载）；`_run_recognition` 参数化 sd；新增 `assign_speaker_numbers` / `speaker_stats` / `segments_to_srt`；`start_job` 增加 `sd` 参数并写 speakers meta + 日志统计 |
| `slirn_home/app.py` | `_render_subtitle_zone`：开关复选框、行徽标、统计行；`/gen_subtitle` 读 `sd`；JS `gen-subtitle` 上报开关状态（closest 定位所在卡片的开关，容错 id 重复）；开关状态 localStorage 记忆 + 重渲染后恢复（评审修订 #1/#3） |
| `slirn_home/static/home.css` | 徽标样式（6 色循环）、has-spk 行网格、active 行适配、窄屏适配 |
| `tests/test_asr_service.py` | 编号归一/统计/SRT 格式/开关关闭/渲染徽标与统计等用例 |

## 4. 风险与对策

- **cam++ 首次运行需下载**（modelscope 缓存离线包之外的增量）→ 日志提示；失败时 job 走既有 error 通道，页面不崩。
- **funasr 传 model id 必联网**：`get_or_download_model_dir` 对 id 强制走 snapshot_download 版本核对，网络/代理失效时即使缓存完整也抛 `model not registered`（实测本机系统代理失效即中招）→ `_local_model_dir` 命中本地完整缓存（含 config.yaml）就改传目录路径（本地路径的版本检查失败会被吞掉），未命中回退 id 走正常下载。
- **长视频内存**（REQ-20260917-023 的教训）：cam++ 逐 VAD 段嵌入为 192 维向量，相对 0.7GB/小时的音频本体可忽略；保持 ffmpeg 16k 单声道抽轨路径不变。
- **spk 字段类型**：FunASR 可能返回 numpy 整型（json 不可序列化）→ 归一时 `int()` 强转，异常段视为无标签降级。
- **上游大小写陷阱**：`recog()` 判 `sd_switch == 'Yes'`（大写 Y），传 `'yes'` 会静默走普通分支（skill 脚本即中招）→ `_run_recognition` 内联 `"Yes" if sd else "no"` 并在 docstring 注明。
