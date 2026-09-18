# REQ-20260917-033 — 关联人员ID 持久化 + 统计口径改为「仅计未删除记录」

| 字段 | 值 |
|---|---|
| 编号 | REQ-20260917-033 |
| 日期 | 2026-09-18 |
| 优先级 | P2（切分修剪阶段能力增强） |
| 状态 | ✅ 已完成（单元 + 真实浏览器 E2E 全过） |
| 关联 | REQ-20260917-031（关联人员ID 首版：即时计算不落盘）；REQ-20260917-029（说话人分离） |
| 改动范围 | `slirn_home/`（cut_speaker.py + app.py 渲染/端点 + router.js + CSS 微调）；tasklib 零改动；funclip/ 零改动 |

---

## 0. 背景

REQ-031 的关联是**即时计算不落盘**：刷新页面/重进任务后徽章和统计条消失，要重新点
「👤 关联人员ID」。统计口径是 总数/保留/已删 三列，用户要自己心算"删干净没有"。

用户反馈两个诉求：
1. **关联信息要保存下来** — 执行关联后，重进任务时徽章 + 统计条直接在场；
2. **统计只计未删除记录** — 删除状态的不进统计范围。目的：把非主讲（非主要人员）
   的字幕删掉后，重算统计即可确认"这个人已经清零"，剩余计数 = 该人员还会进成片的条数。

## 1. 核心需求

| ID | 需求 |
|---|---|
| REQ-1.1 | 点击「关联人员ID」成功后，关联状态**落盘**（outputs/speaker_link.json） |
| REQ-1.2 | 再次打开切分修剪面板时，若已关联：行上 👤 徽章 + 人员统计条**直接渲染**，无需再点按钮；按钮显示「🔄 重新关联人员ID」 |
| REQ-1.3 | 统计口径：每个人员只统计**未删除**（执行口径保留）的记录数；删除状态（整段改判删除 / 切分子段标记删除 / 修订删除洞）**不计入**统计 |
| REQ-1.4 | 「🧮 重新统计」按当前 DOM 去留状态用新口径重算（未保存的改判也计入） |
| REQ-1.5 | 已删光的人员在统计条显示 0 条（确认清零的反馈），仍可查找/导航其记录以便翻回 |
| REQ-1.6 | 数据兼容：subtitle 重新生成后若已无 spk → 渲染时静默跳过关联（面板回普通态，点按钮给既有引导） |

## 2. 设计要点

- **落盘内容**：`outputs/speaker_link.json` = `{version:1, enabled:true, linked_at, rows:{id:spk}, stats:[...]}`。
  渲染时**不直接用快照**，而是 enabled=true 时用与端点完全相同的纯函数
  `link_speakers(sub_meta, cutlist)` **现算**（对齐本身是确定性的，快照仅供人工检查）——
  修订/清单变化后行号变了也不会错位；subtitle 变更导致 available=False 时自动退回普通态。
- **统计条渲染**：服务端直接产出与 `cutSpkBarRender()` 相同结构的 HTML（chips 文案
  `👤N · K 条`），容器带 `data-linked="1"`；chips 点击改走事件委托
  （`data-action="cut-spk-chip"` + `data-spk`），服务端渲染与 JS 重渲染行为一致。
- **Python stats 新形状**：`[{spk, count}]`（count=未删除行数），废弃 total/kept/deleted。
- JS `cutSpkStats()` 同步改口径：只数 `cutRowKept(row)` 为真的行。

## 3. 验收标准

- [x] AC-1 点击关联成功后 `outputs/speaker_link.json` 落盘（enabled=true）
- [x] AC-2 刷新/重进任务 → 切分修剪面板徽章 + 统计条直接在场，按钮为「重新关联」，无需点击
- [x] AC-3 chips 只显示未删除计数；切分删除洞、改判删除的行不计入
- [x] AC-4 删除某人员全部记录 → 重新统计后该人员显示 0 条；逐条翻回后计数恢复
- [x] AC-5 保存切分决策后重进面板，统计反映已保存的删除改判（被删人员计数不含这些行）
- [x] AC-6 未开启说话人的旧任务零回归（无 speaker_link.json → 面板与 REQ-031 前一致）
- [x] AC-7 单元测试更新 + 新增（落盘往返、渲染持久态、口径）；ruff 0 错；全量 pytest 通过
- [x] AC-8 真实浏览器 E2E：关联 → 刷新持久 → 删除人员 → 重算 0 条 → 翻回重算 → 保存 → 再刷新统计正确

## 4. 口径变更说明（相对 REQ-031）

REQ-031 统计 = 总数/保留/已删（删除洞计入「已删」）。本需求把统计简化为
**仅计未删除**：删除洞与任何删除改判一样完全不计入。统计语义从「这个人说了多少」
变为「这个人还有多少条会进成片」——直接服务于"确认非主讲人员已清除"。

## 5. 验证记录

**2026-09-18（实现 + 单元 + 真实浏览器 E2E）**

- 实现：`cut_speaker.py` stats 改 `[{spk, count}]`（count=未删除行数；有行即入表，
  全删光显示 0 条）+ `save_link`/`load_link`（outputs/speaker_link.json，坏文件/
  enabled=False 容错回 None）；端点 `cut_speaker_link` 成功即落盘并回 `linked_at`；
  `_render_cutlist_zone` enabled → 现算对齐（与端点同口径，快照备查；subtitle 已无
  spk 时静默回普通态）→ 行上直接渲染 `data-spk` + 👤 徽章、统计条服务端渲染
  （容器 `data-linked="1"`，chips 走 `data-action="cut-spk-chip"` 事件委托）、按钮变
  「🔄 重新关联人员ID」；router.js `cutSpkStats`/`cutSpkBarRender` 同步新口径
  （chips `👤N · K 条`，全删光 0 条），新增 `cut-spk-chip` 委托分支，删除/关联 toast
  文案更新。
- 质量：`ruff check slirn_home/ tests/` 0 错；`pytest tests/ -q` **249 passed**
  （更新 stats 断言 + 新增 `test_save_and_load_link` 往返、
  `test_render_cutlist_zone_persists_link` 持久态渲染 + 无 spk 回退、端点落盘断言）。
- 修过一个实现偏差：初版 counts 只在保留行时建条目 → 全删光人员从统计消失；
  改为有行即入表（单测先抓到，JS 同步修）。
- AC-8（真实浏览器 E2E，任务 20260918-003/004，CDP 真实点击）：
  - 关联：7 行徽章 + chips `👤1 · 4 条`（1.1 删除洞不计入）/`👤2 · 2 条`，
    speaker_link.json 落盘（enabled=true）；
  - **整页刷新后不点按钮**：linked=1、7 徽章、chips 一致、按钮「重新关联」（AC-2）；
  - chip 点击（委托）→ 查找框填 2；
  - 删除 spk2 → `👤2 · 0 条`（清零反馈）；翻回行3 + 重新统计 → `👤2 · 1 条`；
  - 保存 → `actions={'5':'delete'}`、关联文件仍在；再刷新 → `👤1 · 4 条 | 👤2 · 1 条`
    （反映已保存改判）；
  - 无 spk 任务（20260918-004）：统计条隐藏 + 引导 toast + 不落盘。
- 脚本：`work/REQ-20260917-033-speaker-persist/_e2e_spkpersist.py`（gitignored），
  截图 `_shot_spkp_*.png` 同目录（Windows GBK 控制台跑含 emoji 的 E2E 需
  `PYTHONIOENCODING=utf-8`）。
