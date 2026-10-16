"""REQ-20261013-104：短视频 Stage 6 字幕优化（行级 + 词频 + 批量替换 + 行内切分）。

借鉴 ``slirn_home.optimize_service`` 的思路（_normalize_occ / parse_occurrences /
aggregate_words / apply_to_segments / resplit_line / batch_search / batch_replace），
**重写为短视频版**：数据落 ``project.json → pipeline.stage6_refine.optimize`` 子键，
不 import 长视频 helper（因签名耦合 ``outputs_dir`` 长视频专用路径）。

数据模型（在 stage6_refine.optimize 子键下）：
- ``occurrences``：LLM 解析的「不明确字词」行级出现项；key = 行 id（"1" / "2"...）
  形如 ``[{"before": "...", "after": "...", "reason": "..."}, ...]``（按行聚合）
- ``decisions``：行级决策 dict；key = 行 id，value = {"accept": bool, "edit": "..."}
  - accept=True → 应用 after（默认）
  - accept=False → 保留 before
  - edit 非空 → 覆盖 after（手工编辑后）
- ``line_marks``：list[int] 待删除行 id 列表（按 seg 切时丢这些）
- ``splits``：dict[行id, list[子文本]] 行内切分子段
- ``words``：list[{"after": "...", "count": N, "lines": [行id, ...]}] 词频 chips
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from slirn_home import short_video_service as svc

log = logging.getLogger(__name__)

# ---------- SRT 解析（与 compose_service.parse_srt 同形态；纯本地避免 import 拉长链） ----------

_SRT_BLOCK_RE = re.compile(
    r"(\d+)\s*\n(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})[^\n]*\n([\s\S]*?)(?=\n\s*\n|\Z)",
    re.M,
)


def _srt_time_to_ms(t: str) -> int:
    """HH:MM:SS,mmm → ms。容错 `.` 当 `,` 用。"""
    t = str(t or "").strip().replace(".", ",")
    m = re.match(r"^(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})$", t)
    if not m:
        return 0
    h, mn, s, ms = m.groups()
    return (int(h) * 3600 + int(mn) * 60 + int(s)) * 1000 + int(ms.ljust(3, "0"))


def _ms_to_srt_time(ms: int) -> str:
    """ms → HH:MM:SS,mmm。"""
    if ms < 0:
        ms = 0
    s, milli = divmod(int(ms), 1000)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


def parse_srt(text: str) -> list[dict]:
    """SRT → [{id, start_ms, end_ms, text}, ...]。空段跳过。

    容错：id 缺失时按 1..N 顺序；index 重号也按出现顺序补号。
    """
    out: list[dict] = []
    for m in _SRT_BLOCK_RE.finditer(text or ""):
        idx = int(m.group(1))
        start = _srt_time_to_ms(m.group(2))
        end = _srt_time_to_ms(m.group(3))
        body = (m.group(4) or "").strip()
        out.append({"id": str(idx), "start_ms": start, "end_ms": end, "text": body})
    # 容错：补号（缺 id 时按出现顺序给 1..N）
    if out and [int(x["id"]) for x in out] != list(range(1, len(out) + 1)):
        for i, x in enumerate(out, 1):
            x["id"] = str(i)
    return out


def format_srt(entries: list[dict]) -> str:
    """[{id, start_ms, end_ms, text}] → SRT 字符串。"""
    parts = []
    for e in entries:
        eid = str(e.get("id") or "")
        st = _ms_to_srt_time(int(e.get("start_ms") or 0))
        et = _ms_to_srt_time(int(e.get("end_ms") or 0))
        txt = str(e.get("text") or "").strip()
        parts.append(f"{eid}\n{st} --> {et}\n{txt}")
    return "\n\n".join(parts) + ("\n\n" if parts else "")


# ---------- 阶段数据 I/O（project.json → pipeline.stage6_refine.optimize） ----------

def _get_optimize_state(project: dict) -> dict:
    """读 stage6_refine.optimize 子键；缺省返回全空 dict。"""
    s = svc.get_stage_state(project, "stage6_refine")
    opt = s.get("optimize")
    if not isinstance(opt, dict):
        opt = {}
    # 缺省字段
    opt.setdefault("occurrences", {})  # key=str(row_id) → [occ, ...]
    opt.setdefault("decisions", {})    # key=str(row_id) → {"accept": bool, "edit": ""}
    opt.setdefault("line_marks", [])   # list[int] 待删除
    opt.setdefault("splits", {})       # key=str(row_id) → [子文本, ...]
    opt.setdefault("words", [])        # list[{after, count, lines}]
    return opt


def _save_optimize_state(repo_root: Path, project: dict, opt: dict) -> None:
    """写回 stage6_refine 子键并 save_project。"""
    s = svc.get_stage_state(project, "stage6_refine")
    s["optimize"] = opt
    svc.set_stage_state(project, "stage6_refine", s)
    svc.save_project(repo_root, project)


# ---------- LLM 解析（防御式：剥 ```json 围栏、截 []、未知 id 丢弃） ----------

def _strip_json_block(raw: str) -> str:
    text = str(raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", text)
    if fence:
        return fence.group(1)
    return text


def _normalize_occ(rep: dict, text: str) -> dict | None:
    """校验并归一单条 occurrence。失败 → None（丢弃，宁缺勿错）。

    - before 非空且是 text 的连续子串（模型摘抄必须逐字）
    - after 非空且 != before（建议修正不能是原样）
    """
    if not isinstance(rep, dict):
        return None
    before = str(rep.get("before") or "").strip()
    after = str(rep.get("after") or "").strip()
    if not before or not after or before == after:
        return None
    if before not in text:
        return None
    return {"before": before, "after": after,
            "reason": str(rep.get("reason") or "").strip()}


def parse_occurrences(raw: str, entries: list[dict]) -> dict[str, list[dict]]:
    """解析 LLM 输出 → {行 id: [occ, ...]}。

    输入 entries 通常是单条 refined_NN.srt 的解析结果。返回 occurrences dict
    （只含有发现的行；空行不进）。
    """
    by_id = {str(e["id"]): str(e.get("text") or "") for e in entries}
    cleaned = _strip_json_block(raw or "")
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start < 0 or end <= start:
        return {}
    cleaned = cleaned[start:end + 1]
    try:
        arr = json.loads(cleaned)
    except Exception as e:  # noqa: BLE001
        log.warning("[opt-review] 模型输出不是合法 JSON: %s", e)
        return {}
    if not isinstance(arr, list):
        return {}
    out: dict[str, list[dict]] = {}
    for item in arr:
        if not isinstance(item, dict):
            continue
        rid = str(item.get("id") or "")
        if rid not in by_id:
            continue
        text = by_id[rid]
        # 旧模型可能用 unclear 字段；新模型用 occurrences 列表；都接受
        occs_in = item.get("occurrences")
        if not isinstance(occs_in, list):
            occs_in = item.get("unclear")
        if not isinstance(occs_in, list):
            # 也接受 {"before":..., "after":...} 单条
            if item.get("before") and item.get("after"):
                occs_in = [item]
            else:
                continue
        for rep in occs_in:
            norm = _normalize_occ(rep, text)
            if norm is None:
                continue
            out.setdefault(rid, []).append(norm)
    return out


# ---------- 词频聚合（chips 用） ----------

def aggregate_words(occurrences: dict[str, list[dict]]) -> list[dict]:
    """按 after 聚合 + 出现行数 + 总次数。"""
    bucket: dict[str, dict] = {}
    for rid, occs in (occurrences or {}).items():
        for o in occs or []:
            after = o.get("after") or ""
            if not after:
                continue
            if after not in bucket:
                bucket[after] = {"after": after, "count": 0, "lines": set()}
            bucket[after]["count"] += 1
            bucket[after]["lines"].add(str(rid))
    out = []
    for v in bucket.values():
        out.append({"after": v["after"], "count": v["count"],
                    "lines": sorted(v["lines"], key=lambda x: int(x) if x.isdigit() else 0)})
    out.sort(key=lambda x: (-x["count"], x["after"]))
    return out


# ---------- 行级决策应用 ----------

def apply_to_segments(entries: list[dict],
                      occurrences: dict[str, list[dict]],
                      decisions: dict[str, dict]) -> list[dict]:
    """按 decisions 把 occurrences 应用到 entries。返回新 list（不修改入参）。

    - accept=True (默认 True) + edit 非空 → 替换为 edit
    - accept=True + edit 空 → 替换为 after
    - accept=False → 保留原 text
    - 行级无 occurrence → 原样
    """
    out = []
    for e in entries:
        rid = str(e.get("id") or "")
        new_text = e.get("text", "")
        if rid in (occurrences or {}):
            dec = (decisions or {}).get(rid) or {}
            accept = bool(dec.get("accept", True))
            edit = str(dec.get("edit") or "").strip()
            if accept:
                occs = occurrences[rid] or []
                # 按 before → after/edit 顺序替换（不重叠：见 optimize_service.apply_replacements）
                spans: list[tuple[int, int, str]] = []
                for o in occs:
                    before = o.get("before") or ""
                    after = edit or (o.get("after") or "")
                    if not before or not after or before == after:
                        continue
                    pos = 0
                    while True:
                        idx = new_text.find(before, pos)
                        if idx < 0:
                            break
                        # 重叠剔除
                        if any(not (idx + len(before) <= s or s + le <= idx)
                               for s, le, _ in spans):
                            pos = idx + 1
                            continue
                        spans.append((idx, len(before), after))
                        pos = idx + len(before)
                # 倒序应用（不影响前面位置）
                for s, le, repl in sorted(spans, key=lambda x: -x[0]):
                    new_text = new_text[:s] + repl + new_text[s + le:]
        out.append({**e, "text": new_text})
    return out


# ---------- 行内切分 / 取消切分 ----------

def resplit_line(entries: list[dict], seg_id: str, splits: list[str]) -> list[dict]:
    """行内切分：把指定 id 的行替换为多行（时间按文本长度比例内插）。

    splits：list[str] 子文本列表；拼接后必须 = 原文（不一致也接受，按 LLM 输出信任）。
    """
    if not splits or not seg_id:
        return list(entries)
    out = []
    inserted = False
    for e in entries:
        if str(e.get("id") or "") == str(seg_id):
            full_text = e.get("text", "") or ""
            full_dur = max(1, int(e.get("end_ms") or 0) - int(e.get("start_ms") or 0))
            # 按字符数占比内插
            total_len = sum(max(1, len(s)) for s in splits) or 1
            cursor = int(e.get("start_ms") or 0)
            base_no = int(seg_id) if str(seg_id).isdigit() else len(out) + 1
            for i, sub in enumerate(splits):
                portion = max(1, len(sub)) / total_len
                dur = int(full_dur * portion)
                sub_start = cursor
                sub_end = cursor + dur if i < len(splits) - 1 else int(e.get("end_ms") or cursor + dur)
                out.append({
                    "id": str(base_no + i),
                    "start_ms": sub_start,
                    "end_ms": sub_end,
                    "text": sub,
                })
                cursor = sub_end
            inserted = True
        else:
            out.append(e)
    return out if inserted else list(entries)


def unsplit_line(entries: list[dict], seg_id: str) -> list[dict]:
    """取消切分：合并与指定 id 连续同前缀的多行为一行（占位：仅删除该行）。

    实用策略：把 entries 中 id 整型 ≥ seg_id 的后续连续多行（直到下个原始 id 段）
    合并到 seg_id。简单实现 = 直接过滤掉 seg_id 后续切分子段，保留 seg_id 主行。
    调用方需先传 saved original text；本函数假定 seg_id 是主行 id。
    """
    if not seg_id:
        return list(entries)
    base_id = int(seg_id) if str(seg_id).isdigit() else 0
    out = []
    for e in entries:
        eid = int(str(e.get("id") or "0")) if str(e.get("id") or "").isdigit() else 0
        # 切分子段 = id 介于 (base_id, base_id+N) 的子行（>= base_id+1 且 N 取决于切分数）
        # 用 spans 记录：进入主行后跳过若干子段；超过 5 个就视为子段
        out.append(e)
    # 简化：直接返回原 entries（取消切分由调用方传回原 saved text 全覆盖；本函数留为占位）
    return out


# ---------- 批量搜索 / 替换 ----------

def batch_search(entries: list[dict], word: str) -> list[dict]:
    """返回所有出现项：[{"id": 行id, "pos": 字符位置, "before": 命中词, "context": 上下文}, ...]"""
    if not word:
        return []
    out = []
    for e in entries:
        rid = str(e.get("id") or "")
        text = e.get("text", "") or ""
        pos = 0
        while True:
            idx = text.find(word, pos)
            if idx < 0:
                break
            ctx_start = max(0, idx - 10)
            ctx_end = min(len(text), idx + len(word) + 10)
            out.append({
                "id": rid,
                "pos": idx,
                "before": word,
                "context": text[ctx_start:ctx_end],
            })
            pos = idx + len(word)
    return out


def batch_replace(entries: list[dict], word: str, replacement: str) -> tuple[list[dict], int]:
    """所有出现项 word → replacement。返回 (新 entries, 替换次数)。"""
    if not word:
        return list(entries), 0
    out = []
    n = 0
    for e in entries:
        text = e.get("text", "") or ""
        if word in text:
            n += text.count(word)
            text = text.replace(word, replacement)
        out.append({**e, "text": text})
    return out, n


# ---------- 顶层：单条 LLM 优化 ----------

_OPTIMIZE_SYSTEM_PROMPT = """你是短视频字幕编辑。输入一条短视频的字幕行（按 id 编号），找出可能不明确或可优化的字词。
对每一行，输出该行里"不明确"或"建议改写"的小片段（before/after/reason），重复词不要在多行重复建议（首次出现给建议即可）。
规则：
1. before 必须是该行 text 里的连续子串（逐字摘抄）。
2. after 是建议改写后的字串（≠ before，简洁、规范、口语化优化）。
3. reason 简短一句话（可选）。
4. 没有建议的行可省略；不要捏造字词。
只输出 JSON 数组：[{"id": 行号, "occurrences": [{"before": "...", "after": "...", "reason": "..."}]}]。
不要任何解释或代码块标记。"""


_OPTIMIZE_USER_TMPL = """标题：{title}

字幕行（每行 id + 文本）：
{lines}

对每行给出不明确/可优化的字词出现项（before + after + reason），输出 JSON 数组。
只在确实需要修改时给建议；无需修改的行可省略。"""


def _load_refined_srt(repo_root: Path, task_id: str, project_id: str, index: int) -> Path:
    """单条 refined_NN.srt 路径（注意：refined 在 stage5 子目录，与 stage6 状态同义）。"""
    return svc.stage5_refined_srt_path(repo_root, task_id, project_id, index)


def _load_hl_title(repo_root: Path, task_id: str, project_id: str, index: int) -> str:
    """读 stage3 highlights.json 拿单条标题（作为 LLM 上下文）。"""
    try:
        from slirn_home import short_video_service as _svc
        hl_dir = _svc.stage3_dir(repo_root, task_id, project_id)
        p = hl_dir / "highlights.json"
        if not p.is_file():
            return ""
        data = json.loads(p.read_text(encoding="utf-8"))
        hls = data.get("highlights") or []
        for h in hls:
            if int(h.get("index") or 0) == int(index):
                return str(h.get("title") or h.get("name") or "")
    except Exception:  # noqa: BLE001
        return ""
    return ""


def run_optimize_one(repo_root: Path | str, task_id: str, project_id: str,
                     index: int, *, timeout_sec: int = 120) -> dict:
    """单条 LLM 优化：读 refined_NN.srt → 调 LLM → 写 occurrences + words。

    调 ``revision_service._call_llm``（与 Stage 3 / Stage 4 同款）。返回 opt 子键内容。
    """
    from slirn_home import revision_service

    root = Path(repo_root)
    srt = _load_refined_srt(root, task_id, project_id, index)
    if not srt.is_file():
        raise svc.ShortVideoError(f"refined srt 不存在: {srt}")
    text = srt.read_text(encoding="utf-8", errors="replace")
    entries = parse_srt(text)
    if not entries:
        raise svc.ShortVideoError("refined srt 无有效条目")
    title = _load_hl_title(root, task_id, project_id, index)
    lines_payload = [{"id": e["id"], "text": e["text"]} for e in entries]
    user_prompt = _OPTIMIZE_USER_TMPL.format(
        title=title or "（无标题）",
        lines=json.dumps(lines_payload, ensure_ascii=False, indent=2),
    )
    raw = revision_service._call_llm(  # noqa: SLF001 — 复用同款客户端
        _OPTIMIZE_SYSTEM_PROMPT, user_prompt,
    )
    occs = parse_occurrences(raw, entries)
    words = aggregate_words(occs)

    # 落 project.json
    project = svc.load_project(root, task_id, project_id)
    opt = _get_optimize_state(project)
    # 仅替换本条 occurrences（按 row id 限定为 index 段；行 id 在本条内为 1..N）
    # occs 解析时已用行 id（短编号）；按 "index:row_id" 复合 key 存（防冲突多 clip）
    prefix = f"{int(index)}:"
    for rid, occs_list in occs.items():
        opt["occurrences"][prefix + str(rid)] = occs_list
    # 词频：本条覆盖
    opt["words"] = words
    _save_optimize_state(root, project, opt)
    return {"occurrences": occs, "words": words,
            "index": int(index), "lines": len(entries)}


def save_decisions(repo_root: Path | str, task_id: str, project_id: str,
                   index: int, decisions: dict[str, dict]) -> dict:
    """保存行级 accept/edit 决策 + 应用到 refined srt 落盘。"""
    root = Path(repo_root)
    srt = _load_refined_srt(root, task_id, project_id, index)
    if not srt.is_file():
        raise svc.ShortVideoError(f"refined srt 不存在: {srt}")
    text = srt.read_text(encoding="utf-8", errors="replace")
    entries = parse_srt(text)
    project = svc.load_project(root, task_id, project_id)
    opt = _get_optimize_state(project)
    # 复合 key：prefix+rid
    prefix = f"{int(index)}:"
    for rid, dec in (decisions or {}).items():
        opt["decisions"][prefix + str(rid)] = {
            "accept": bool(dec.get("accept", True)),
            "edit": str(dec.get("edit") or ""),
        }
    # 应用决策 → 新 srt
    # 合并本条 occurrences（按 prefix 过滤）+ 本条 decisions
    local_occs = {rid.split(":", 1)[1]: v
                  for rid, v in opt["occurrences"].items()
                  if rid.startswith(prefix)}
    local_decs = {rid.split(":", 1)[1]: v
                  for rid, v in opt["decisions"].items()
                  if rid.startswith(prefix)}
    new_entries = apply_to_segments(entries, local_occs, local_decs)
    new_text = format_srt(new_entries)
    srt.write_text(new_text, encoding="utf-8")
    _save_optimize_state(root, project, opt)
    return {"applied": len(new_entries), "decisions": len(decisions or {})}


def resplit(repo_root: Path | str, task_id: str, project_id: str,
            index: int, seg_id: str, splits: list[str]) -> dict:
    """行内切分 → 落 refined srt + splits 子键。"""
    root = Path(repo_root)
    srt = _load_refined_srt(root, task_id, project_id, index)
    if not srt.is_file():
        raise svc.ShortVideoError(f"refined srt 不存在: {srt}")
    text = srt.read_text(encoding="utf-8", errors="replace")
    entries = parse_srt(text)
    new_entries = resplit_line(entries, str(seg_id), list(splits or []))
    new_text = format_srt(new_entries)
    srt.write_text(new_text, encoding="utf-8")
    project = svc.load_project(root, task_id, project_id)
    opt = _get_optimize_state(project)
    opt["splits"][f"{int(index)}:{seg_id}"] = list(splits or [])
    _save_optimize_state(root, project, opt)
    return {"applied": True, "lines": len(new_entries)}


def unsplit(repo_root: Path | str, task_id: str, project_id: str,
            index: int, seg_id: str) -> dict:
    """取消切分：清 splits 子键 + 占位 srt 行为（不修改文本；UI 拿 splits 重建）。"""
    root = Path(repo_root)
    project = svc.load_project(root, task_id, project_id)
    opt = _get_optimize_state(project)
    key = f"{int(index)}:{seg_id}"
    if key in opt["splits"]:
        del opt["splits"][key]
        _save_optimize_state(root, project, opt)
    return {"cleared": True, "key": key}


def search_word(repo_root: Path | str, task_id: str, project_id: str,
                index: int, word: str) -> dict:
    """单条 refined srt 内搜索词。"""
    root = Path(repo_root)
    srt = _load_refined_srt(root, task_id, project_id, index)
    if not srt.is_file():
        raise svc.ShortVideoError(f"refined srt 不存在: {srt}")
    text = srt.read_text(encoding="utf-8", errors="replace")
    entries = parse_srt(text)
    matches = batch_search(entries, word)
    return {"word": word, "matches": matches, "count": len(matches)}


def replace_word(repo_root: Path | str, task_id: str, project_id: str,
                 index: int, word: str, replacement: str) -> dict:
    """单条 refined srt 内替换词 → 落盘。"""
    root = Path(repo_root)
    srt = _load_refined_srt(root, task_id, project_id, index)
    if not srt.is_file():
        raise svc.ShortVideoError(f"refined srt 不存在: {srt}")
    text = srt.read_text(encoding="utf-8", errors="replace")
    entries = parse_srt(text)
    new_entries, n = batch_replace(entries, word, replacement)
    new_text = format_srt(new_entries)
    srt.write_text(new_text, encoding="utf-8")
    return {"replaced": n, "lines": len(new_entries)}


def get_state(repo_root: Path | str, task_id: str, project_id: str,
              index: int | None = None) -> dict:
    """读 optimize 子键（前端加载面板用）。"""
    root = Path(repo_root)
    project = svc.load_project(root, task_id, project_id)
    opt = _get_optimize_state(project)
    if index is not None:
        prefix = f"{int(index)}:"
        # 仅本条 occurrences
        local_occs = {rid.split(":", 1)[1]: v
                      for rid, v in opt["occurrences"].items()
                      if rid.startswith(prefix)}
        local_splits = {k.split(":", 1)[1]: v
                        for k, v in opt["splits"].items()
                        if k.startswith(prefix)}
        return {
            "occurrences": local_occs,
            "splits": local_splits,
            "words": opt.get("words") or [],
            "line_marks": opt.get("line_marks") or [],
        }
    return opt
