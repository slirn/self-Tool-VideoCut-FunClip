# REQ-20260915-005 — 处理剪辑·第 2 步：字幕修订（大模型建议 + 人工决策）

| 字段 | 值 |
|---|---|
| 编号 | REQ-20260915-005 |
| 日期 | 2026-09-15 |
| 优先级 | P1（处理剪辑阶段主线） |
| 状态 | ✅ 已完成（验证记录见 §4） |
| 关联 | REQ-20260915-001（字幕生成）；REQ-20260915-003（工作台 8 阶段） |
| 改动范围 | `slirn_home/`（revision_service 新模块 + 工作台修订面板 + 3 个 API + JS/CSS）；tasklib 零改动；funclip/ 零改动 |

---

## 0. 背景

进入剪辑工作台第 3 阶段「字幕修订」。上一阶段（字幕生成）产出 subtitle.json；
本阶段把字幕交给**大模型**逐段分析处理建议（去口癖/口头禅/语气词/重复等无效内容），
用户在建议之上做**逐条手动决策**，形成修订定稿，供后续粗剪使用。

用户原话拆解：
- 大模型判断**每一个时段**的字幕应该如何处理，分析时**带着处理建议和说明**；
- 有的整行去掉、有的完整保留、有的需**进一步切分**（如一句里重复多次的只保留一次）；
- 返回的列表中还要有**用户手动决策类别 + 手动处理说明** — 每一条字幕都齐备这些信息。

## 1. 核心需求

| ID | 需求 |
|---|---|
| REQ-5.1 | 新服务 `revision_service`：读 subtitle.json → 构造提示词 → 调大模型（DashScope qwen，`DASHSCOPE_API_KEY`）→ 防御式解析 → 落盘 `outputs/revision.json`；后台线程 job（与字幕生成同模式），长字幕分批调用 |
| REQ-5.2 | 大模型建议类别 4 类：`keep` 完整保留 / `delete` 整行删除（口癖、口头禅、语气词、无意义、整行重复）/ `split` 切分修剪（行内重复只保留一次、剔除夹杂语气词，附 `keep_text` 建议保留文本）/ `review` 拿不准需人工复核；**每段必有建议 + 具体说明**（模型漏答的段回填 review） |
| REQ-5.3 | 工作台「字幕修订」面板（替换规划占位）：无字幕 → 引导先生成；有字幕无建议 → 分析按钮 + 进度；有建议 → 修订列表，每行 = 原字幕（序号/时间/文本，点击定位播放）+ 模型建议（类别徽章 + 建议 + 说明）+ 手动决策（下拉：未决策/采纳建议/保留/删除/切分 + 手动说明输入）+ 统计条 |
| REQ-5.4 | 保存决策：落盘 revision.json（decision/user_note）；全部行决策完成后任务状态推进 SUBTITLE_REVIEWED；工作台阶段步骤条同步（阶段 done 以 revision.json 磁盘为准，状态序兜底） |
| REQ-5.5 | 重新分析覆盖旧建议（决策重置为未决策），须二次确认 |

## 2. 验收标准

- [x] AC-1 前置校验：无 subtitle.json 时启动分析报「请先生成字幕」；面板显示引导
- [x] AC-2 分析完成后 revision.json 落盘：**条目数 == 字幕段数**，每条含 category + note（split 另含 keep_text）、decision="pending"、user_note=""
- [x] AC-3 修订列表每行同时展示：原字幕信息 + 模型建议（类别徽章+说明）+ 手动决策下拉 + 手动说明输入
- [x] AC-4 保存决策：改动的 decision/user_note 持久化；全部决策完成 → 状态 SUBTITLE_REVIEWED、工作台该阶段 done；未完成可部分保存
- [x] AC-5 job 模式：重复启动不并发（提示已在分析中）；错误（无 API Key / 解析失败）进入 error 态可重试
- [x] AC-6 pytest 全过 + ruff 0 错；E2E（真实 ASR + 真实大模型）+ 浏览器实测

## 3. 非目标

- ❌ 不按建议实际改写字幕文本/视频（粗剪阶段消费决策结果）
- ❌ 不做批量一键采纳（按用户要求逐条手动决策；后续可加）
- ❌ 不改 funclip/ 上游、不新增依赖（dashscope 已在 venv，上游 llm 客户端同款）

## 4. 验证记录（2026-09-15）

### 单元测试
- `pytest tests/ -v`：**94 passed**（新增 `tests/test_revision.py` 10 例 —
  提示词构造 / LLM 输出防御式解析（代码围栏、混排文本、非法 JSON、漏答回填）/
  merge_decisions 应用与跳过 / all_decided / job 落盘（mock LLM，含 split keep_text）/
  job 错误态 / 面板三态渲染 / 阶段状态联动）
- `ruff check .`：0 错误

### E2E（真实 ASR + 真实大模型 qwen-plus，任务 20260915-011）
- 素材：speech_2min.mp4 截取 05–25s（20s），热词「训练营 大家好」
- AC-1：无字幕启动分析被拒（`请先生成字幕`），工作台面板显示引导 ✅
- 真实 ASR：6 段字幕 → 真实 qwen 分析（1 批次），分布 **delete:2 / keep:3 / split:1**
- AC-2：revision.json 6 条 == 段数，每条 category+note+decision=pending+user_note=""，
  split 条目含非空 keep_text；真实建议样例（摘自 20260915-011）：
  - `#5 [delete]「嗯」→ 典型单字语气词，无实际语义，纯停顿填充，符合"纯口癖、语气词"删除标准，应整行删除`
  - `#3 [split]「我们稍等一下」→ 附 keep_text="我们稍等一下"（建议保留文本单独成字段）`
- AC-5：分析中重复启动被挡（`⏳ 该任务已在分析中`）；已有建议无 force 重分析被拒 ✅
- AC-3：工作台 HTML 断言 6 行 ×（原字幕 + 徽章建议 + 决策下拉 + 手动说明）✅
- AC-4：混合决策 [accept/keep/delete/split 循环 + 1/3 带手动说明] 保存 →
  toast `✅ 已保存（生效 6 条 · 已决策 6/6）· 字幕修订完成，可进入粗剪`，
  磁盘 decision/user_note 逐一吻合，metadata status → `SUBTITLE_REVIEWED`，
  工作台前三阶段 done、current=粗剪、修订面板「已决策 6/6」 ✅

### 浏览器实测（CDP headless Chrome，同任务）
- 打开工作台：done=3、current=✂️ 粗剪；切「字幕修订」阶段 → 面板可见，
  行/徽章/下拉/说明输入各 6，统计条含 6/6，保存与重分析（二次确认）按钮齐备
- 行点击（原字幕区）→ 播放器浮现，src=`/slirn/api/video/20260915-011`
- 改判 #1 为「整行删除」+ 手动说明「浏览器实测：改判删除」→ 保存 →
  工作台刷新；**磁盘侧复核** revision.json entry#1 decision=delete、note 吻合 ✅
- 截图存档：`work/REQ-20260915-005-subtitle-review/_shot_revision_list.png`、
  `_shot_revision_saved.png`

### 遗留说明
- 重新生成字幕（阶段 2）后旧 revision.json 不会自动失效 — 用户需在修订面板
  「🔄 重新分析」重建（有二次确认）。粗剪阶段消费时以 entries 与 subtitle 段数一致性为准。
- tasklib 零改动（update_status 已支持 SUBTITLE_REVIEWED）→ 本次仅提交 funclip-main，
  无子模块变更。
