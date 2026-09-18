"""切分修剪阶段关联人员 ID — REQ-20260917-031 / REQ-20260917-033.

按**时间段重叠**把字幕生成阶段的人员编号（subtitle.json 段级 spk，REQ-029
说话人分离产出）对齐到切分修剪清单的每一行（含切分子段），并按人员统计
**未删除**记录数（REQ-033 口径：删除状态不计入统计——服务于"确认非主讲
人员已清除"；删除洞与任何删除改判一样不计数）。对齐部分为纯函数、无 IO。

对齐规则：overlap = max(0, min(endA,endB) − max(startA,startB))，取重叠毫秒
最大且 >0 的 subtitle 段的 spk；并列取时间更早的段；无重叠 → 不标注。

持久化（REQ-033）：link_speakers 的结果快照落盘 outputs/speaker_link.json
（enabled 标记 + rows/stats 快照）。渲染时不直接用快照——enabled=true 时用
相同纯函数对当前数据现算（对齐确定性，修订变化不错位），快照仅供人工检查。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

LINK_FILENAME = "speaker_link.json"


def overlap_ms(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    """两时间窗的重叠毫秒数（无重叠为 0）。"""
    return max(0, min(a_end, b_end) - max(a_start, b_start))


def _parse_actions(cutlist: dict) -> dict[int, str]:
    """与 cutlist_service.effective_keep_units 同口径的组级改判解析。"""
    actions: dict[int, str] = {}
    for k, v in (cutlist.get("actions") or {}).items():
        try:
            v = str(v)
            if v in ("keep", "delete", "split"):
                actions[int(k)] = v
        except (TypeError, ValueError):
            continue
    return actions


def _row_kept(item: dict, actions: dict[int, str]) -> bool:
    """行是否在执行口径保留（action=delete 整条剔除；split 子段 mark=delete 剔除）。"""
    try:
        si = int(item.get("source_i", 0))
    except (TypeError, ValueError):
        return True
    if actions.get(si) == "delete":
        return False
    if item.get("kind") == "split" and str(item.get("mark") or "keep") == "delete":
        return False
    return True


def link_speakers(subtitle_meta: dict, cutlist: dict) -> dict:
    """subtitle 段级 spk → 切分清单每行的人员编号 + 按人员统计未删除记录数。

    返回 {available, rows: {行id: spk}, stats: [{spk, count}]}（REQ-033 口径：
    count = 执行口径保留的行数，删除状态完全不计入）。stats 按 spk 升序；
    行 id 与清单/面板一致（整段=父编号，子段=父.子）。
    """
    segs: list[dict] = []
    for s in (subtitle_meta or {}).get("segments") or []:
        try:
            spk = int(s.get("spk") or 0)
        except (TypeError, ValueError):
            continue
        if spk < 1:
            continue  # 无人员编号（旧任务 / SD 关闭）的段不参与对齐
        segs.append({
            "spk": spk,
            "start_ms": int(s.get("start_ms", 0)),
            "end_ms": int(s.get("end_ms", 0)),
        })
    if not segs:
        return {"available": False, "rows": {}, "stats": []}

    actions = _parse_actions(cutlist)
    rows: dict[str, int] = {}
    counts: dict[int, int] = {}
    for it in cutlist.get("items") or []:
        try:
            s_ms, e_ms = int(it.get("start_ms", 0)), int(it.get("end_ms", 0))
        except (TypeError, ValueError):
            continue
        best_ov, best_spk, best_start = 0, None, None
        for s in segs:
            ov = overlap_ms(s_ms, e_ms, s["start_ms"], s["end_ms"])
            if ov <= 0:
                continue
            if ov > best_ov or (ov == best_ov and best_start is not None
                                and s["start_ms"] < best_start):
                best_ov, best_spk, best_start = ov, s["spk"], s["start_ms"]
        if best_spk is None:
            continue
        rid = str(it.get("id") or "")
        if not rid:
            continue
        rows[rid] = best_spk
        if best_spk not in counts:  # 有行即入表（全删光也显示 count=0 = 已清零反馈）
            counts[best_spk] = 0
        if _row_kept(it, actions):  # 删除状态不计入统计（REQ-033）
            counts[best_spk] += 1

    stats = [{"spk": spk, "count": c} for spk, c in sorted(counts.items())]
    return {"available": True, "rows": rows, "stats": stats}


def save_link(outputs_dir: Path, link: dict) -> dict:
    """关联结果落盘（REQ-033）：speaker_link.json = enabled 标记 + 结果快照。"""
    payload = {
        "version": 1,
        "enabled": True,
        "linked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "rows": link.get("rows") or {},
        "stats": link.get("stats") or [],
    }
    (Path(outputs_dir) / LINK_FILENAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def load_link(outputs_dir: Path) -> dict | None:
    """读取关联状态；无文件/坏文件/未启用 → None（面板回普通态）。"""
    p = Path(outputs_dir) / LINK_FILENAME
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("enabled") else None
