# REQ-20261013-100 — 短视频管线阶段重编号 + Stage 4「内容优化」（替换 3.5 核验）

## 解决什么问题

用户两项要求：

1. **阶段重编号**：Stage 3.5 这个小数号难看 — 3.5 改成 4，原 4（粗剪）→5，
   原 5（字幕优化）→6，原 6（精剪混编）→7。管线变为整数 1-7。
2. **「字幕一致性核验」改名「内容优化」并重做功能**：
   - [已确认] 输入 = Stage 3 生成的全部短视频（每条标题 + 字幕行）。
   - [已确认] LLM 按标题对字幕做三类优化：**切分**（长行拆多行）、
     **文字修改**（fix）、**整行删除**（delete）。
   - [已确认] 切分参考长视频「切分修剪」的功能语义（一行拆父编号.子序号）；
     实现上短视频 raw.srt 无字级时间戳（长视频严格模式宁可不切），
     采用 app.py 已有先例的**按子文本占比线性内插**行内时间戳（标注 ⚠️ 仅供参考）。

## 设计决策

1. **内部 state key 随显示编号重命名，load 时一次性迁移**：
   `stage3_verify→stage4_optimize`（新语义）、`stage4_coarse→stage5_coarse`、
   `stage5_refine→stage6_refine`、`stage6_finalize→stage7_finalize`。
   - 迁移在 `load_project` 幂等执行（新 key 不存在才搬）；旧 verify 状态
     结构与新阶段不兼容 → 迁移时**丢弃重置 pending**（核验报告无下游消费）。
   - **产物目录名不改**（`stage4/`、`stage5/`、`stage6/` 磁盘零迁移，
     旧项目 state 里的 rel path 继续有效）；新阶段目录 `stage4_optimize/`。
   - 端点 URL（`short_video_stage4_run` 等）与 JS action 名保留为稳定 API
     不随编号变；仅 verify 端点删除、新增 optimize 端点。
2. **优化结果写回 highlights.json 的 subtitle_lines**（下游粗剪/拼接自然
   使用优化后字幕与时间戳）；首次优化前备份原表到
   `stage4_optimize/backup_highlights.json`（已存在不覆盖）。
3. **优化后作废下游**：粗剪/字幕优化/精剪（reset_downstream）+
   `stage3_splice` 重置（行变了旧 clip 失效），对齐 rerun/restore 行为。
4. **LLM 协议**：逐条 highlight 调用（标题上下文独立、失败单条告警不炸整跑），
   输出 JSON `{"lines":[{"no":1,"op":"keep"|"fix"|"text":…}|"split"|"splits":[…]}]}`；
   行缺席 = 删除。split 校验：子文本按序拼接须与原文**去空白后相等**，
   不等则该行降级 keep + 告警（宁可不切不可错切，同长视频原则）。
   fix 自由文本；时间戳不动（fix/keep）或按占比内插（split 子行）。
   split/删除后行 `no` 按**当前行序**重编 1..M，`src_index` 继承父行。
5. **verify 功能整体移除**（模块/端点/JS/UI/测试）——被「内容优化」替换，
     git 历史可找回。

## 不做的事（anti-examples）

- ❌ 不做长视频式「建议→人工逐条决策」两步流（短视频条数多，直接产出优化
  结果；用户仍可回 Stage 3 页签手工微调，与 rerun/restore 同一编辑面）。
- ❌ 不迁移/清理旧项目的 stage3_verify 产物目录（留着无害）。
- ❌ 本需求不动长视频管线；快捷键配置提取、精剪混编搬运是后续独立需求。

## 验收标准

1. 阶段导航显示 1-7 整数编号：4=内容优化、5=粗剪、6=字幕优化、7=精剪混编；
   「字幕一致性核验」不再出现。 ✅ 证据：UI 渲染 `data-stage-key="stage4..stage7"`、
   `data-action="sv-stage4-optimize-run"`、端点 `/slirn/api/short_video_stage4_optimize`、
   旧 `stage3VerifyRun`/`sv-stage3-verify`/`.slirn-stage35-*` 全部清零。
2. 旧 key 项目 load 后：stage5_coarse/stage6_refine/stage7_finalize 状态就位
   （值与旧 key 相同）、stage4_optimize 为 pending。 ✅ 证据：迁移在
   `_normalize_project` 早于 setdefault 跑（修复 setdefault 抢占 bug），新
   key 已存在则保留新值；幂等；`tests/test_short_video_service.py` 新增
   `test_pipeline_key_migration_old_project` 覆盖。
3. Stage 3 done 后「内容优化」可跑：LLM 收到标题+编号行；keep 行原样、
   fix 行换文字保时间戳、split 行拆多行且子段时间戳按文本占比内插、
   缺席行删除、no 重编；备份文件生成且重跑不覆盖。 ✅ 证据：
   `test_optimize_run_end_to_end`（fix/split/delete 落地 + src_index 继承 +
   首尾对齐占比 + 组窗口重算 + 备份存在 + 下游 stage3_splice+stage5/6/7 重置
   + Stage 3 state 镜像同步）+ `test_optimize_run_backup_once`。
4. split 子文本拼接 ≠ 原文 → 该行降级 keep + 告警，不写错数据。 ✅ 证据：
   `test_optimize_parse_result_ops_and_downgrade`（坏 split + fix 空文本
   + 未知行号均降级 keep 并产告警）。
5. 优化后 highlights.json 更新、stage3_splice 与粗剪及之后全部回 pending。 ✅
   证据：见第 3 条端到端测试。
6. 全量 pytest 通过 / node --check 双 JS OK / ruff 零新增。 ✅ 证据：
   全量 1182 passed（基础 1133 + 新增 49）；`node --check short_video.js
   router.js` 通过；`ruff check` 四个工作树变更文件全部 ≤ HEAD 同文件
   错误数（short_video_ui.py 5 vs HEAD 6、app.py 99 vs HEAD 99、
   short_video_service.py 0、short_video_optimize.py 0）。
7. 7861 重启后服务新 CSS（含新 `.slirn-stage4-opt-*`）+ 新 JS
   （`stage4OptimizeRun` 存在、`stage3VerifyRun`/`/short_video_stage3_verify`
   消除）。 ✅ 证据：`netstat` 7861 新 PID 24752、`http://127.0.0.1:7861/`
   含新 CSS 类（旧 `slirn-stage35-*` 全 0）、`/slirn/static/short_video.js`
   校验三件全过。

## 闭环记录

- 提交：6f7d540，pushed to main（ref-update `fa33d9c..6f7d540`）。
- 7861 后台重启任务 ID：bdfaz0dpm（live 进程 PID 24752）。
- 后续任务队列：B（快捷键提取到顶栏热词库前）→ C+D（短视频最后阶段 + 粗剪
  + 字幕优化按长视频搬运，方向相同合并侦察）。
