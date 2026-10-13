"""REQ-20261013-100：Stage 4「内容优化」（替换原 Stage 3.5 字幕一致性核验）。

把 Stage 3 生成的全部短视频（标题 + 字幕行）逐条交给大模型：
- **切分**：长行拆多行（子文本按序拼接必须与原文逐字一致 — 宁可不切不可
  错切，同长视频切分修剪原则；子行时间戳按文本占比线性内插，raw.srt 无
  字级时间戳，同 app.py 精剪合成的占比估算先例）。
- **文字修改**（fix）：去口语冗余/错字，保持原意；时间戳不动。
- **整行删除**：输出中缺席的编号即删除。
结果写回 ``stage3/highlights.json`` 的 subtitle_lines（首次优化前备份原表到
``stage4_optimize/backup_highlights.json``），拼接与粗剪及之后作废。

LLM 客户端沿用 ``revision_service._call_llm``（与 Stage 3 解析同款）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from slirn_home import revision_service
from slirn_home import short_video_service as svc

WARNINGS_CAP = 20

# REQ-20261013-108：使用 short_video_service.STAGE4_SYSTEM_PROMPT 作为系统提示词
# 旧版 _OPTIMIZE_SYSTEM_PROMPT 保留为别名（向后兼容 — 任何外部导入都不破）。
_OPTIMIZE_SYSTEM_PROMPT = svc.STAGE4_SYSTEM_PROMPT

_OPTIMIZE_USER_TMPL = """短视频标题：{title}

字幕行：
{lines}

对上面每行给出决策（keep/fix/split 之一，或省略=删除），输出：
{{"lines": [{{"no": 1, "op": "keep"}}, {{"no": 2, "op": "fix", "text": "..."}}, {{"no": 3, "op": "split", "splits": ["...", "..."]}}]}}
未出现在 lines 里的编号 = 删除该行。只输出 JSON。"""


def _strip_json_block(raw: str) -> str:
    """宽容提取 LLM 输出里的 JSON 对象（剥 ```json 围栏与前后杂文）。"""
    text = str(raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        return fence.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start:end + 1]
    return text


def _norm_text(s: str) -> str:
    """切分校验用：去所有空白与常见标点差异口径（逐字比对）。"""
    return re.sub(r"\s+", "", str(s or ""))


def _parse_optimize_result(raw: str, src_lines: list[dict]) -> tuple[dict, list[str]]:
    """解析 LLM JSON → {no: op}；op 为 "keep" / {"fix": text} / {"split": [..]}。

    校验（宁可不改不可改错）：
    - split 子文本按序拼接与原文逐字不等 → 该行降级 keep + 告警；
    - fix 后文字为空 → 降级 keep + 告警；
    - 未知 no / 非法 op → 忽略 + 告警。
    """
    warnings: list[str] = []
    ops: dict[int, dict] = {}
    by_no = {int(sl.get("no") or 0): sl for sl in src_lines}
    try:
        data = json.loads(_strip_json_block(raw))
    except Exception as e:  # noqa: BLE001
        raise svc.ShortVideoError(f"内容优化输出不是合法 JSON: {e}") from e
    lines = (data or {}).get("lines") if isinstance(data, dict) else None
    if not isinstance(lines, list):
        raise svc.ShortVideoError("内容优化输出缺少 lines 数组")

    for item in lines:
        if not isinstance(item, dict):
            continue
        try:
            no = int(item.get("no") or 0)
        except (TypeError, ValueError):
            continue
        src = by_no.get(no)
        if src is None:
            warnings.append(f"未知行号 {no} 被忽略")
            continue
        op = str(item.get("op") or "").strip().lower()
        src_text = str(src.get("text") or "")
        if op == "keep":
            ops[no] = {"op": "keep"}
        elif op == "fix":
            text = str(item.get("text") or "").strip()
            if not text:
                warnings.append(f"#{no} fix 文字为空，保留原行")
                ops[no] = {"op": "keep"}
            else:
                ops[no] = {"op": "fix", "text": text}
        elif op == "split":
            splits = [str(x or "").strip() for x in (item.get("splits") or []) if str(x or "").strip()]
            if len(splits) < 2:
                warnings.append(f"#{no} split 子句不足 2 段，保留原行")
                ops[no] = {"op": "keep"}
            elif _norm_text("".join(splits)) != _norm_text(src_text):
                warnings.append(f"#{no} split 子句拼接与原文不一致，保留原行（宁可不切不可错切）")
                ops[no] = {"op": "keep"}
            else:
                ops[no] = {"op": "split", "splits": splits}
        else:
            # REQ-20261013-110：兼容 LLM 误用 `op: "delete"`（prompt 明确说 delete = 省略，
            # 但部分 LLM 仍会输出 "delete"）。静默忽略，不警告（避免噪音）。
            if op == "delete":
                continue
            warnings.append(f"#{no} 未知 op「{op}」，保留原行")
            ops[no] = {"op": "keep"}
    return ops, warnings


def _interpolate_split(line: dict, splits: list[str]) -> list[dict]:
    """把一行按子文本字符占比内插切成多行（raw.srt 无字级时间戳）。

    时间戳口径与 app.py 精剪合成的占比估算先例一致；src_index 继承父行，
    no 由调用方按当前行序统一重编。
    """
    start = int(line.get("start_ms") or 0)
    end = int(line.get("end_ms") or start)
    total = sum(len(s) for s in splits) or 1
    span = max(0, end - start)
    out: list[dict] = []
    cursor = start
    used = 0
    for i, text in enumerate(splits):
        used += len(text)
        if i == len(splits) - 1:
            seg_end = end
        else:
            seg_end = start + round(span * used / total)
            seg_end = max(cursor + 1, min(end, seg_end))
        out.append({
            "src_index": line.get("src_index"),
            "text": text,
            "start_ms": cursor,
            "end_ms": seg_end,
        })
        cursor = seg_end
    return out


def _hl_window(hl: dict) -> None:
    """按当前行重算组窗口 start/end_ms/total_ms（与解析器同口径）。"""
    times = [(int(sl.get("start_ms") or 0), int(sl.get("end_ms") or 0))
             for sl in hl.get("subtitle_lines") or []]
    if not times:
        return
    hl["start_ms"] = min(t[0] for t in times)
    hl["end_ms"] = max(t[1] for t in times)
    hl["total_ms"] = sum(max(0, t[1] - t[0]) for t in times)


def _optimize_user_payload(title: str, lines: list[dict]) -> str:
    rows = "\n".join(
        f"{int(sl.get('no') or i + 1)}. {sl.get('text') or ''}"
        for i, sl in enumerate(lines)
    )
    return _OPTIMIZE_USER_TMPL.format(title=title or "（无标题）", lines=rows)


def run_stage4_optimize(
    repo_root: Path | str,
    task_id: str,
    project_id: str,
    *,
    prompt: str = "",
    entry: dict | None = None,
) -> dict:
    """Stage 4「内容优化」：LLM 逐条优化 Stage 3 字幕并写回 highlights.json。

    Args:
        prompt: 可选的附加优化要求（追加到 system 提示词尾部）。
        entry: llm_config 注册项；None 时用当前默认模型。
    """
    root = Path(repo_root)
    project = svc.load_project(root, task_id, project_id)

    hp = svc.stage_highlights_path(root, task_id, project_id)
    if not hp.is_file():
        raise svc.ShortVideoError("Stage 4 需要先完成 Stage 3 解析短视频内容")
    doc = json.loads(hp.read_text(encoding="utf-8"))
    highlights = [h for h in (doc.get("highlights") or []) if h.get("subtitle_lines")]
    if not highlights:
        raise svc.ShortVideoError("Stage 3 没有可优化的字幕行")

    # 状态：running
    state = svc.get_stage_state(project, "stage4_optimize")
    state["status"] = "running"
    state["started_at"] = svc.now_iso()
    state["count"] = len(highlights)
    # REQ-20261013-108：项目级用户提示词（"prompt_user" 区别于旧的 "prompt" 补充要求）
    state["prompt_user"] = str(prompt or "")[:2000]
    state["prompt"] = str(prompt or "")[:500]  # 兼容旧字段
    state["logs"] = list(state.get("logs") or []) + [
        f"[{svc.now_iso()}] stage4_optimize start, highlights={len(highlights)}",
    ]
    svc.set_stage_state(project, "stage4_optimize", state)
    svc.save_project(root, project)

    system = _OPTIMIZE_SYSTEM_PROMPT
    if prompt.strip():
        system += f"\n\n补充要求：{prompt.strip()}"

    # 首次优化前备份原表（已存在不覆盖 — 保住最初解析结果）
    opt_dir = svc.stage4_optimize_dir(root, task_id, project_id)
    opt_dir.mkdir(parents=True, exist_ok=True)
    backup = opt_dir / "backup_highlights.json"
    if not backup.is_file():
        backup.write_text(
            json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8",
        )

    all_warnings: list[str] = []
    hl_states: list[dict] = []
    failed = 0
    try:
        for hl in highlights:
            idx = int(hl.get("index") or 0) or (len(hl_states) + 1)
            src_lines = [dict(sl) for sl in hl.get("subtitle_lines") or []]
            user_p = _optimize_user_payload(str(hl.get("title") or ""), src_lines)
            raw = revision_service._call_llm(system, user_p, entry=entry, retries=1)
            (opt_dir / f"llm_raw_hl{idx}.txt").write_text(
                str(raw), encoding="utf-8",
            )
            ops, warns = _parse_optimize_result(str(raw), src_lines)
            all_warnings.extend(f"#{idx} {w}" for w in warns)

            new_lines: list[dict] = []
            kept = fixed = split = deleted = 0
            for sl in src_lines:
                no = int(sl.get("no") or 0)
                op = ops.get(no) or {"op": "delete"}
                if op["op"] == "delete":
                    deleted += 1
                elif op["op"] == "fix":
                    fixed += 1
                    new_lines.append({**sl, "text": op["text"]})
                elif op["op"] == "split":
                    split += 1
                    new_lines.extend(_interpolate_split(sl, op["splits"]))
                else:
                    kept += 1
                    new_lines.append(dict(sl))
            # 行序不变；no 按当前行序重编 1..M（切分/删除后原 no 不连续）
            for i, sl in enumerate(new_lines):
                sl["no"] = i + 1
            hl["subtitle_lines"] = new_lines
            _hl_window(hl)

            st = "ok" if new_lines else "failed"
            if not new_lines:
                failed += 1
                all_warnings.append(f"#{idx} 优化后没有任何字幕行，保留原行")
                hl["subtitle_lines"] = src_lines  # 宁可不改
                _hl_window(hl)
                st = "kept_original"
            hl_states.append({
                "index": idx,
                "status": st,
                "lines_before": len(src_lines),
                "lines_after": len(hl["subtitle_lines"]),
                "kept": kept, "fixed": fixed, "split": split, "deleted": deleted,
            })
    except svc.ShortVideoError as e:
        state = svc.get_stage_state(project, "stage4_optimize")
        state["status"] = "failed"
        state["error"] = str(e)
        state["finished_at"] = svc.now_iso()
        state["logs"] = list(state.get("logs") or []) + [
            f"[{svc.now_iso()}] stage4_optimize failed: {e}",
        ]
        svc.set_stage_state(project, "stage4_optimize", state)
        svc.save_project(root, project)
        return state

    # 写回 doc（溯源字段 + LLM 原文时间）
    doc["optimized_at"] = svc.now_iso()
    hp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    # 下游作废：粗剪及之后（行变了旧产物失效）
    svc.reset_downstream_stages(project, "stage4_optimize")
    pipeline = project.setdefault("pipeline", {})
    pipeline.pop("stage3_splice", None)

    # done（含 state 镜像整表同步 — 与 rerun/restore 同一口径）
    state = svc.get_stage_state(project, "stage4_optimize")
    state["status"] = "failed" if failed == len(highlights) else "done"
    state["finished_at"] = svc.now_iso()
    state["highlights"] = hl_states
    state["warnings"] = all_warnings[:WARNINGS_CAP]
    state["logs"] = list(state.get("logs") or []) + [
        f"[{svc.now_iso()}] stage4_optimize done, "
        f"hls={len(highlights)}, failed={failed}, warnings={len(all_warnings)}",
    ]
    s3 = svc.get_stage_state(project, "stage3_analyze")
    s3["highlights"] = [dict(h) for h in doc.get("highlights") or []]
    svc.set_stage_state(project, "stage3_analyze", s3)
    svc.set_stage_state(project, "stage4_optimize", state)
    svc.save_project(root, project)
    return state


# ---------- REQ-20261013-108：Stage 4 per-highlight 用户编辑状态 ----------
# 路径：<proj>/stage4_optimize/per_hl/<hl_index>.json
# 形态：{line_edits: {src_index: text}, line_marks: [src_index, ...], split_marks: {src_index: [text, ...]},
#        line_order: [src_index, ...] | None}  # REQ-20261013-110：段内重排白名单
# 注意：仅存用户手动编辑，**不污染** stage3/highlights.json（LLM 真相源）。
# 读侧用 _effective_subtitle_lines 合并 highlights + per_hl 后再消费。

_PER_HL_FILENAME = "subs.json"


def per_hl_dir(repo_root: Path | str, task_id: str, project_id: str) -> Path:
    """per-highlight 用户编辑状态的目录。"""
    opt_dir = svc.stage4_optimize_dir(repo_root, task_id, project_id)
    d = opt_dir / "per_hl"
    d.mkdir(parents=True, exist_ok=True)
    return d


def per_hl_path(repo_root: Path | str, task_id: str, project_id: str, hl_index: int) -> Path:
    """per-highlight 用户编辑状态文件路径。"""
    return per_hl_dir(repo_root, task_id, project_id) / f"{int(hl_index):02d}_{_PER_HL_FILENAME}"


def _empty_per_hl() -> dict:
    return {"line_edits": {}, "line_marks": [], "split_marks": {}, "line_order": None}


def load_per_hl(repo_root: Path | str, task_id: str, project_id: str, hl_index: int) -> dict:
    """读 per-hl 用户编辑状态。无 / 损坏 → 空。"""
    p = per_hl_path(repo_root, task_id, project_id, hl_index)
    if not p.is_file():
        return _empty_per_hl()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return _empty_per_hl()
    if not isinstance(data, dict):
        return _empty_per_hl()
    out = _empty_per_hl()
    le = data.get("line_edits") or {}
    if isinstance(le, dict):
        for k, v in le.items():
            if str(v or "").strip():
                out["line_edits"][str(k)] = str(v)
    lm = data.get("line_marks") or []
    if isinstance(lm, list):
        for x in lm:
            try:
                out["line_marks"].append(int(x))
            except (TypeError, ValueError):
                pass
    sm = data.get("split_marks") or {}
    if isinstance(sm, dict):
        for k, v in sm.items():
            if isinstance(v, list) and all(isinstance(x, str) for x in v):
                out["split_marks"][str(k)] = [str(x) for x in v]
    # REQ-20261013-110：line_order 是段内重排白名单（None = 保持原序）
    lo = data.get("line_order")
    if isinstance(lo, list) and lo:
        clean = []
        for x in lo:
            try:
                clean.append(int(x))
            except (TypeError, ValueError):
                pass
        if clean:
            out["line_order"] = clean
    return out


def save_per_hl(repo_root: Path | str, task_id: str, project_id: str,
                hl_index: int, line_edits: dict | None = None,
                line_marks: list | None = None,
                split_marks: dict | None = None,
                reorder: list | None = None) -> dict:
    """写 per-hl 用户编辑状态（合并覆盖，未传字段保留旧值）。

    REQ-20261013-110：``reorder`` 接受 ``None``（清除重排）or ``list[int]``
    （段内重排白名单）。传 ``[]`` 视为 None（空列表清空）。

    Returns: 写入后的完整 doc。
    """
    cur = load_per_hl(repo_root, task_id, project_id, hl_index)
    if line_edits is not None:
        cur["line_edits"] = {str(k): str(v) for k, v in (line_edits or {}).items()
                              if str(v or "").strip()}
    if line_marks is not None:
        cur["line_marks"] = [int(x) for x in (line_marks or [])
                              if str(x).strip().lstrip("-").isdigit()]
    if split_marks is not None:
        cur["split_marks"] = {str(k): [str(x) for x in (v or [])]
                               for k, v in (split_marks or {}).items()
                               if isinstance(v, list) and v}
    if reorder is not None:
        # None 或 [] → 清除重排
        if not reorder:
            cur["line_order"] = None
        else:
            cur["line_order"] = [int(x) for x in reorder
                                 if str(x).strip().lstrip("-").isdigit()]
    p = per_hl_path(repo_root, task_id, project_id, hl_index)
    p.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    return cur


def _interpolate_subs_time(orig: dict, subs: list[str]) -> list[dict]:
    """按字数占比在原行 [start_ms, end_ms] 内插子段时间戳（无字级时间戳时回退）。"""
    start_ms = int(orig.get("start_ms") or 0)
    end_ms = int(orig.get("end_ms") or start_ms)
    total_ms = max(1, end_ms - start_ms)
    total_len = sum(len(str(s or "")) for s in subs) or 1
    out: list[dict] = []
    cursor = start_ms
    for i, text in enumerate(subs):
        share = len(str(text or "")) / total_len
        sub_start = cursor
        sub_end = start_ms + int(total_ms * (sum(len(str(s or "")) for s in subs[: i + 1])) / total_len) \
            if i < len(subs) - 1 else end_ms
        out.append({
            "no": int(orig.get("no") or 0) * 1000 + i + 1,  # 子行 no 编码（与 src_index 不冲突）
            "src_index": int(orig.get("src_index") or 0) * 1000 + i + 1,  # 子行 src_index 编码
            "text": str(text or ""),
            "start_ms": int(sub_start),
            "end_ms": int(sub_end),
            "split_from": int(orig.get("src_index") or 0),  # 标记父行 src_index
        })
        cursor = sub_end
    return out


def _resolve_srt_line_times(repo_root: Path, task_id: str, project_id: str) -> dict[int, tuple[int, int]]:
    """raw.srt → {src_index: (start_ms, end_ms)}。空 / 失败 → {}。"""
    try:
        srt_path = svc.stage_raw_srt_path(repo_root, task_id, project_id)
    except Exception:  # noqa: BLE001
        return {}
    if not srt_path.is_file():
        return {}
    try:
        from slirn_home.short_video_analyze import _parse_srt_lines
        lines = _parse_srt_lines(srt_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return {int(ln.index): (int(ln.start_ms), int(ln.end_ms)) for ln in lines
            if int(ln.end_ms) > int(ln.start_ms)}


def effective_subtitle_lines(repo_root: Path | str, task_id: str, project_id: str,
                              hl_index: int) -> list[dict]:
    """合并 highlights.json + per-hl 后的实际字幕行（用于预览 / 粗剪 / 展示）。

    顺序：先按 line_marks 删 → 再 line_edits 改 text → 再 split_marks 切分。
    每行输出：{src_index, text, start_ms, end_ms, no} — start_ms/end_ms 来自
    raw.srt 按 src_index 查询；找不到则用 highlight 全段时间（按字数占比内插）。
    切分子行用原行 [start_ms, end_ms] 按字数内插。
    """
    root = Path(repo_root)
    hp = svc.stage_highlights_path(root, task_id, project_id)
    if not hp.is_file():
        return []
    doc = json.loads(hp.read_text(encoding="utf-8"))
    highlights = doc.get("highlights") or []
    if hl_index < 1 or hl_index > len(highlights):
        return []
    hl = highlights[hl_index - 1]
    base = [dict(sl) for sl in (hl.get("subtitle_lines") or [])]
    srt_index_to_time = _resolve_srt_line_times(root, task_id, project_id)
    hl_start = int(hl.get("start_ms") or 0)
    hl_end = int(hl.get("end_ms") or hl_start)
    # 给每行补上 start_ms / end_ms / no
    for i, s in enumerate(base):
        sid = int(s.get("src_index") or 0)
        if sid in srt_index_to_time:
            sm, em = srt_index_to_time[sid]
        else:
            # fallback: 整个 highlight 区间按字数占比内插
            total = sum(len(str(x.get("text") or "")) for x in base) or 1
            cum = sum(len(str(base[j].get("text") or "")) for j in range(i))
            cur_len = len(str(s.get("text") or ""))
            sm = hl_start + int((hl_end - hl_start) * cum / total)
            em = hl_start + int((hl_end - hl_start) * (cum + cur_len) / total)
        s["start_ms"] = sm
        s["end_ms"] = em
        s["no"] = i + 1
    per = load_per_hl(root, task_id, project_id, hl_index)
    marks = set(int(x) for x in per.get("line_marks") or [])
    if marks:
        base = [s for s in base if int(s.get("src_index") or 0) not in marks]
    # split_marks：按子段替换原行（子行内插时间）
    splits = per.get("split_marks") or {}
    if splits:
        new_base: list[dict] = []
        for s in base:
            sid = str(s.get("src_index") or 0)
            if sid in splits:
                new_base.extend(_interpolate_subs_time(s, splits[sid]))
            else:
                new_base.append(s)
        base = new_base
    # line_edits：整行替换 text（保留 start_ms / end_ms）
    edits = per.get("line_edits") or {}
    if edits:
        for s in base:
            sid = str(s.get("src_index") or 0)
            if sid in edits:
                s["text"] = edits[sid]
    # REQ-20261013-110：line_order 段内重排白名单（仅对原始 src_index 行重排；
    # split 子行带 split_from 字段，随父行一起走）。未列入的按原序追加到末尾。
    line_order = per.get("line_order")
    if line_order:
        by_src: dict[int, dict] = {}
        rest: list[dict] = []
        for s in base:
            if s.get("split_from") is not None:
                rest.append(s)  # split 子行不参与重排（跟随父行）
                continue
            sid = int(s.get("src_index") or 0)
            by_src[sid] = s
        ordered: list[dict] = []
        for sid in line_order:
            if sid in by_src:
                ordered.append(by_src.pop(sid))
        # 未列入 reorder 的剩余原始行按原序追加；split 子行穿插在原位之后整体追加
        ordered.extend(by_src.values())
        ordered.extend(rest)
        base = ordered
    # 重新按出现顺序编 no 1..M
    for i, s in enumerate(base):
        s["no"] = i + 1
    return base


def per_hl_apply_line_edit(repo_root: Path | str, task_id: str, project_id: str,
                            hl_index: int, src_index: int, new_text: str) -> dict:
    """整行替换 text（fix）。Returns 写入后的 per-hl doc。"""
    save_per_hl(repo_root, task_id, project_id, hl_index,
                line_edits={str(src_index): str(new_text)})
    return load_per_hl(repo_root, task_id, project_id, hl_index)


def per_hl_toggle_line_mark(repo_root: Path | str, task_id: str, project_id: str,
                             hl_index: int, src_index: int) -> dict:
    """整行删除/恢复（toggle）。Returns 写入后的 per-hl doc。"""
    cur = load_per_hl(repo_root, task_id, project_id, hl_index)
    marks = list(cur.get("line_marks") or [])
    sid = int(src_index)
    if sid in marks:
        marks.remove(sid)
    else:
        marks.append(sid)
    save_per_hl(repo_root, task_id, project_id, hl_index, line_marks=marks)
    return load_per_hl(repo_root, task_id, project_id, hl_index)


def per_hl_resplit(repo_root: Path | str, task_id: str, project_id: str,
                    hl_index: int, src_index: int, target_subs: list[str]) -> dict:
    """把一行切分为多段（split）。Returns 写入后的 per-hl doc。"""
    if not target_subs or not all(str(x or "").strip() for x in target_subs):
        raise svc.ShortVideoError("切分子段不能为空")
    save_per_hl(repo_root, task_id, project_id, hl_index,
                split_marks={str(src_index): [str(x) for x in target_subs]})
    return load_per_hl(repo_root, task_id, project_id, hl_index)


def per_hl_unsplit(repo_root: Path | str, task_id: str, project_id: str,
                    hl_index: int, src_index: int) -> dict:
    """取消切分（unsplit）。Returns 写入后的 per-hl doc。"""
    cur = load_per_hl(repo_root, task_id, project_id, hl_index)
    splits = dict(cur.get("split_marks") or {})
    splits.pop(str(src_index), None)
    save_per_hl(repo_root, task_id, project_id, hl_index, split_marks=splits)
    return load_per_hl(repo_root, task_id, project_id, hl_index)


def per_hl_reorder(repo_root: Path | str, task_id: str, project_id: str,
                   hl_index: int, order: list[int]) -> dict:
    """REQ-20261013-110：段内重排（白名单）。

    ``order`` 是 src_index 列表，按出现顺序应用。**未列入的原始 src_index
    按原序追加到末尾**（白名单语义，不会丢行）。传 ``[]`` 或 ``None`` = 清除
    重排，恢复原 src 顺序。

    写入后**不**自动 reset 下游 stage（coarse cut 暂不读 per-hl；UI 会在
    旁注里提示用户「重排后需重新优化才能影响 Stage 5 粗剪」）。
    """
    if order is not None and not isinstance(order, list):
        raise svc.ShortVideoError("order 必须是 list[int] 或 None")
    if not order:
        # 清除重排
        save_per_hl(repo_root, task_id, project_id, hl_index, reorder=[])
    else:
        # 校验：所有 src_index 必须是 int，且至少 1 个
        clean = [int(x) for x in order if str(x).strip().lstrip("-").isdigit()]
        if not clean:
            raise svc.ShortVideoError("order 至少包含 1 个有效 src_index")
        save_per_hl(repo_root, task_id, project_id, hl_index, reorder=clean)
    return load_per_hl(repo_root, task_id, project_id, hl_index)

