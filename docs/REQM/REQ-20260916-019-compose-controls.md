# REQ-20260916-019 — 粗剪合成面板增强：删除成片 + 有效字幕预览

## 需求原话

> 粗剪合成功能还是不对，现在功能这么改，首先你添加删除合成后视频的功能，然后添加把切分修剪之后的有效的字幕信息单独列出来，按照字幕的格式先显示出来，我要查看。然后再进行后续的工作

## 背景

REQ-20260916-018 用上游 VideoClipper 方法合成粗剪成片后，用户需要：

1. **删除粗剪成片** — `rough_compose.mp4`（REQ-016 旧产物）+ `rough_compose.srt`（REQ-018 副产物）
   一旦生成就只能等下次合成覆盖，无法主动清理。
2. **查看切分之后保留的有效字幕** — 在粗剪合成面板内联 SRT 预览，让用户在送入下游阶段
   之前看到「将要进入精剪合成/字幕合成」的字幕究竟是什么。

完成后用户才能往下推进精剪视频/字幕合成阶段。

## 实现

### 改动一览

| 改动 | 说明 |
|---|---|
| `slirn_home/compose_service.py`（新增 3 函数） | `_ms_to_srt_time(ms)`（整数毫秒 → `'HH:MM:SS,mmm'`）；`format_srt(units, text_overrides)`（保留行 → SRT 字符串，按 `start_ms` 升序 1-based 编号，`text_overrides[id]` 优先于 `unit.text`，空 units → ""）；`delete_rough_compose(outputs_dir)`（同步删除 mp4 + srt 副产物，毫秒级不进 job 表，返回 `{deleted, removed, remaining, message}`） |
| `slirn_home/app.py`（新增 2 端点 + 面板 + JS） | `compose_rough_delete`（同步删产物，返 `{deleted, toast, removed, remaining, message}`）；`compose_rough_preview_subs`（同源复用 `compose_rough` 的 `units` + `fmap` 计算，传给 `compose_service.format_srt`，返 `{srt, lines, keep_ms}` — 守卫链与合成端点同款：`rev` 必须决策完毕 + `sub_meta` 存在 + `units` 非空）；`_render_rough_compose_zone` 内嵌 `<details class="slirn-rc-subs" open>` 折叠块 + `<pre>` 文本节点（HTML 转义防 XSS）；JS dispatch 加 3 分支（`compose-rough-delete` 二次 confirm → 调端点 → `openWorkbench` 刷新；`compose-rough-subs-copy` 走 `navigator.clipboard` + `execCommand` 兜底；`compose-rough-subs-download` POST `preview_subs` 拿 SRT → Blob 下载） |
| `slirn_home/static/home.css`（新增 1 规则块） | `.slirn-rc-subs` / `.slirn-rc-subs > summary`（▸/▾ 自定义折叠图标，禁用默认 marker）/ `.slirn-rc-subs-body`（等宽字体 + max-height 360 滚动 + 黑底浅色） |
| `tests/test_compose_service.py`（新增 9 用例） | `_ms_to_srt_time` 基础/小时；`format_srt` 空/排序/乱序输入/text_overrides；`delete_rough_compose` 不存在/只 mp4/同时存在 → 196 passed |
| `work/REQ-20260916-019-compose-controls/` | `_service_test.py`（format_srt 演示 + 临时目录删除 + 任务 001 真实 1681 行 → 75KB SRT 离线实测）；`_e2e_test.py`（chrome_cdp_037/9354 headless：SRT 渲染/删除按钮/复制下载按钮/端点守卫/产物未动/其他面板/截图/零写 七项全过） |

### 行集口径复用

粗剪合成面板的 SRT 来源：**服务端 `_render_rough_compose_zone` 同步算 SRT 内嵌 HTML `<pre>`**，不复跑 `build_cutlist + effective_keep_units`。与 `compose_rough` 端点用同一份 `units`（同源），加 `fmap`（`{id → new_text}`，热词替换未撤销行）后传给 `compose_service.format_srt`。前端 `<pre>` 文本节点赋值（先 HTML escape 三字符），不解析为 HTML — 字幕修订/热词替换产物安全。

### 删除按钮二次确认

原生 `confirm()` 同步弹出，文案：「删除「粗剪成片」（mp4 + 随片 srt 副产物）？删除后需要重新合成才能预览效果（约 11 分钟）。」成功后调 `openWorkbench(tid)` 全刷新（删按钮消失、mp4 预览消失、SRT 预览保留 — 因为字幕清单不依赖产物）。

### SRT 字符串来源设计权衡

- **选 A（推荐）**：服务端 `_render_rough_compose_zone` 同步算 SRT → 内嵌 HTML `<pre>`
  - 优点：刷新工作台即显示，无额外 fetch 延迟；与原版面板「服务端算好内嵌」范式相同
  - 缺点：HTML 体积变大（任务 001 实测：1681 行 → 74883 chars ≈ 75KB）
  - **接受**：Gradio 内嵌页面仍可接受，`<pre>` 用 `max-height: 360` + 滚动
- 选 B（拒绝）：端点按需返回 + 前端 fetch
  - 缺点：与现有面板「内嵌」范式不一致；首次进入面板多一次网络往返

## 验证

- `pytest tests/ -q` → **196 passed**（新增 9 个：`format_srt`(5) + `_ms_to_srt_time`(2) + `delete_rough_compose`(3)；原 187 → 196）
- `ruff check slirn_home/ tests/` → clean
- **离线** (`work/REQ-20260916-019-compose-controls/_service_test.py`)：
  - `format_srt` 演示单元：3 行（热词替换 `神精网络` → `神经网络`），升序 1-based
  - `delete_rough_compose` 临时目录兜底：都不存在 / 只 mp4 / mp4 + srt
  - **任务 001 真实数据**：1681 行 → 74883 chars SRT / 5ms 耗时；前 10 段落 `_task001_subs_sample.srt` 人工核对
- **服务重启**（netstat 7862 PID → taskkill → PowerShell Start-Process detached `funclip/launch.py --home slirn -p 7862 --listen`）→ HTTP 200
- **端点最小 curl 验证**：
  - `compose_rough_delete {}` → `{"ok":false,"error":"缺少 task_id"}`（守卫）
  - `compose_rough_preview_subs {task_id:"20260915-001"}` → `{"ok":true,"srt":"1\n00:00:00,950 --> 00:00:04,270\n有点回音好像还有吗\n\n...","lines":1681}`
- **E2E 只读**（chrome_cdp_037/9354 headless）七项全过：
  1. 面板 `.slirn-rc-subs` 渲染 + `<pre>` 含 1681 行 74883 chars SRT，含 `00:00:00,950 --> 00:00:04,270` 毫秒时间戳
  2. 删除按钮 `🗑️ 删除粗剪成片` + `slirn-btn-danger` 类（仅断言存在，**绝未点击**）
  3. 复制 + 下载按钮（`compose-rough-subs-copy` / `compose-rough-subs-download`）渲染
  4. `compose_rough_delete {}` 端点守卫 → `缺少 task_id`
  5. `compose_rough_preview_subs {tid}` → ok + 1681 行 + SRT 与内嵌一致
  6. 产物未动（`<video>` 仍指 `/slirn/api/video/20260915-001?src=rough_compose`，mp4 + srt 仍在）
  7. 其他面板按钮齐全（rcCompose / fineRevise / cutSave / cutRebuild） + 截图 + 零写请求（仅 `workbench` + `video/20260915-001` 两条 READ）

## 边界说明

- **行集口径**：与 `compose_rough` 完全同源（修订实时 + 已保存手工翻转/改判）— 上游合成与本地预览计算结果一致
- **文本来源**：保留行文本取 ASR 行文本，热词替换未撤销行取 `new_text`，其余取 `unit.text`
- **删除可见性**：仅 `rough_compose.mp4` 存在时显示；防删半成品（有 running job 时也不显示，但合成前端流程已禁合成按钮 + 后端守卫生效）
- **SRT 体积**：任务 001 75KB / 1681 行 — `<pre>` 加 `max-height: 360` 滚动条不阻塞页面渲染
- **HTML 注入**：`format_srt` 输出转义 `&`/`<`/`>` 三字符后嵌入 `<pre>` 文本节点 — 与上游合成 mp4 时直接用 ASR 原始文本同安全等级
- **下载文件名**：`rough_compose_subs_<tid>.srt`，带 task_id 便于多任务区分
- 不改 funclip/ 上游源码；不引入新依赖；任务数据只读

## 关联

- 前置：REQ-20260916-016（粗剪合成可选阶段）、REQ-20260916-018（粗剪合成改用上游 VideoClipper 合成方法，副产物 `rough_compose.srt`）
- 下游：精剪视频合成阶段（待规划）— 需要拿到粗剪的 `rough_compose.mp4` + 字幕清单（即本 REQ 的 SRT 预览）才能继续
- 提交：`feat(home): 粗剪合成面板增强——删除产物 + 有效字幕 SRT 预览 (REQ-20260916-019)`