# DESIGN-20260918-047 — 流程配置 + 自动执行（实现方案）

| 字段 | 值 |
|---|---|
| 编号 | DESIGN-20260918-047 |
| 日期 | 2026-09-18 |
| 状态 | ✅ 设计文档 REQ-20260918-047-pipeline-auto-run.md 已获用户批准，本设计落地实施 |
| 配套需求 | REQ-20260918-047 |
| 实现范围 | 新增 `slirn_home/pipeline_service.py`（执行器 + 持久化）\| `slirn_home/static/pipeline.js`（抽屉编辑器 + 状态条）\| `slirn_home/app.py` 加 5 个 API + 工作台接入点 \| `home.css` 加样式 \| 测试 / E2E |

---

## 0. 目标

按已批准的设计文档，把 5 阶段手动串行改造为「可配置 + 自动按顺序执行」：

- 工作台顶部新增「⚙ 流程」按钮 → 抽屉编辑器（5 tab + 配置表单 + ▶ 从当前节点运行/⏹ 停止）
- 持久化在 `tasks/<tid>/outputs/pipeline.json`（per-task）
- 内置 3 套模板（人工全审 / 半自动 / 全自动） + 用户自定义
- 后台守护线程顺序执行；每步调现有原 stage API（不直接调 service 函数，便于复用 client polling + modal 状态机）
- 两层 stop_after（per-stage + flow-level，取最严）
- 错误 → 停在出错阶段，log 报错信息，不自动重试
- 手动按钮始终可用；自动跑期间手动点同一阶段 → toast「已在自动跑 X 阶段」

---

## 1. 受影响文件清单

| 文件 | 改动类型 | 说明 |
|---|---|---|
| `slirn_home/pipeline_service.py` | 新增 | 持久化 + 后台调度器 + 历史 |
| `slirn_home/static/pipeline.js` | 新增 | 抽屉编辑器 + 状态条 + 轮询 |
| `slirn_home/app.py` | 修改 | 加 5 个 API + 工作台 `⚙ 流程` 按钮 + 抽屉挂载点 + 状态条挂载点 |
| `slirn_home/static/home.css` | 修改 | 抽屉 / 状态条 / 表单 / tabs 样式 |
| `tests/test_pipeline_service.py` | 新增 | 配置 / 调度 / stop_after / 持久化 / 锁 单测 |
| `work/REQ-20260918-047/_e2e_047.py` | 新增 | 端到端 CDP 测试 |

---

## 2. 架构决策（ADR）

### 2.1 调度模型：in-process daemon thread（**不**走 TaskQueue）

**选项 A**：守护线程（沿用 `_JOBS` 模式，状态内存 + 落盘）
- ✅ 与 asr_service / compose_service / revision_service / optimize_service 完全同模式，已有 4 个 _JOBS 实例成功跑通
- ✅ 状态机 / 锁 / 重启恢复 / 错误处理都是现成代码直接抄
- ❌ 单进程内，跨任务串行还要 `os.pid` 锁

**选项 B**：subprocess + stdio JSON
- ❌ 启动开销大（Python 子进程每次 0.5s+），不适合频繁调度
- ❌ LLM 模型已经在主进程，没必要再开子进程隔离

**选项 C**：Celery / RQ
- ❌ 引入 Redis/RabbitMQ 依赖，CLAUDE.md 明确禁止不批准的新依赖

**选定 A**：理由 = 复用现成 4 个 _JOBS 模式，零新基础设施依赖，状态机/锁/错误处理全抄。

### 2.2 调度器调谁：HTTP 内部回环（**不**直接 import 调函数）

**选项 A**：调现有 `/slirn/api/*` HTTP 端点（走 fetch + polling）
- ✅ 复用现有 client 端 polling 函数与 modal 状态显示
- ✅ 调度器代码最少（只有循环 + sleep）
- ❌ 多一次 HTTP 序列化（微秒级，可忽略）

**选项 B**：直接 import service 模块（`asr_service.start_job()`）
- ❌ service 内部 `_JOBS` 状态机不可被调度器访问（必须等结果后查 _JOBS）
- ❌ 调度器需要重新实现 polling 等候逻辑
- ❌ 单元测试 mock 困难

**选定 A**：调度器作为 in-process HTTP client 调 `/slirn/api/gen_subtitle` 等，用 `urllib` 而非 `requests`（零依赖，与 execution_history 模块同理）。

### 2.3 配置持久化：单文件 `pipeline.json`

**选项 A**：单 JSON 文件
- ✅ 用户编辑一次 → 一次 fsync
- ✅ 状态轮询读盘开销 = 0（任务跑时只读内存 _PIPELINE_JOBS）
- ❌ 并发写需要锁

**选项 B**：拆 `pipeline_config.json` + `pipeline_history.json`
- ❌ 用户编辑 + 自动跑时两文件需协调

**选定 A**：复用 execution_history 的「单写锁 + 原子写」模式。

### 2.4 锁实现：模块级 threading.Lock

**选项 A**：模块级 `threading.Lock()`
- ✅ 已有 execution_history 同款锁，工作良好
- ✅ 不需要文件锁（同一进程）

**选项 B**：`fcntl.flock` / `msvcrt.locking`
- ❌ 跨进程才有意义；本调度器 in-process 用不上

**选定 A**。

### 2.5 stop_after 解析：min(per-stage, flow-level)

两层 stop_after 都要遵守：
1. 节点层：每阶段 config 上 `stop_after: <stage_key>`
2. 流程层：抽屉底部「总停点」下拉

执行器在每阶段完成前计算 `effective_stop_after = min(per_stage, flow)`：
- 用阶段顺序索引比较（subtitle_generation < subtitle_review < rough_cut < rough_compose < optimize）
- 达到任一阈值就停

### 2.6 错误处理：失败即停 + 写入 log + 通知用户

- 单步失败 → 整流程停止，调度器把 `state="error"`、`current_stage=<failed>`、`error=<msg>` 写回 `_PIPELINE_JOBS` + `pipeline.json.history`
- toast 由前端轮询发现 state 变化自动弹
- **不**自动重试（任何步骤失败都留给用户判断）

### 2.7 UI：抽屉式编辑器（**不**做新标签页）

工作台顶部按钮组新增 `⚙ 流程` → 点击展开抽屉。理由：
- 抽屉与工作台共用上下文（任务信息已加载），切换不丢状态
- 工作台阶段列表可继续点击切到对应阶段观察进度

---

## 3. 接口设计

### 3.1 `pipeline_service.py` 模块

```python
PIPELINE_FILENAME = "pipeline.json"  # outputs/ 下

@dataclass
class PipelineConfig:
    version: int = 1
    config: dict = field(default_factory=dict)  # 5 个 stage 配置
    stop_after: str | None = None  # 流程层 stop_after

@dataclass
class PipelineJob:
    state: str  # idle / running / done / error / stopped
    current_stage: str | None
    percent: float
    started_at: float | None
    finished_at: float | None
    error: str | None
    log: list[dict]   # [{stage, msg, level, ts}, ...]
    history: list[dict]  # 最近 10 次完整运行的 summary

def default_config() -> dict:
    """返回设计文档 §2.1 的默认配置（5 阶段都跑 + 各 stop_after = 自己）。"""

def load_pipeline(outputs_dir: Path) -> dict | None:
    """读取 pipeline.json；不存在/损坏 → None。"""

def save_pipeline(outputs_dir: Path, cfg: dict) -> None:
    """原子写配置 + 记录 updated_at。"""

def pipeline_status(tid: str) -> dict | None:
    """读内存 job 状态（无 job → None 表示从未跑过）。"""

def run_pipeline(tid: str, mgr, since: str | None = None) -> bool:
    """启动后台守护线程；已有 running job → False。"""

def stop_pipeline(tid: str) -> bool:
    """请求停止（设置标志位，下次循环检查时退出）。"""
```

### 3.2 端点契约

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| POST | `/slirn/api/pipeline_get` | `{task_id}` | `{ok, config, status, history}` |
| POST | `/slirn/api/pipeline_save` | `{task_id, config}` | `{ok, saved_at}` |
| POST | `/slirn/api/pipeline_run` | `{task_id, since?}` | `{ok, started}` |
| POST | `/slirn/api/pipeline_status` | `{task_id}` | `{ok, state, current_stage, percent, log, error, history}` |
| POST | `/slirn/api/pipeline_stop` | `{task_id}` | `{ok, stopped}` |

### 3.3 配置 schema（与设计文档 §2.1 同源，复述）

```jsonc
{
  "version": 1,
  "config": {
    "subtitle_generation": {
      "speaker_diarization": false,
      "stop_after": "subtitle_generation"
    },
    "subtitle_review": {
      "accept_all_suggestions": true,
      "skip_categories": [],
      "stop_after": "subtitle_review"
    },
    "rough_cut": {
      "delete_speakers": [],
      "default_decision": "keep",
      "stop_after": "rough_cut"
    },
    "rough_compose": {
      "stop_after": "rough_compose"
    },
    "optimize": {
      "accept_all_replacements": true,
      "stop_after": "optimize"
    }
  },
  "stop_after": "rough_cut",
  "updated_at": "2026-09-18T..."
}
```

### 3.4 阶段顺序 + 索引

```python
STAGE_ORDER = [
    ("subtitle_generation", "字幕生成", "subtitle"),
    ("subtitle_review", "字幕修订", "subtitle_review"),
    ("rough_cut", "切分修剪", "rough_cut"),
    ("rough_compose", "粗剪合成", "rough_compose"),
    ("optimize", "优化字幕", "fine_review"),
]
```

每个阶段对应一个 handler：`(tid, cfg, logger) -> bool`。handler 内部调对应 `/slirn/api/*` 端点。

### 3.5 handler 列表

| 阶段 | handler 行为 |
|---|---|
| subtitle_generation | 调 `/gen_subtitle`（body 含 sd 来自 config.speaker_diarization），polling `/subtitle_status` 等 done |
| subtitle_review | 若 config.accept_all_suggestions：调 `/revise_subtitle`（rigor 从当前 LLM 配置默认值取）→ 等 done → 调 `/save_revision` 全 accept；否则停 + msg「需人工决策」 |
| rough_cut | 若 config.delete_speakers：先 `cut_speaker.link_speakers` + `cut_speaker` 端点把所有 spk 行 mark delete → 调 `/build_cutlist` → 调 `/save_cut_decisions` 应用 actions |
| rough_compose | 调 `/compose_rough`（synchronous via `_JOBS`），polling `/compose_rough_status` 等 done |
| optimize | 调 `/optimize_subtitle`（force=True）→ 等 done → 调 `/save_optimize_subtitle` 全 applied |

### 3.6 stop_after 触发

每阶段 handler 完成后检查 `effective_stop_after`：
```python
stage_idx = STAGE_ORDER.index((stage_key, ...))
stop_idx = min(STAGE_INDEX[cfg[stage_key].stop_after], STAGE_INDEX[flow_stop_after])
if stage_idx >= stop_idx:
    return "stop_after_reached"
return "continue"
```

`stage_index = 5` 表示「跑完不因 stop_after 停」（即 null）。

---

## 4. 边界与错误处理

| 失败场景 | 处理 |
|---|---|
| pipeline.json 损坏 | 读盘失败 → 当 None；前端提示「无法读取流程配置，使用默认」 |
| 阶段未达 prereq | handler 返回 `(False, "缺少字幕生成产物")`；调度器记 skip，不视作 error；log 写「跳过」 |
| 单步运行时报错 | handler raise → 调度器捕获 → 状态 error → 通知 toast（轮询） |
| 跨任务并发 | 同一阶段两个任务同时跑：`_JOBS[tid]` 检查 → 第二个 start_job 返回 False → handler 当作 skip+log |
| 服务重启 | 内存 `_PIPELINE_JOBS` 丢失 → status 端点读盘 pipeline.json 看到 `state=="running"`（重启前写）→ 实际重新读 pipeline.json history（已记录最后一次 run） |
| 用户中途关闭页面 | 调度器继续后台跑；下次回到工作台时轮询立刻显示状态 |
| 手动按钮与自动跑冲突 | 自动跑期间手动点同一 service → service `start_job` 返回 False → UI 弹 toast「自动流程正在跑 X 阶段」（不动状态） |

---

## 5. 兼容性

- ✅ 完全后向兼容：旧任务无 pipeline.json → default_config() 提供默认（ac=AC-1）
- ✅ 与现有 5 阶段手动按钮无干扰：scheduler 调的是同一组 service；手动按钮调的是同一组 service；并发时谁先抢到 _JOBS 谁赢
- ✅ 与 REQ-040-046 增强无干扰：搜索/折叠/自动跳转都基于渲染 DOM，与本需求不重叠
- ❌ 不向后兼容：旧 execution_history.json 不感知 pipeline.json（两模块独立）

---

## 6. UI 设计

### 6.1 工作台顶部新增按钮

```html
<button class="slirn-btn slirn-btn-sm" data-action="pipe-open" data-task-id="...">⚙ 流程</button>
```

### 6.2 抽屉结构

```html
<aside id="slirn-pipe-drawer" class="slirn-pipe-drawer" hidden>
  <header class="slirn-pipe-head">
    <span>⚙ 流程配置</span>
    <div class="slirn-pipe-tpl">
      <select id="slirn-pipe-template">
        <option value="default">人工全审（默认）</option>
        <option value="semi">半自动</option>
        <option value="full">全自动</option>
        <option value="custom">自定义</option>
      </select>
    </div>
    <button data-action="pipe-close">✕</button>
  </header>
  <nav class="slirn-pipe-tabs">
    <button data-pipe-tab="subtitle_generation" class="active">1.字幕生成</button>
    <button data-pipe-tab="subtitle_review">2.字幕修订</button>
    <button data-pipe-tab="rough_cut">3.切分修剪</button>
    <button data-pipe-tab="rough_compose">4.粗剪合成</button>
    <button data-pipe-tab="optimize">5.优化字幕</button>
  </nav>
  <section class="slirn-pipe-body" id="slirn-pipe-body">
    <!-- 当前 tab 表单，JS 切换内容 -->
  </section>
  <footer class="slirn-pipe-foot">
    <label class="slirn-pipe-flow-stop">总停点 <select id="slirn-pipe-flow-stop">…</select></label>
    <button data-action="pipe-save">💾 保存配置</button>
    <button data-action="pipe-run" class="slirn-btn-primary">▶ 从当前节点运行</button>
    <button data-action="pipe-stop">⏹ 停止</button>
  </footer>
</aside>
```

### 6.3 状态条（自动跑时浮在顶部）

```html
<div id="slirn-pipe-status" class="slirn-pipe-status" hidden>
  <span class="slirn-pipe-status-stage">当前阶段：字幕修订</span>
  <progress max="100" value="42"></progress>
  <span class="slirn-pipe-status-msg">分析中...</span>
  <button data-action="pipe-status-collapse">▾</button>
</div>
```

---

## 7. 测试策略

### 7.1 单测（`tests/test_pipeline_service.py`）

| 用例 | 覆盖 |
|---|---|
| test_default_config_keys | 5 个阶段配置项都有默认值 |
| test_load_pipeline_missing_returns_none | 不存在 → None |
| test_load_pipeline_corrupt_returns_none | 损坏 → None（不抛） |
| test_save_pipeline_atomic | 写盘后 .tmp 不残留 |
| test_save_pipeline_under_lock_concurrent | 50 线程并发写，文件长度合理（不丢更新） |
| test_pipeline_status_idle_when_no_job | 从未跑过 → None |
| test_pipeline_status_reflects_running_state | 启动后 → {state: running, ...} |
| test_run_pipeline_returns_false_when_already_running | 重复 run → False |
| test_stop_pipeline_sets_stop_requested | stop_pipeline 后，job state = stopped |
| test_effective_stop_after_min | per_stage=rough_cut + flow=null → 停在 rough_cut |
| test_effective_stop_after_per_strict | per_stage=rough_compose + flow=rough_cut → 停在 rough_cut |
| test_save_pipeline_records_updated_at | 写盘后 updated_at 字段存在 |

### 7.2 E2E（`work/REQ-20260918-047/_e2e_047.py`）

**前置**：6 阶段产物都有的旧任务（如 `e2e-047-rev`）。

| # | 步骤 | 预期 |
|---|---|---|
| 1 | 打开工作台 | 「⚙ 流程」按钮可见 |
| 2 | 点击「⚙ 流程」 | 抽屉展开，默认 tab「字幕生成」高亮 |
| 3 | 切到「字幕修订」tab | 表单可见「默认接受所有建议」勾选 |
| 4 | 选模板「半自动」 | 5 tab 配置全切换为半自动 |
| 5 | 点「💾 保存配置」 | toast「已保存」 |
| 6 | 点「▶ 从当前节点运行」 | 状态条出现，「当前阶段：字幕修订」，进度条动画 |
| 7 | 等 ~10 秒 | 进度变化或 state 变化（轮询触发 toast） |
| 8 | 模拟 prereq 缺失 | 跳过该阶段 + log 写「跳过」 |
| 9 | 点「⏹ 停止」 | 状态条消失，state=stopped |
| 10 | 重复点「▶ 从当前节点运行」 | 第二次返回 False（已有 running） |

---

## 8. 端到端使用示例

### 用户视角

1. 用户打开任务的剪辑工作台 → 看到「⚙ 流程」按钮
2. 点击 → 抽屉显示默认「人工全审」模板
3. 切换到「字幕修订」tab → 勾选「默认接受所有建议」
4. 切到「粗剪合成」tab → 选 stop_after = 字幕修订（想看完修订再跑）
5. 抽屉底部「总停点」下拉选「粗剪合成」
6. 点「💾 保存配置」
7. 点「▶ 从当前节点运行」 → 状态条出现，进度推到字幕修订阶段
8. 字幕修订完成后自动停止 → toast「已达暂停点（粗剪合成）」
9. 用户去工作台阶段列表点「粗剪合成」看粗剪状态
10. 满意后再回抽屉点「▶」继续

### 服务端视角（pipeline_service.py 调度）

```
T0 run_pipeline("t-001", mgr):
  since = "rough_cut"
  for stage_key in STAGE_ORDER:
    if stage_key < since: continue
    log("开始 stage_key")
    handler = HANDLERS[stage_key]
    success, msg = handler(tid, cfg, logger)
    if not success: state=error; return
    if stage_key == effective_stop_after: state=stopped; return
  state=done
```

---

## 9. 实施步骤

1. **新增 `pipeline_service.py`**：持久化 + 后台调度器 + 5 个 handler
2. **app.py 加 5 个 API**：get/save/run/status/stop
3. **app.py 工作台接入点**：`_render_workbench` 顶部加「⚙ 流程」按钮 + 抽屉挂载 + 状态条挂载
4. **新增 `pipeline.js`**：抽屉渲染 / tabs 切换 / 模板载入 / 表单绑定 / 状态条轮询
6. **CSS**：抽屉 / tabs / 状态条 / 表单样式
7. **单测**：13 用例全过
8. **E2E**：10 步全过 + 不破坏现有 REQ-040-046 的 32/33
9. **commit + push**

---

## 10. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 调度器 / 手动按钮并发抢同一 _JOBS | 现有 service 已有「running 状态拒绝新起」语义，无需新代码 |
| 后台线程异常未捕获导致进程崩溃 | 调度器外层 `try/except Exception` 全兜底 + log |
| pipeline.json 半写损坏 | 复用 execution_history 的 `_write_atomic` 模式 |
| 阶段跨产物依赖破裂（prereq 缺失） | 每个 handler 跑前检查 `os.path.exists`（subtitle.json / revision.json / cutlist.json / rough_compose.mp4）→ 缺失即 skip+log |
| 调度器内调 HTTP 端点阻塞 | 用 `urllib.request` 异步轮询：handler 启动后台 → 每 1s poll status → timeout 24h |