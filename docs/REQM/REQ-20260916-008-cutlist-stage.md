# REQ-20260916-008 — 阶段「粗剪」改名「切分修剪」+ 实现切分修剪阶段（决策→清单，切分子段父编号+子编号）

## 需求原话

> 把阶段中的粗剪改为切分修剪，切分修剪阶段的工作是把前一阶段确定的字幕信息进行进一步的处理，完整保留和语义修正的带到本阶段，删除的不带到本阶段。然后对需要切分修剪的对这段字幕进行内容和切分后文字中的内容进行比较，从音频中切出所要求的文字的字幕时间段，切分一条字幕对应的音频时，保留父级编号，然后对切分出来的每一段加一个子编号。例如原来的音频编号是10，就切分出来之后有三段，就是10.1、10.2和10.3。并且从上一阶段继承过来的序号尽量保持不变，方便对这段音频和文字进行对照查看

（「负极编号」按上下文理解为「父级编号」。）

## 设计

### 阶段改名

- `_WB_STAGES`：`rough_cut` 标题「粗剪」→「切分修剪」，说明改为「按修订决策带入保留/更正段，切分段父编号+子编号」；`fine_subtitle` 说明同步（对切分修剪清单重新生成精确字幕）。
- tasklib（slirn-standalone 仓库）`TASK_STATUS_LABEL[ROUGH_CUT_DONE]`：「粗剪完成」→「切分修剪完成」。
- save_revision 完成 toast：「可进入粗剪」→「可进入切分修剪」。

### 新模块 slirn_home/cutlist_service.py

- 决策语义：手动改判（keep/delete/split/fix）优先；`accept` = 采纳模型 category；`accept` review（无明确处理）→ 防御按整段保留带入（不丢内容）。
- **带入规则**：
  - keep → 整段带入，id = 原编号（字符串），编号继承不变；
  - fix → 整段带入，文本 = 更正后（keep_text，缺失回退原文）；
  - delete → 剔除（编号空洞保留，**不重排** — 方便与修订阶段对照）；
  - split → token 对齐切子段（见下），id = `父编号.子序号`（10 → 10.1 / 10.2 / 10.3）。
- **split 对齐**（核心）：FunASR 的字级 timestamp 与 `tokenize(text)`（上游 `str2list` 同款正则：中文单字、英文/数字连字）一一对应（上游 `generate_srt_clip` 同口径假设）。切分后文字（手动「切分修剪后内容」user_note 优先，回退模型 keep_text）逐 token 在原段 tokens 中**贪心子序列匹配**（最靠前、保序），命中的连续块 = 切分子段：时间取块首 token_ts[0] / 块尾 token_ts[1]，文本 = 原文对应块（音频实际内容，与时间段严格一致）；`ms2srt` 格式与 subtitle.json 一致。
  - 重复语句（「所以我们×3我们看到」→「所以我们看到」）→ 两个子段，重复词只保留第一次出现，子段拼起来 = 切分后文字（内容无损）。
  - **降级**：旧格式字幕无 tokens/token_ts、或两列表不等长（asr 侧就不保存）、或切分后文字完全对不上（用户改写）→ 整段单子段（`fallback` 标记，文本 = 切分后文字，时间 = 整段）。
- 产物 `cutlist.json`：`{version, created_at(溯源修订时间), entries_count, stats{brought/kept/fixed/split_parents/split_subs/dropped/fallback/keep_duration_ms}, items[{id, source_i, kind, sub, start_ms, end_ms, start, end, text, orig_text, target_text, fallback}]}`；`save_cutlist` 补 saved_at，`load_cutlist` 容错读。

### asr_service：字级时间戳落盘

`segments_from_sentences` 在段上追加 `tokens` + `token_ts`（仅当 timestamp 与 token 数等长且数值合法 — 不等长视为不可信不保存，该段切分时降级）。存量 subtitle.json（无 tokens）自动走降级路径，无需迁移。

### 前端（app.py + home.css）

- `_render_cutlist_zone`（rough_cut 面板真实化，原为「规划中」占位）：
  - 引导态：无修订数据 → 「去字幕修订」；未全决策 → 「还有 N/M 条未决策」+「去完成决策」（wb-stage 联动切回）；
  - 预览态（全决策）：服务端**现算不落盘** — 统计行（带入 N 段（保留/更正/切分子段）· 剔除 · 预计保留时长 · 编号继承说明）、介绍、fallback 提示（⚠️ N 条缺字级时间戳已整段带入）、行式清单（与修订列表同列布局：编号|时间|徽章+文本；切分子段缩进+紫竖条，父段前渲染「原段：… → 切分后：…」对照行）、独立播放器 + 点击行定位播放（playCutAt，与修订行同模式）、按钮「✅ 生成切分清单并完成本阶段」「▶️ 播放视频」；已落盘 → 统计行标注「清单已生成（时间）」。
- 端点 `/slirn/api/build_cutlist`：校验（修订存在 / all_decided / subtitle 存在）→ build → 落盘 → `update_status(ROUGH_CUT_DONE)` → toast；前端 `openWorkbench` 全刷新（阶段条切分修剪 → done、精剪字幕 → current）。
- `_wb_stage_states`：rough_cut 增加磁盘判定（cutlist.json 有 items → done），与 subtitle/revision 同模式。
- CSS：`.slirn-cut-list/.slirn-cut-row/.slirn-cut-line`（同 rev-list 网格 44px|216px|1fr）、`.slirn-cut-row.sub`（左缩进+紫竖条）、`.slirn-cut-parent`（灰底对照行）、`.active` 播放高亮、≤900px 响应式。

## 验收标准

1. 阶段列表第 4 阶段显示「切分修剪」，页面无「粗剪」残留。✅
2. 保留（keep）与内容更正（fix）整段带入本阶段，编号继承不变；fix 用更正后文本。✅
3. 删除（delete）不带入；其余段编号不重排。✅
4. 切分（split）按「原内容 vs 切分后文字」对齐，从原段音频切出目标文字时间段；父编号保留 + 子段 10.1/10.2/10.3 编号。✅
5. 修订未完成时本阶段给出引导（含未决策计数），不提供生成入口。✅
6. 生成切分清单落盘 cutlist.json 并推进阶段状态；再次生成幂等覆盖。✅
7. 旧格式字幕（无字级时间戳）不阻塞：整段带入 + 明确降级提示。✅

## 验证记录

- `ruff check` clean；`pytest tests/ -v` → **156 passed**（新增 tests/test_cutlist.py 10 用例：tokenize/join/对齐/连续块/ms2srt、全类别组合清单（编号继承、accept 语义、fix 更正文本、user_note 优先）、重复语句多子段、降级（无 tokens/对不上）、accept review 防御、落盘往返、面板三态渲染、阶段状态磁盘判定；test_asr_service 追加 tokens 保存/不等长不保存；test_workbench 断言更新为切分修剪面板真实化 + 占位 4 个）。
- 浏览器实测（CDP headless，任务 20260915-002 **只读**）5 步全绿：①第 4 阶段「✂️ 切分修剪」current、无「粗剪」残留 ②引导态「还有 389/389 条未决策」+ 去完成决策按钮、无生成按钮 ③去完成决策切回修订面板 ④截图 ⑤零 build_cutlist/save_revision/revise_subtitle 请求（**用户数据零改动、零 LLM 消耗**）。
- 视觉复核（截图 + 视觉模型）：阶段名/高亮态/引导空态/按钮/排列/无重叠溢出 → 5/5 通过。

## 非目标

- 实际音视频文件切割拼接（属后续「精剪字幕/精剪视频」阶段，以 cutlist.json 为输入）。
- 修订阶段反向联动（改决策后自动重算清单 — 用户可手动点「生成切分清单」覆盖）。
- 子段时间的手动微调（本阶段只读预览+确认）。
- 存量任务 subtitle.json 的 tokens 回填（重新生成字幕即可获得）。

## 关联

- 前置：REQ-20260916-006（fix 类别与「切分修剪后内容」输入区）、REQ-20260916-003（决策默认采纳）
- 提交：`feat(home): 粗剪改名切分修剪并实现决策到清单阶段 (REQ-20260916-008)`（funclip-main）+ tasklib 标签（slirn-standalone）
