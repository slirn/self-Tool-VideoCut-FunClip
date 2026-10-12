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

_OPTIMIZE_SYSTEM_PROMPT = """你是短视频字幕编辑。输入一条短视频的标题和带编号的字幕行，按规则优化并只输出一个 JSON 对象。
规则：
1. keep：字幕合适 → 原样保留，输出 {"no": 行号, "op": "keep"}。
2. fix：修改文字（去口语冗余、语气词、错别字），保持原意与信息量，输出 {"no": 行号, "op": "fix", "text": "修改后文字"}。
3. split：一行太长（约 30 字以上）或一句里含两个意思 → 切成多行，输出 {"no": 行号, "op": "split", "splits": ["子句一", "子句二", ...]}；切分子文本按顺序拼接必须与该行原文逐字一致（不改写、不遗漏、不新增）；想改写就同时需要 fix 的语义时，先放弃 split 只做 fix。
4. delete：纯口水话、重复、与标题主题无关、无信息量的行 → 在输出 lines 中省略该编号（即删除）。
5. 不改变行的先后顺序；不新增原文没有的内容；标题不改。
只输出 JSON：{"lines": [...]}，不要任何解释或代码块标记。"""

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
    state["prompt"] = str(prompt or "")[:500]
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

    # 下游作废：粗剪及之后 + 拼接（行变了旧 clip 失效）
    svc.reset_downstream_stages(project, "stage4_optimize")
    pipeline = project.setdefault("pipeline", {})
    if "stage3_splice" in pipeline:
        pipeline["stage3_splice"] = {"status": "pending"}

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
