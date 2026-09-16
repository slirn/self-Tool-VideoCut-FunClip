# REQ-20260916-017 — 精剪修订：大模型热词替换（统计 / 高亮 / 过滤 / 确认）

## 需求原话

> 下一阶段是精简修订，这块主要是对切分修剪阶段完成之后的字幕再通过大模型进行。分析其中一些错误的文字，这时可以通过热词再跑一遍，判断字幕中哪些应该被替换掉，即用热词替换不准确的字幕文字。在这里可以看到任务里边添加的热词，执行精简字幕之后，要统计出来有哪些词被替换掉了，出现频率是多少，并在字幕文字中用特殊颜色进行标识，用来用户确认，同时也可以过滤出有替换热词的字幕记录，方便直接查看大模型替换的正确性

## 实现

原第 6 阶段「精剪修订」占位（仅阶段表条目）按热词替换语义真实化。

| 改动 | 说明 |
|---|---|
| `slirn_home/fine_service.py`（新建） | `FINE_SYSTEM` 提示词（只处理热词相关误识别、before 必须逐字摘自行文本、宁缺勿错）；`parse_replacements` 防御解析（摘抄幻觉丢弃、编造热词从 after 回填、after 不含热词丢弃、重叠替换剔除）；`apply_replacements`（附 pos 供精确高亮）；`start_job`/`job_status` 后台线程（对齐 revision_service 范式：分批 40 行、批间 0.6s、解析失败减半重试、进度 stage+progress）；`save_decisions`（撤销决定全量落盘 + saved_at 完成标记）；`word_stats` / `effective_stats` |
| 行集口径 | `effective_keep_units(build_cutlist(...))` — 与切分面板/粗剪合成完全同源：**切分修剪之后的字幕** = 执行口径保留行（含 split 子段，文本=fix 更正/切分对齐文本） |
| LLM 通路 | 复用 `revision_service._call_llm`（用户 ⚙️ 注册的当前模型，OpenAI 兼容/Anthropic 双协议，Key 只读环境变量） |
| `_render_fine_review_zone` | 守卫链（无修订/未决策/无字幕/无热词 → guide 按钮，无热词引导去素材准备）；说明态（保留行数 + 当前模型 + **任务热词 chips** + 分析按钮）；结果态（统计行 + **替换热词频次表**（撤销不计）+ 行列表 + 过滤按钮「只看有替换的行（K/N）」+ 确认保存/重新分析）；过期黄条（切分/修订决策 newer than 分析时间） |
| 高亮 | 行内 `<mark class="slirn-fw-mark">`（琥珀色）标替换处，`title` 悬浮显示原文；已撤销行显示原文（不高亮）+ 置灰 |
| 撤销确认 | 每行「↩️ 撤销替换 / 已撤销 · 恢复」纯前端切换（live/orig 双容器 CSS 切换）；「✅ 确认替换结果」全量提交 reverted_ids → 落盘 + 幂等推进 `FINE_SUBTITLE_REVIEWED` |
| 行定位播放 | 独立播放器 `#slirn-fine-player` + `bindSpeedControl` 倍速（REQ-20260916-014 同款）— 听原声核对替换是否正确 |
| 端点 | `POST fine_revise`（守卫链 + force 二次确认）/ `fine_revise_status`（轮询 + 服务重启产物在即 done）/ `save_fine_revision`（撤销落盘 + 状态推进） |
| CSS | `.slirn-fw-mark`（琥珀高亮）、`.slirn-fw-word`（频次 pill）、`.slirn-fw-row`（grid 行 + reverted/only-replaced 态）、列表 max-height 560 滚动 |

阶段状态：`fine_revision.json` 存在且 `saved_at`（确认保存）→ done；已分析未确认 → current 停留提示。

## 验证

- `pytest tests/ -q` → **175 passed**（新增 13 个纯函数用例：归一拒绝/回填、重叠剔除、pos、防御解析、prompt、统计撤销排除、落盘幂等）；`ruff check .` → clean。
- **离线全流程**（`work/REQ-20260916-017-fine-review/_service_test.py`，monkeypatch fake LLM 走 start_job 真实代码路径）：摘抄幻觉/编造热词/after 不含热词/未知 id 全部正确过滤；单批调用；entries 只含合法三行；撤销保存后统计正确剔除。
- **E2E 只读**（CDP headless，chrome_cdp_035/9352）七项全过：阶段条描述更新；说明态（1681 行 + 模型 + 9 个热词 chips + 按钮）；save 端点无结果正确报错；status idle；粗剪合成/切分面板不受扰；截图；**零写请求**（fine_revise/save_fine_revision 不在其中）。

## 边界说明

- 替换结果独立落盘 `fine_revision.json`，不改写 subtitle.json/revision.json 原文（可随时撤销/重分析）；后续精剪视频/字幕合成阶段按「未撤销的 new_text」取文本。
- 模型整行改写不可能通过校验（before 必须逐字子串 + after 必须落在热词表上）。

## 关联

- 前置：REQ-20260916-011（切分执行口径——行集来源）、REQ-20260915-005/007/008（LLM 调用/重试/注册表——直接复用 `_call_llm`）
- 提交：`feat(home): 精剪修订大模型热词替换——统计/高亮/过滤/逐处确认 (REQ-20260916-017)`
