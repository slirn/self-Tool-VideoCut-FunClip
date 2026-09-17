"""切分修剪阶段关联人员 ID — REQ-20260917-031.

按**时间段重叠**把字幕生成阶段的人员编号（subtitle.json 段级 spk，REQ-029
说话人分离产出）对齐到切分修剪清单的每一行（含切分子段），并按人员统计
记录数（总数 / 执行口径保留数）。纯函数、无 IO — 便于单测。

对齐规则：overlap = max(0, min(endA,endB) − max(startA,startB))，取重叠毫秒
最大且 >0 的 subtitle 段的 spk；并列取时间更早的段；无重叠 → 不标注。
"""

from __future__ import annotations


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
    """subtitle 段级 spk → 切分清单每行的人员编号 + 按人员统计。

    返回 {available, rows: {行id: spk}, stats: [{spk, total, kept, deleted}]}。
    stats 按 spk 升序；行 id 与清单/面板一致（整段=父编号，子段=父.子）。
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
    counts: dict[int, dict[str, int]] = {}
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
        c = counts.setdefault(best_spk, {"total": 0, "kept": 0})
        c["total"] += 1
        if _row_kept(it, actions):
            c["kept"] += 1

    stats = [
        {"spk": spk, "total": c["total"], "kept": c["kept"],
         "deleted": c["total"] - c["kept"]}
        for spk, c in sorted(counts.items())
    ]
    return {"available": True, "rows": rows, "stats": stats}
