# REQ-20260918-047 — 流程配置 + 自动执行（设计文档，待审）

| 字段 | 值 |
|---|---|
| 编号 | REQ-20260918-047 |
| 日期 | 2026-09-18 |
| 优先级 | P1（核心工作流改造） |
| 状态 | 📝 设计中（待用户审阅后再实现） |
| 关联 | REQ-20260916-005（快捷键自定义 — 同款「选项+记忆+应用」体验）；后端服务 on_success 钩子（asr_service.py:346 / revision_service.py:477 / optimize_service.py:249） |
| 改动范围 | 新增 `slirn_home/pipeline_service.py`（执行器）+ `slirn_home/static/pipeline.js`（编辑器）| `slirn_home/app.py` 加 8 个新 API + 一个工作台标签/卡片 | `home.css` 加编辑器样式 | 测试 / 文档 |

---

## 0. 背景与现状

5 阶段全部上线后，逐阶段点按钮+等待+切下一阶段的方式在「成片序列」场景里摩擦很大 —— 用户已经决策过「这个任务系列里哪些步骤可省略、哪些步骤要停下来让我看一眼」，但每次新任务还得手动按一次。

探索代理（`fs_milan_47`）已盘清可程序化调用的服务面（见下「服务面表」）。本设计基于：
- 后端**已经**提供全部端点的 async 封装 + 重启恢复
- 前端**已经**提供全部批量改判的纯前端逻辑（040/039 batch bars）
- 现有后台任务模型是「内存 dict + 守护线程 + 状态轮询」，每个阶段已有 `*_status` API + 磁盘产物 fallback

## 1. 用户故事（按原话还原）

> 我需要一个任务处理流程配置的页面，把所有这些阶段配置到一个流程中，让流程按配置的过程一步步执行。
> 每一个阶段中可以选择执行哪些操作，例如：
> - **字幕修订**：「默认接受所有建议」/「保留全部为待人工」/「按关键词过滤再接受」；
> - **切分修剪**：「删除某说话人 N 的全部记录」/「默认保留全部」/「只保留带 spk 标签的行」；
> - **粗剪合成**：「合成前/合成后自动 X」；
> - **优化字幕**：「默认接受所有替换」/「全部驳回」。
>
> 设置都是选择式。配置完成后要配套**自动执行脚本**：从当前节点起，按流程跑。用户可**选择到哪一个节点暂停**（例：在粗剪合成入口暂停 — 让他看一眼切分修剪对不对；在优化字幕前暂停 — 让他先听成片）。

## 2. 设计要点

### 2.1 流程对象（per-task 持久化）

```
outputs/pipeline.json      # 流程配置 + 每次运行记录（last run + history 末 10 条）
```

```jsonc
{
  "version": 1,
  "config": {
    "subtitle_generation": {
      "speaker_diarization": false,        // 对应 sd-switch（REQ-041 已默认关）
      "stop_after": "subtitle_generation"  // null / 当前 key = 跑完即停
    },
    "subtitle_review": {
      "accept_all_suggestions": true,       // 跑修订后自动 save_revision(全部 accept)
      "skip_categories": [],                // 空 = 全接受；["delete"] = 跳过 delete 建议
      "stop_after": "subtitle_review"
    },
    "rough_cut": {
      "delete_speakers": [],                // [] = 不删；[2,5] = 删 spk=2 与 spk=5 全部
      "default_decision": "keep",           // 修订阶段遗留未决策时本阶段如何补全
      "stop_after": "rough_cut"
    },
    "rough_compose": {
      "stop_after": "rough_compose"         // 用户想在合成前看切分修剪效果 → 设为 "rough_cut"
    },
    "optimize": {
      "accept_all_replacements": true,      // 默认全部 applied=true
      "stop_after": "optimize"              // 或 null = 跑完
    }
  },
  "stop_after": "rough_cut",                // 总停点（覆盖每阶段的 stop_after，取最严的）
  "updated_at": "2026-09-18T..."
}
```

**配置可继承**：在新建任务步骤「🎬 流程配置」卡片上提供「继承自模板」下拉（默认 3 套：人工全审 / 半自动 / 全自动），省去每次都重设。

### 2.2 工作台接入点

在 `_render_workbench()` 的 `.slirn-wb-top` 右侧按钮组新增「⚙ 流程」按钮：
- 点击 → 打开一个抽屉式编辑器（不是新标签，占用工作台内）
- 抽屉内：5 阶段 tab + 每阶段的选项表单 + 底部「▶ 从当前节点运行 / ⏹ 停止 / 📋 复制为新任务」

### 2.3 自动执行器

```
POST /slirn/api/pipeline_run   {task_id, since: "rough_cut"|null, stop_after?: "rough_compose"}
GET  /slirn/api/pipeline_status {task_id}   → {state, current_stage, percent, log[], error?, stop_requested?}
POST /slirn/api/pipeline_stop  {task_id}
```

**执行模型**：复用现有 per-service `_JOBS` 模式。新建模块级 `_PIPELINE_JOBS[tid]`，守护线程顺序执行步骤，每步调对应原 stage API（不是直接调 service 函数，便于复用现成的 client polling 与前端 modal 状态）。完成或错误后写 `pipeline.json.history`，UI 用同一个轮询函数 `startPipelinePolling` 推送状态。

**调度伪代码**：
```
run(tid, since, stop_after):
  job.state = "running"
  for stage in [subtitle_generation, subtitle_review, rough_cut, rough_compose, optimize]:
    if stage < since: continue
    job.current_stage = stage
    cfg = config[stage]
    if cfg.requires_human_decisions_already(stage):   # 仅修订+切分需要
      if not meets_prereq(tid, stage):
        skip_stage(reason)
        continue
    case stage:
      "subtitle_generation": call gen_subtitle; wait poll until done
      "subtitle_review": 
        if config.accept_all_suggestions: 
          apply accept-all batch via /save_revision payload
        else: stop_with_msg("字幕修订需人工")
      "rough_cut":
        if config.delete_speakers: link + mark spk 行 delete
        build_cutlist + save_cut_decisions(actions={spk→delete})
      "rough_compose": call /compose_rough; wait poll until done
      "optimize":
        call /optimize-subtitle; if accept_all → /save-optimize-subtitle(all applied)
    if stage == stop_after:
      stop_with_msg("已在配置节点暂停（cursor={{stage}}）")
      return
```

**强制依赖**（沿用现有服务面验证）：
- 字幕生成 → 字幕修订 → 修订未决策时**不能**跑切分（cutlist 门槛 `all_decided`）
- 切分未产出粗剪 → 优化**不能**入口（`rough_compose.mp4` 缺失就 3241-3243 拒）
- 每步失败 → 整流程停止 + 报错保留在 history（**不**自动重试，让用户决策）

**自动执行与现有手动按钮的关系**：手动按钮始终可用，**自动执行不抢占**（每步调 API，独立 job，不阻塞手动按钮 — 后台 job 已占时手动调用同一 service 会返回 `False`，UI 提示「自动流程正在跑 X 阶段」）。

### 2.4 节点控制两层
1. **节点层 stop_after**：每阶段可独立设「这阶段完成后停」
2. **流程层 stop_after**：抽屉底部「最严的暂停点」下拉（枚举 5 阶段 + null = 跑完）

执行时取 `min(per-stage, flow-level)` — 谁更早就听谁的。

### 2.5 UI

抽屉编辑器（`.slirn-pipe-edit`）：
- 5 tab 顶部 tab
- 当前 tab 表单：所有「选项 + 二级说明」
- 底部固定条：保存 / 重置默认 / ▶ 从当前阶段跑（带 running 状态显示 + 进度条）

工作台状态条（`.slirn-pipe-status`）：跑流程时浮在顶部，显示 `当前阶段 · 进度 · 日志最近 3 条 · ⏸/⏹`。位置参考 046 的 autonext 开关。

### 2.6 测试策略

- 单测：`test_pipeline_service.py` 覆盖配置校验、stage 调度顺序、stop_after 截断、prereq 跳过、磁盘产物 fallback
- E2E：`work/REQ-20260918-047/_e2e.py` 走完整链路：建任务 → 配置流程 → 从桌面运行 → 看到每个阶段的进度 → 验证 stop_after 停 → 验证错误时停 → 验证手动按钮仍可用
- 不变项：原有 5 阶段的所有单测与 E2E 必须继续过

### 2.7 不在本期范围

- ❌ 流程模板共享库（先支持「3 个内置 + 通用 ≠」3 套按钮，不做用户自建模板）
- ❌ 跨任务串行调度（一台机器跑多个任务的流水线调度器 — 留给后续）
- ❌ 自动重试（任何步骤失败都停下，留给用户判断）
- ❌ LLM 模型/严谨性级别的覆盖（沿用工作台顶栏的全局当前模型与当前严谨性设置 — 不在流程里再设）

---

## 3. 服务面表（探索代理产物摘要）

| 阶段 | 启动 API | 状态 API | 关键参数 | 完成判定 |
|---|---|---|---|---|
| 字幕生成 | `POST /gen_subtitle` | `POST /subtitle_status` | `sd` | `state=done` + `subtitle.json` |
| 字幕修订 | `POST /revise_subtitle` | `POST /revise_status` | `rigor, force` | `state=done` + `revision.json` |
| 接受所有 | — | — | — | `POST /save_revision` 全 accept |
| 切分修剪 | `POST /build_cutlist` | 同步 | — | `cutlist.json` saved |
| 删说话人 | `POST /cut_speaker_link` | — | spk 列表 | 行级 mark + `save_cut_decisions` |
| 粗剪合成 | `POST /compose_rough` | `POST /compose_rough_status` | — | `state=done` + `rough_compose.mp4` |
| 优化字幕 | `POST /optimize_subtitle` | `POST /optimize_subtitle_status` | `force` | `state=done` |
| 接受替换 | — | — | — | `POST /save_optimize_subtitle` all applied |

## 4. 验收标准

- [ ] AC-1 抽屉编辑器可保存/读取 `outputs/pipeline.json`（含 3 套内置模板 + 自定义）
- [ ] AC-2 每阶段表单覆盖表 §2.3 的所有可选项
- [ ] AC-3 自动运行：可从任意阶段切到该阶段跑，按配置顺序走完
- [ ] AC-4 节点 stop_after：流程在指定阶段完成后停，UI 显示「已达暂停点」
- [ ] AC-5 错误阶段：流程停在出错阶段，log 报错信息，用户可手动恢复
- [ ] AC-6 prereq 缺失：自动跳过该阶段 + 提示（如未跑修订就尝试粗剪）
- [ ] AC-7 手动按钮：自动跑期间手动点同一阶段 → toast 提示已在自动跑，不报错
- [ ] AC-8 ruff 0 错 / 全量 pytest 通过 / 真实浏览器 E2E 跑通完整链路

## 5. 实现步骤（待用户批准）

1. 写 `pipeline_service.py`（schema + 调度器 + 历史）
2. 写 `app.py` 的 4 个 API（run/status/stop/get）
3. 写 `pipeline.js`（抽屉编辑器 + 状态条）
4. 改 `home.css` 样式
5. 单测 → E2E → 文档

请审阅后告知是否：
- 采纳此设计
- 调整某些阶段的可选项
- 调整 stop_after 语义（当前「半途停」还是「完成即停」）
- 调整 3 套内置模板（现在打算：人工全审 / 半自动 = 修订+切分人工 / 全自动 = 全接 accept）