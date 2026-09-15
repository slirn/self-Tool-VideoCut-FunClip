# DESIGN-20260915-005 — 字幕修订服务（大模型建议 + 人工决策）

## 1. 关键决策

| 决策 | 选择 | 理由 |
|---|---|---|
| LLM 提供方 | DashScope `Generation.call`（默认 `qwen-plus`，`SLIRN_LLM_MODEL` 可覆盖） | 本机已配置 `DASHSCOPE_API_KEY`；与上游 `funclip/llm/qwen_api.py` 同款 SDK，不新增依赖 |
| LLM 客户端复用 | 不 import 上游 `qwen_api.py`，自写 ~15 行 | 上游函数会 `print` 整个 response 且无超时/重试；自写可控制 json 输出与异常语义（上游零改动原则不限制 import，但其实现不适合直接用） |
| 服务归属 | `slirn_home/revision_service.py`（funclip-main），不进 tasklib | 与 REQ-001 的 asr_service 同构：阶段服务在 slirn_home，tasklib 只管任务模型；本需求 tasklib 零改动（update_status 已支持 SUBTITLE_REVIEWED） |
| revision.json 自包含 | 每条 entry 快照字幕文本/时间 | 列表渲染不需 join subtitle.json；模型漏答的段也要回填 review（用户要求每条都有建议） |
| 决策状态推进 | 全部行 decision ≠ pending 才 update_status(SUBTITLE_REVIEWED) | 「修订完成」语义 = 人工逐条拍板；部分保存允许中途离开 |
| 长字幕分批 | 40 段/批顺序调用，job 阶段显示「调用大模型 (k/n)」 | qwen-plus 上下文/输出长度安全边际；字幕通常几十~几百段 |
| 解析防御 | 剥 ```json 围栏 → json.loads → 逐条校验；未知类别→review；漏答段→review("模型未返回该段") | 大模型输出不可信假设；保证 AC-2「条目数==段数」恒成立 |

## 2. 数据结构 — `outputs/revision.json`

```json
{
  "version": 1,
  "model": "qwen-plus",
  "created_at": "2026-09-15T12:00:00",
  "saved_at": null,
  "segments_count": 8,
  "entries": [
    {"i": 1, "start_ms": 0, "end_ms": 1800, "start": "00:00:00.000", "end": "00:00:01.800",
     "text": "嗯嗯大家好", "category": "split", "keep_text": "大家好",
     "note": "行首连续两个「嗯」为语气词，建议剔除",
     "decision": "pending", "user_note": ""}
  ]
}
```

类别表：
- 模型建议 `category`: `keep` 保留 / `delete` 整行删除 / `split` 切分修剪(含 keep_text) / `review` 人工复核
- 手动决策 `decision`: `pending` 未决策 / `accept` 采纳建议 / `keep` / `delete` / `split`

## 3. API（3 个，全 POST `/slirn/api/*`）

| 路由 | 入参 | 行为 |
|---|---|---|
| `revise_subtitle` | task_id | 前置 subtitle.json 存在；start_job（已在跑→toast 提示）；body 可带 `force` 跳过「已有建议」确认（JS 二次确认后传） |
| `revise_status` | task_id | 内存 job 优先；无 job 看 revision.json → done+统计；否则 idle |
| `save_revision` | task_id, decisions:[{i,decision,user_note}] | 合并落盘（非法类别拒绝该条）；全部非 pending → SUBTITLE_REVIEWED；返回 workbench 内层 HTML 刷新阶段条 |

## 4. UI — 工作台 `subtitle_review` 面板（`_render_revision_zone`）

三态：
1. 无 subtitle.json → 🚧 引导 + 「跳去生成字幕」按钮（wb-stage 切面板）
2. 有字幕无建议 → 说明卡（分析什么/4 类建议含义）+ `🤖 大模型分析字幕`（有旧建议时改「🔄 重新分析」+ JS confirm）
3. 有建议 → 统计条（N 段 · 建议分布 · 已决策 x/N）+ 播放器 + 列表 + `💾 保存修订决策`

行结构（每行 2 列 grid：原字幕 | 建议+决策）：
```
#3  00:00:12.300 → 00:00:15.100          [切分✂️]  建议保留：「大家好」
嗯嗯大家好大家好，今天我们开始…            说明：连续「嗯」为语气词；「大家好」重复2次保留1次
[决策: 未决策▾] [手动说明: ____________]
```

- 行点击 → 修订区播放器定位播放（独立 id：slirn-rev-player，与字幕区播放器互不干扰；全局委托加 `.slirn-rev-row` 分支）
- job 轮询 revPollTimer（2s）仿 startSubPolling；完成 → openWorkbench(tid) 刷新阶段态
- 徽章配色：keep 绿 / delete 红 / split 紫 / review 橙；decision 下拉同色系

## 5. `_wb_stage_states` 变更

`subtitle_review` 阶段：done = revision.json 有 entries（磁盘优先）或 rank ≥ SUBTITLE_REVIEWED — 与 subtitle 阶段同模式（服务器重启后内存丢失兜底）。

## 6. 测试策略

- 单元（mock LLM，不打网络）：parse 防御 ×4、决策合并/非法值、渲染三态、阶段状态推进
- E2E（真实链路）：新建任务 → 真实 ASR → 真实 qwen 分析 → 断言 AC-1..4 → 保存混合决策 → 状态推进
- 浏览器 CDP：面板可见、行内徽章/下拉/输入、点击定位播放、截图
