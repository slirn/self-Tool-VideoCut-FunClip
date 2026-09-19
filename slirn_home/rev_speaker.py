"""字幕修订阶段关联人员 ID — REQ-20260919-068.

参考切分修剪 cut_speaker.py（REQ-20260917-031/033）的对齐逻辑，但行集合
是 revision entries（每行一个 i + 一个 decision）而不是 cutlist items。

对齐规则：overlap = max(0, min(endA,endB) − max(startA,startB))，取重叠毫秒
最大且 >0 的 subtitle 段的 spk；并列取时间更早的段；无重叠 → 不标注。

持久化：link_speakers 的结果快照落盘 outputs/rev_speaker_link.json
（enabled 标记 + rows/stats 快照）。渲染时不直接用快照——enabled=true 时用
相同纯函数对当前数据现算（对齐确定性，修订变化不错位），快照仅供人工检查。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

LINK_FILENAME = "rev_speaker_link.json"


def overlap_ms(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    """两时间窗的重叠毫秒数（无重叠为 0）。"""
    return max(0, min(a_end, b_end) - max(a_start, b_start))


def _row_deleted(entry: dict) -> bool:
    """修订阶段：decision=delete 即视为删除（一行一决策，无组级/子段之分）。"""
    return str(entry.get("decision") or "") == "delete"


def link_speakers(subtitle_meta: dict, revision: dict) -> dict:
    """subtitle 段级 spk → revision entries 每行的人员编号 + 按人员统计未删除记录数。

    返回 {available, rows: {行i: spk}, stats: [{spk, count}]}。
    count 口径：decision != 'delete' 的行数（删除决策不计入；用于"确认非主讲
    人员已清除"）。stats 按 spk 升序；行 id 与修订面板一致（整数 1-based）。
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

    rows: dict[str, int] = {}
    counts: dict[int, int] = {}
    for e in (revision or {}).get("entries") or []:
        try:
            i = int(e.get("i"))
            s_ms = int(e.get("start_ms", 0))
            e_ms = int(e.get("end_ms", 0))
        except (TypeError, ValueError):
            continue
        if not (e_ms > s_ms):
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
        rows[str(i)] = best_spk
        if best_spk not in counts:  # 有行即入表（全删光也显示 count=0 = 已清零反馈）
            counts[best_spk] = 0
        if not _row_deleted(e):  # 删除决策不计入统计
            counts[best_spk] += 1

    stats = [{"spk": spk, "count": c} for spk, c in sorted(counts.items())]
    return {"available": True, "rows": rows, "stats": stats}


def save_link(outputs_dir: Path, link: dict) -> dict:
    """关联结果落盘：rev_speaker_link.json = enabled 标记 + 结果快照。"""
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