# DESIGN-20260917-030 — 优化字幕阶段（成片重识别 + 不明确字词提取替换）方案

对应 REQ-20260917-030。改动集中在 `slirn_home/`，tasklib / funclip 零改动。

## 1. 关键决策

| # | 决策 | 理由 |
|---|---|---|
| D1 | **新建 `optimize_service.py`**，`fine_service.py` 原样保留 | 新旧语义不同（成片时间基 vs 切分行 id 键）；旧文件继续服务旧任务的 fine_revision.json 读取（粗剪面板/rough_subs 端点），零回归风险；`apply_replacements`（重叠剔除纯函数）直接 import 复用，不复制代码 |
| D2 | ASR 走 `asr_service` 既有内部函数：`get_model(sd=False)` + `_run_recognition(video, hotword_str, sd=False)` + `segments_from_sentences` | 与字幕生成同链路（seaco-paraformer + 热词偏置）；单例复用避免双份 paraformer（SD 单例已加载时 sd=False 也复用它，`sd_switch='no'` 不返回说话人）；**绝不调用 `asr_service.start_job`** —— 它会覆盖 subtitle.json/srt，那是切分/修订全链路的根基产物 |
| D3 | 「只识别字幕信息」= `sd=False` 显式关闭说话人 | 本阶段产物是文本核对，不需要人员编号；省 cam++ 推理开销 |
| D4 | LLM 防御解析沿用 fine_service 成熟模式：before 必须逐字摘自行文本、after 非空且 ≠ before、重叠剔除（复用 `apply_replacements`）、解析失败减半重试、批间 0.6s | 与已验证的热词替换同样健壮（REQ-20260916-017 评审结论）；不明确字词提取是"提议"，宁缺勿错，最终人工分辨 |
| D5 | **词频按替换目标词（after）分组**；出现处（occurrence）为一等实体：{seg_i, pos, before, after, reason, applied} | 同一目标词可有多种错误写法（「神精网络」「神经网洛」→「神经网络」），按目标词计数才回答"这个词出现了几次"；逐处采纳/编辑需要 per-occurrence 状态 |
| D6 | 人工替换 = 行内出现项可编辑（input 默认填模型建议）+ 单独采纳/不采纳按钮；保存时服务端重算 new_text 与聚合统计（**前端只传决定，文本计算在服务端**） | 前端编辑值仅作输入，生效判定（after 非空、≠before）服务端兜底；与切分决策"全量收集、服务端落盘"同哲学 |
| D7 | 产物 `optimize_subtitle.json`（独立于 fine_revision.json）：meta + segments（成片行集，含 new_text）+ occurrences（逐处替换记录）+ words（按词聚合）+ saved_at | 对应关系（REQ-1.6）= occurrences 列表本身 + words 聚合；成片时间基独立成文件，不污染任何中间产物 |
| D8 | 阶段完成判定：optimize_subtitle.json 有 saved_at → done；**兼容旧任务**：fine_revision.json 有 saved_at 同样视为 done（已推进过旧流程的任务不回退） | TaskStatus 枚举/历史推进不变；旧任务工作台不出现"倒退回待办"的观感 |
| D9 | 删除旧阶段交互入口（/fine_revise、/fine_revise_status、/save_fine_revision 端点 + 对应 JS），新端点 `/optimize_subtitle`、`/optimize_subtitle_status`、`/save_optimize_subtitle`、`/optimized_srt` | 旧端点只被本仓 JS 调用，UI 重写后即死代码；保留读取端（load_fine 消费方）不动 |
| D10 | 热词为空不阻塞：ASR 无热词偏置照常识别，LLM 提示词说明"热词表为空"；面板提示建议补充热词 | 新阶段以成片为唯一硬输入；热词是增强不是门槛 |
| D11 | 成片播放复用 `/slirn/api/video/<tid>?src=rough_compose`（Range 流式） | 已有基建，成片时间基的行 start_ms 直接 seek |

## 2. 数据流

```
[开始优化] POST /slirn/api/optimize_subtitle {task_id, force?}
  ├─ 校验：rough_compose.mp4 存在 · LLM 已注册 · 无 running job · 有产物须 force
  └─ optimize_service.start_job(tid, video, hotwords, task_name, outputs_dir, entry)
       ├─ stage 加载模型（asr_service.get_model(False)，复用单例）
       ├─ stage 识别成片（_run_recognition(video, hotword_str, sd=False)）
       │    └─ segments_from_sentences → 成片行集 [{i,start_ms,end_ms,start,end,text}]
       ├─ stage 大模型分析 (n/m 批)：OPT_SYSTEM 提取不明确字词
       │    └─ parse_occurrences 防御解析 → occurrences（before 逐字、重叠剔除）
       └─ 落盘 optimize_subtitle.json（segments + occurrences[applied=true 默认] + words）
UI 轮询 /optimize_subtitle_status → done 刷新面板
  ├─ 词频 chips：「神经网络 ×3」… 点词过滤出现行
  ├─ 行列表：行内出现项 <s>before</s>→<input after> [✓/✕]；点行按成片时间跳播
  └─ [💾 确认保存] POST /save_optimize_subtitle {decisions:[{occ_id, applied, after}]}
       ├─ 服务端校验 + 重算各行 new_text + words 聚合 + saved_at
       ├─ 推进 TaskStatus.FINE_SUBTITLE_REVIEWED
       └─ [⬇️ 下载优化字幕 SRT] /optimized_srt → segments(new_text) → SRT
```

## 3. 改动清单

| 文件 | 改动 |
|---|---|
| `slirn_home/optimize_service.py`（新建） | OPT_SYSTEM 提示词 + build_user_prompt；parse_occurrences 防御解析；start_job（ASR+LLM 后台线程）+ job_status；load_optimize / save_decisions（决定合并 + new_text/words 重算）；word_stats / effective_stats；build_srt |
| `slirn_home/app.py` | `_render_fine_review_zone` → `_render_optimize_zone` 重写；_WB_STAGES 更名「优化字幕」+ 文案；_wb_stage_states 判定改 D8 兼容口径；4 个新端点替换 3 个旧端点；JS：startOptimize/轮询/词过滤/出现项编辑与采纳/保存/SRT 下载/成片跳播；TASK_STATUS_LABEL 文案「精剪修改」→「优化字幕完成」 |
| `slirn_home/static/home.css` | 出现项样式（原文删除线 + 替换输入框 + 采纳开关）、词频 chips、不采纳态、复用 .slirn-fw-* 骨架 |
| `tests/test_optimize_subtitle.py`（新建） | 提示词构造/防御解析/重叠剔除/保存重算 new_text 与聚合/词频分组/SRT 生成/渲染含词频与出现项/旧任务兼容 |
| `docs/REQM/REQ-20260917-030-*.md`、本文件 | 需求 + 设计 |

## 4. 风险与对策

- **成片重识别耗时/内存**：成片 = 切分保留内容拼接，通常短于源视频；识别复用 0.7GB/小时口径的 16k 单声道抽取路径；模型单例复用（subtitle 阶段已加载则零增量）。job 进度含 ASR 阶段提示 + 已耗时。
- **LLM 幻觉（编造行/编造原文/整行改写）**：before 逐字子串校验、after≠before、重叠剔除、未知行 id 丢弃（D4 老三样）。
- **覆盖 subtitle.json 的误操作**：识别走内部函数（D2），产物只写 optimize_subtitle.json —— 代码评审重点核对无 `start_job`/`SUBTITLE_JSON` 引用。
- **旧任务观感**：已 done 的旧任务（fine_revision.json saved_at）阶段仍 done（D8）；其 optimize 面板显示"未优化"入口可重新开始，不破坏原产物。
- **成片被删除/重合成**：optimize_subtitle.json 与成片不一致 → 面板 staleness 提示（成片 mtime > created_at 时提示重新优化），与切分 staleness 同模式。
