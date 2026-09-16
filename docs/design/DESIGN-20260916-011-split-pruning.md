# DESIGN-20260916-011 — 切分修剪阶段：完整时间轴切分算法（第二步）

> 关联 REQ：REQ-20260916-008（切分清单阶段）、REQ-20260916-011（本 REQ 的 bug 修复部分）
> 算法载体：`slirn_home/cutlist_service.py`（纯函数，已含 tokenize / align_tokens / contiguous_blocks）

## 1. 需求原话

> 第二步是针对切分修剪的字幕根据修剪前和修剪后的文字比较决定这段。字幕所对应的时间段要切分成多段，切分之后把每一段也做上相关的建议标记，例如是保留还是删除，基本就这两种状态。通过这种切分修剪，可以把这一段字幕中的字或者是词在视频上这个时间段的时间截出来，可以手工删掉，删掉不需要的。针对整段字幕拆分之后的细分字幕，它的序号才是加上点1.2.3这样的，同时，原来的序号10还是存在的，作为父级。而这个父级切分之后的各个子段是十点一十点二十点三。

第一步（决策到清单）已在 REQ-20260916-008 完成：修订阶段决策为「保留/内容更正」的整段带入，「删除」的不带入，「切分」的按修剪后文字切子段。**本设计只解决第二步**。

## 2. 现状与差距

当前 `build_cutlist` 对 split 段的处理（`cutlist_service.py:167-201`）：

```
原文 tokens（字级时间戳）        修剪后文字
大 家 好 今 天 我 们 来 讲  →   "今天我们讲这个话题"
一 下 这 个 话 题
    ↓ align_tokens（贪心子序列匹配）
命中索引 3,4,5,6,8,9
    ↓ contiguous_blocks
保留块 (3,6)=今天我们来讲  (8,9)=这个话题
    ↓ 只产出这两个块
子段：10.1 今天我们来讲   10.2 这个话题        ← 现状只到这里
```

**差距**：被剪掉的字/词（"大家好"、"一下"）是隐式空洞——清单上看不到它们、没有编号、没有时间段。用户要求的是**完整划分**：父段时间轴全部切成子段（含删除部分），每段有编号（父.N 按时间序）+ 双态建议标记（保留/删除），并能手工翻转标记，从而实现"把某个字/词的时间段截出来、手工删掉"。

## 3. 核心逻辑（先把逻辑讲清楚）

一句话：**用修剪后文字在原文字里做"划线对照"——划中线的进保留组，没划中的进删除组，然后按时间顺序把原段的所有字不重不漏地切成交替的子段。**

三步：

1. **对齐**（已有，不变）：修剪后文字 tokenize 成 token 序列（中文单字/英文连字），在原段 token 序列里做**贪心子序列匹配**（保序、不回溯）。修剪本质是"原文子集 + 去语气词/重复"，子序列匹配足够；用户改写导致对不上的 token 自然落进删除洞，行为可预期。
2. **完整划分**（新增）：命中索引聚成连续块（保留块）；保留块之间的间隙（含首尾）就是删除洞。原段全部 token 被分成 **keep 块 / delete 洞交替**，不重不漏。
3. **时间边界**（新增规则）：keep 块时间 = 块内首 token 起点 → 尾 token 终点（现状口径不变）；**delete 洞采用外扩边界**——洞起点 = 前一 keep 块尾 token 的终点（首洞用父段 start_ms），洞终点 = 后一 keep 块首 token 的起点（尾洞用父段 end_ms）。

为什么洞要外扩：keep 段定义不动、父段时间轴被 keep/delete **完全二分**（相邻段共享边界、无重叠无缝隙）；删掉所有 delete 段后 keep 段自动首尾紧贴——这正是"把那段时间从视频上剪掉"的语义。token 之间的静默间隔也随洞一起删掉，不会残留在成片里。

**编号**：按时间顺序父.1、父.2、父.3…**不分标记**（10.1 可以是删除段）。翻转某段标记不改变任何编号——编号是位置，标记是决策，两者解耦，用户手工调整时不会"一删编号全变"。

**建议标记（mark）推导**：块来自命中 → 建议 `keep`；洞 → 建议 `delete`。即模型建议的修剪文本决定初始标记，用户手工翻转后以手工为准。

## 4. 算法与代码

新增一个纯函数（复用现有 `contiguous_blocks`）：

```python
def partition_tokens(n_tokens: int, keep_marks: list[int]) -> list[tuple[str, int, int]]:
    """命中索引 → 完整交替划分 [(mark, 首 token 索引, 尾 token 索引), …]。

    覆盖 [0, n_tokens) 不重不漏：keep 块（命中连续块）与 delete 洞
    （块间/首/尾的间隙）交替；编号阶段直接按本列表顺序产 父.N。
    """
    parts: list[tuple[str, int, int]] = []
    cur = 0
    for a, b in contiguous_blocks(keep_marks):
        if a > cur:
            parts.append(("delete", cur, a - 1))
        parts.append(("keep", a, b))
        cur = b + 1
    if cur < n_tokens:
        parts.append(("delete", cur, n_tokens - 1))
    return parts
```

时间边界换算（`build_cutlist` 的 split 分支改造）：

```python
segs = partition_tokens(len(orig_tokens), align_tokens(orig_tokens, tokenize(target)))
seg_start_ms, seg_end_ms = int(seg.get("start_ms", 0)), int(seg.get("end_ms", 0))
prev_e = seg_start_ms                       # 前一段的终点（初始=父段起点）
for k, (mark, a, b) in enumerate(segs, start=1):
    if mark == "keep":
        s_ms, e_ms = token_ts[a][0], token_ts[b][1]           # 现状口径
    else:
        # 外扩：向前贴住前一段的终点、向后贴住后一段 keep 块的起点
        s_ms = prev_e
        e_ms = token_ts[b + 1][0] if b + 1 < len(token_ts) else seg_end_ms
    s_ms = max(s_ms, prev_e)                # ts 非单调防御
    if e_ms - s_ms > 0:                     # 零长洞（相邻 token 无缝）不产段
        items.append({
            "id": f"{i}.{k}", "source_i": i, "kind": "split", "sub": k,
            "mark": mark,                   # ← 新增字段（keep/delete）
            "start_ms": s_ms, "end_ms": e_ms,
            "start": ms2srt(s_ms), "end": ms2srt(e_ms),
            "text": join_tokens(orig_tokens[a:b + 1]),
            "orig_text": str(seg.get("text", "")), "target_text": target,
            "fallback": False,
        })
        prev_e = e_ms
```

## 5. 实例推演（原编号 10）

父段 10：`大家好 今天 我们 来讲 一下 这个 话题`（8 个 token，各带字级时间戳），段边界 [10.0s, 19.6s]。
模型建议修剪后文字：`今天我们这个话题`（删"大家好"和"来…讲"…此处假设命中 `今天 我们 这个 话题` = 索引 3,4,6,7）。

| 子段 | 标记 | token | 时间（示意） | 文本 |
|---|---|---|---|---|
| 10.1 | ❌ delete | 0–2 | 10.0 → 13.2 | 大家好 |
| 10.2 | ✅ keep | 3–4 | 13.2 → 15.0 | 今天 我们 |
| 10.3 | ❌ delete | 5 | 15.0 → 16.1 | 来讲 一下 |
| 10.4 | ✅ keep | 6–7 | 16.1 → 18.4 | 这个 话题 |

- 父编号 10 保留（清单按父段分组，组头显示原文与建议摘要）；子段 10.1–10.4 按时间序编号，delete/keep 交替，边界共享（10.2 的起点 = 10.1 的终点）。
- 成片 = 全部 ✅ 段按时序拼接：13.2–15.0 + 16.1–18.4，字词间静默随洞删净。
- 用户手工操作示例：发现"今天 我们"里"我们"也想去掉 → 后续版本支持段内细分（M3）；当前版本先把 10.2 整段翻转为 delete，或回到修订阶段改修剪后文字重新生成。

## 6. 数据模型变更

`cutlist.json` 的 split 子段 item 新增：

| 字段 | 类型 | 说明 |
|---|---|---|
| `mark` | `"keep" \| "delete"` | 本段最终标记（建议推导 + 手工翻转后落盘） |
| `mark_manual` | bool | 是否手工翻转过（重建统计用） |

keep/fix 整段不带 mark（恒为保留，语义已由 kind 表达）。清单顶层新增 `manual_marks: {"<source_i>.<sub>": "keep"|"delete", …}`——**手工决策单独存**，`build_cutlist(sub_meta, rev, manual_marks=…)` 重建时按 (source_i, sub) 恢复手工翻转；重新生成清单会提示将清除手工决策（与 REQ-20260916-010「重新分析=重置全部」同一哲学）。stats 增加 `split_subs_delete`（洞段数）与 `mark_flipped`（手工翻转数）。

## 7. UI 与交互（切分修剪面板）

- **按父段分组**：组头 = 父编号 + 原文 + 建议（剪掉→保留文本摘要）；组内子段表。
- **子段行**：`10.2` ｜ 起止时间 ｜ 文本 ｜ 建议徽章（✅ 保留 / ❌ 删除，delete 行降透明度+删除线）｜ 操作：`▶ 预播`、`翻转标记`。
- **预播**：工作台新增迷你播放器（当前工作台无 video 元素，需加常驻小窗或点击时浮出）；点 ▶ → seek 到段 start 播放至 end 自动暂停。这是"听一眼再决定"的关键闭环。
- **翻转**：点徽章/按钮即翻转，未保存纯前端；「保存切分决策」落盘 cutlist.json（扩展 build_cutlist 端点或新增 save_cut_marks）。
- **fallback 段**（无字级时间戳/对不上）：单子段 mark=keep + `fallback` 徽章，同样可整段翻转为 delete。
- **手工删字/词**（M3 进阶）：子段内按 token 细分——点子段文本中某字/词，该 token 高亮，「从本段剔除」将该 token 前后切两刀生成三个子段（同算法同标记规则）。首版以"翻转整段"覆盖主路径。

## 8. 下游契约（精剪字幕 / 成片阶段）

- 成片时间轴 = 所有 mark=keep 段（keep/fix 整段 + split keep 子段）按 start_ms 升序拼接；mark=delete 全部剔除（不再依赖"子段缺失=删除"的隐式语义，显式双态）。
- 精剪字幕：每个 keep 段产一条字幕（split 子段文本=join_tokens；fix 段=更正后文本），编号沿用段 id（10.2 成片后可顺序重排，属下一阶段决策）。
- 剪辑执行（ffmpeg/moviepy）：直接消费 (start_ms, end_ms) 对列表，无需再对齐。

## 9. 边界情形

| 情形 | 处理 |
|---|---|
| 无字级时间戳（旧任务） | 整段单子段，fallback=true，mark=keep（现状语义），可手工整段删 |
| 修剪后文字为空 | 同上 fallback（不猜对齐） |
| 命中数 = 0（文字全对不上） | fallback 整段——对齐失败信号，宁可不切不可错切 |
| 全部命中（修剪=原文） | 无洞，单 keep 子段（等价现状） |
| 首尾洞 | 外扩边界用父段 start_ms/end_ms 兜底 |
| 零长洞（相邻 token 无缝） | e_ms-s_ms ≤ 0 跳过，不产空段 |
| token_ts 非单调（ASR 异常） | 边界换算统一 `max(prev_e, s_ms)` 防负时长/重叠 |
| 手工翻转后重生成 | manual_marks 按 (source_i, sub) 恢复；编号漂移导致对不上的丢弃并提示 |

## 10. 实施路径

- **M1 算法层**：`partition_tokens` + split 分支产完整子段（mark 字段）+ manual_marks 恢复 + 单测（交替完整性/首尾洞/全命中/零命中/fallback/翻转恢复）。纯函数改造，`build_cutlist` 签名向后兼容。
- **M2 UI 层**：清单按父段分组渲染子段表 + 标记徽章 + 翻转 + 保存端点 + 迷你播放器预播；E2E 只读验证渲染。
- **M3 进阶**：段内 token 细分、相邻同标记合并、键盘快捷导航（对齐修订阶段的操作手感）。
