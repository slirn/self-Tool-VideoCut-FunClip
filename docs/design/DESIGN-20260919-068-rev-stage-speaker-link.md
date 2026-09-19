# DESIGN-20260919-068 字幕修订阶段·人员 ID 关联

## Context

REQ-20260919-068：把切分修剪阶段的「关联人员ID」功能完整移植到字幕修订阶段。包括：
- 行内人员徽章
- 顶部「关联人员ID / 重新关联」按钮
- 关联后的统计条（chips + 查找 + 跳转 + 删除 + 重算）

参考既有 cut_speaker 模块（`slirn_home/cut_speaker.py`），但行集合从切分清单换为 revision entries。

## 设计

### 1. 新模块 `slirn_home/rev_speaker.py`

与 `cut_speaker.py` 同模式（纯函数 + IO 辅助），但行集合是 revision entries 而不是 cutlist items。

```python
LINK_FILENAME = "rev_speaker_link.json"

def overlap_ms(a_start, a_end, b_start, b_end) -> int: ...
def _row_deleted(entry: dict) -> bool:
    """decision=delete 即视为删除（修订阶段不区分组级/子段 — 一行一决策）。"""
    return str(entry.get("decision") or "") == "delete"

def link_speakers(subtitle_meta: dict, revision: dict) -> dict:
    """subtitle 段级 spk → revision entries 每行的人员编号 + 按人员统计。
    
    revision.entries 元素有 i, start_ms, end_ms, decision 字段。
    返回 {available, rows: {i: spk}, stats: [{spk, count}]}。
    count 口径 = 未删除决策的行数（decision != 'delete'）。
    """
    ...

def save_link(outputs_dir, link) -> dict: ...
def load_link(outputs_dir) -> dict | None: ...
```

**关键差异（vs cut_speaker）**：
- 行 id 是整数（`e["i"]`），不是字符串（"10.1"）。但 JSON 序列化时 key 还是 str — 渲染端用 `str(i)` 索引。
- 删除口径：cutlist 有 `actions`（组级改判）和子段 `mark`；revision 只有 `decision` 单字段。`_row_deleted` 简化。

### 2. 新 endpoint `POST /slirn/api/rev_speaker_link`

```python
@app.app.post("/slirn/api/rev_speaker_link")
async def rev_speaker_link(body: dict):
    """字幕修订阶段关联人员ID（REQ-20260919-068）：
    按时间段重叠对齐 subtitle 段级 spk 到 revision entries。
    落盘 rev_speaker_link.json；返回 link + rows 数 + linked_at。
    """
    from slirn_home import asr_service, revision_service, rev_speaker
    
    tid = body["task_id"]
    outputs_dir = mgr.tasks_dir / tid / "outputs"
    rev = revision_service.load_revision(outputs_dir)
    if not rev or not rev.get("entries"):
        return _err("尚无修订建议，请先在「字幕修订」阶段完成大模型分析")
    sub_meta = asr_service.load_subtitle(outputs_dir)
    if not sub_meta or not sub_meta.get("segments"):
        return _err("缺少字幕生成产物")
    
    link = rev_speaker.link_speakers(sub_meta, rev)
    if not link.get("available"):
        return _err("字幕无人员编号 — 该任务生成字幕时未开启说话人分离（或为旧任务）")
    
    saved = rev_speaker.save_link(outputs_dir, link)
    return _ok("", link=link, rows=len(rev["entries"]),
               linked_at=saved.get("linked_at"))
```

### 3. 字幕修订面板渲染

**行内徽章**（紧跟序号）：
```python
def _rev_spk_badge(i) -> str:
    spk = spk_rows.get(str(i))
    if not spk: return ""
    return (f'<span class="slirn-rev-spk"'
            f' title="人员 {int(spk)}（时间段重叠最大的字幕段说话人）">👤{int(spk)}</span>')
```

**关联按钮 + 统计条**：参照切分修剪 `spk_bar`。

### 4. 前端 router.js

新增 `revSpkBarRender` 函数（与 `cutSpkBarRender` 同结构）：
- 保留查找输入 + skipdel 状态
- chips 渲染 + 点击填入查找框
- 「上一条/下一条」遍历 `.slirn-rev-row[data-spk=N]`，跳过 deleted（按 data-decision 判定）
- 「删除该人员全部记录」：把匹配行的 select 改成 `delete`，触发 `applyRevDecision` 或直接 POST
- 「重新统计」：调 `rev-spk-recount` → POST 拉新 link → 重渲

新增 actions：
- `rev-spk-link` → POST `/slirn/api/rev_speaker_link` → toast + 重渲
- `rev-spk-prev` / `rev-spk-next` / `rev-spk-chip` / `rev-spk-delete` / `rev-spk-recount`

### 5. CSS

复用 `.slirn-cut-spk*` 样式类，或新增一组 `.slirn-rev-spk*`（更清晰，避免混入 cut 阶段）。

行内徽章用 `.slirn-rev-spk`（小色块 + 👤 数字），紧跟 `.slirn-sub-idx`。

### 6. 关键文件

| 文件 | 改动 |
|---|---|
| `slirn_home/rev_speaker.py` (新) | 链接逻辑 + IO |
| `slirn_home/app.py` | 新 endpoint + 修订面板渲染徽章/统计条/按钮 |
| `slirn_home/static/router.js` | 新增 rev-spk-* handlers + revSpkBarRender |
| `slirn_home/static/home.css` | 新增 .slirn-rev-spk* 样式 |
| `tests/test_workbench.py` | 新增单测 |

## 实施步骤

1. `slirn_home/rev_speaker.py` (新) — link_speakers + save_link + load_link
2. `slirn_home/app.py` — 新 endpoint + 修订面板渲染
3. `slirn_home/static/router.js` — 新 handlers
4. `slirn_home/static/home.css` — 新样式
5. `tests/test_workbench.py` — 单测
6. 重启 slirn + 真机手测

## 风险与边界

| 风险 | 处理 |
|---|---|
| 旧任务无 subtitle spk | 关联 endpoint 返回 `_err` + toast（与切分修剪一致） |
| 关联修改了 revision 数据 | 不改 — 只读 subtitle spk + 写快照 |
| 删除该人员全部记录后没保存 | toast 提示用户需保存；删除改判只在前端，不落盘（与「批量改判」同口径） |
| 统计条和行徽章对不上 | 行徽章用同一份 spk_rows 服务端渲染；前端不维护独立状态 |