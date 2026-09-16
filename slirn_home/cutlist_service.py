"""切分修剪清单服务 — REQ-20260916-008。

前一阶段（字幕修订）全部决策完成后，把最终决策翻译成切分修剪清单：
- keep（完整保留）/ fix（内容更正）→ 带入本阶段，继承原编号不变；
  fix 采用更正后的文本（keep_text）
- delete → 剔除，不带入本阶段
- split（切分修剪）→ 把原字幕内容与切分后文字逐 token 对齐（字级时间戳），
  从原段音频中切出目标文字对应的字幕时间段：父级编号保留，
  切出的每个连续时间段为一个子段 — 原编号 10 切出 3 段 → 10.1 / 10.2 / 10.3
  （用户原话：保留父级编号、对切分出来的每一段加一个子编号；
    从上一阶段继承过来的序号尽量保持不变，方便音频与文字对照查看）

产物 outputs/cutlist.json（服务端预览现算不落盘，「生成切分清单」时才落盘 +
推进 ROUGH_CUT_DONE），后续阶段以它为输入。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

CUTLIST_JSON = "cutlist.json"

# 切分条目类型 → (中文标签, 徽章色) — 徽章色沿用修订阶段（keep 绿 / split 紫 / fix 蓝）
CUT_KINDS: dict[str, tuple[str, str]] = {
    "keep": ("完整保留", "keep"),
    "fix": ("内容更正", "fix"),
    "split": ("切分子段", "split"),
}

# token 切分 — 与上游 funclip/utils/subtitle_utils.str2list 同款正则。
# FunASR 的字级 timestamp 正是与该口径的 token 一一对应
# （上游 generate_srt_clip 即按此假设切片对齐），口径必须一致才能对齐时间。
_TOKEN_RE = re.compile("[\\u4e00-\\u9fff]|[\\w-]+")


def tokenize(text: str) -> list[str]:
    """文本 → token 列表（中文单字、英文/数字连字）。"""
    return _TOKEN_RE.findall(str(text or ""))


def join_tokens(tokens: list[str]) -> str:
    """token 列表 → 显示文本（中文直接拼、英文词空格 — 与 Text2SRT.text() 一致）。"""
    res = ""
    for w in tokens:
        if 0x4E00 <= ord(w[:1]) <= 0x9FFF:  # 中文字符直接拼（Text2SRT 同口径）
            res += w
        else:
            res += " " + w
    return res.lstrip().rstrip(" 、。，")


def ms2srt(ms: int) -> str:
    """毫秒 → 'HH:MM:SS,mmm'（与上游 time_convert 输出一致，subtitle.json 同款）。"""
    ms = max(0, int(ms))
    tail = ms % 1000
    s = ms // 1000
    mi, s = divmod(s, 60)
    h, mi = divmod(mi, 60)
    return f"{h:02d}:{mi:02d}:{s:02d},{tail:03d}"


def align_tokens(orig: list[str], keep: list[str]) -> list[int]:
    """keep tokens 按序在 orig 中贪心匹配 → 命中的 orig 索引列表（最靠前优先）。

    修剪目标是原文子集（去掉语气词/重复），子序列匹配即可；对不上的
    token（用户改写过）跳过，保序不回溯。
    """
    marks: list[int] = []
    pos = 0
    for kt in keep:
        for j in range(pos, len(orig)):
            if orig[j] == kt:
                marks.append(j)
                pos = j + 1
                break
    return marks


def contiguous_blocks(marks: list[int]) -> list[tuple[int, int]]:
    """命中索引 → 连续块 [(起, 止), …]（每块 = 一个切分子段）。"""
    blocks: list[tuple[int, int]] = []
    for idx in marks:
        if blocks and idx == blocks[-1][1] + 1:
            blocks[-1] = (blocks[-1][0], idx)
        else:
            blocks.append((idx, idx))
    return blocks


def _final_kind(entry: dict) -> str | None:
    """条目最终生效类别：手动改判（keep/delete/split/fix）优先，accept=采纳模型建议。

    pending → None（本阶段入口要求修订完成，防御兜底）。
    """
    d = str(entry.get("decision") or "")
    if d in ("keep", "delete", "split", "fix"):
        return d
    if d == "accept":
        cat = str(entry.get("category") or "")
        return cat if cat in ("keep", "delete", "split", "fix") else None
    return None


def _split_target(entry: dict) -> str:
    """split 的切分后文字：手动填写的「切分修剪后内容」优先，回退模型建议 keep_text。"""
    return str(entry.get("user_note") or "").strip() or str(entry.get("keep_text") or "").strip()


def _segment_tokens(seg: dict) -> tuple[list[str], list[list[int]]] | None:
    """段的 (tokens, token_ts) — 仅当两列表非空且等长（字级时间戳可信）。"""
    tokens = seg.get("tokens")
    ts = seg.get("token_ts")
    if (not isinstance(tokens, list) or not isinstance(ts, list)
            or not tokens or len(tokens) != len(ts)):
        return None
    try:
        norm = [[int(t[0]), int(t[1])] for t in ts]
    except (TypeError, ValueError, IndexError):
        return None
    return [str(w) for w in tokens], norm


def build_cutlist(subtitle_meta: dict, revision: dict) -> dict:
    """字幕生成 + 修订决策 → 切分修剪清单（纯函数，不落盘）。

    - keep/fix：整段带入，id=原编号（字符串），fix 文本=更正后（keep_text）
    - delete：剔除
    - split：token 对齐切子段，id=「父编号.子序号」；无字级时间戳/对不上 →
      整段单子段（fallback 标记，文本=切分后文字）
    - 继承编号不重排（删除留下的空洞保留，方便与修订阶段对照）
    """
    seg_by_i = {int(s["i"]): s for s in (subtitle_meta or {}).get("segments") or []}
    items: list[dict] = []
    stats = {"kept": 0, "fixed": 0, "split_parents": 0, "split_subs": 0,
             "dropped": 0, "fallback": 0, "keep_duration_ms": 0}
    for e in (revision or {}).get("entries") or []:
        i = int(e.get("i", 0))
        seg = seg_by_i.get(i)
        if seg is None:
            continue
        kind = _final_kind(e)
        if kind == "delete":
            stats["dropped"] += 1
            continue
        if kind is None:  # pending / 采纳 review 等 — 防御：按整段保留带入
            kind = "keep"
        if kind in ("keep", "fix"):
            text = str(e.get("keep_text") or "").strip() if kind == "fix" else str(seg.get("text", ""))
            if kind == "fix" and not text:
                text = str(seg.get("text", ""))  # 更正文本缺失 → 原文兜底
            items.append({
                "id": str(i), "source_i": i, "kind": kind, "sub": None,
                "start_ms": int(seg.get("start_ms", 0)), "end_ms": int(seg.get("end_ms", 0)),
                "start": str(seg.get("start", "")), "end": str(seg.get("end", "")),
                "text": text, "orig_text": str(seg.get("text", "")),
                "target_text": None, "fallback": False,
            })
            stats["kept" if kind == "keep" else "fixed"] += 1
            stats["keep_duration_ms"] += max(0, int(seg.get("end_ms", 0)) - int(seg.get("start_ms", 0)))
            continue

        # ---- split：原内容与切分后文字对齐 → 子段（父编号.子序号）----
        target = _split_target(e)
        aligned = _segment_tokens(seg)
        blocks: list[tuple[int, int]] = []
        if aligned and target:
            orig_tokens, token_ts = aligned
            blocks = contiguous_blocks(align_tokens(orig_tokens, tokenize(target)))
        if not blocks:
            # 无字级时间戳（旧字幕数据）/ 目标文字对不上 → 整段单子段降级
            items.append({
                "id": f"{i}.1", "source_i": i, "kind": "split", "sub": 1,
                "start_ms": int(seg.get("start_ms", 0)), "end_ms": int(seg.get("end_ms", 0)),
                "start": str(seg.get("start", "")), "end": str(seg.get("end", "")),
                "text": target or str(seg.get("text", "")),
                "orig_text": str(seg.get("text", "")), "target_text": target or None,
                "fallback": True,
            })
            stats["split_parents"] += 1
            stats["split_subs"] += 1
            stats["fallback"] += 1
            stats["keep_duration_ms"] += max(0, int(seg.get("end_ms", 0)) - int(seg.get("start_ms", 0)))
            continue
        orig_tokens, token_ts = aligned
        for k, (a, b) in enumerate(blocks, start=1):
            items.append({
                "id": f"{i}.{k}", "source_i": i, "kind": "split", "sub": k,
                "start_ms": token_ts[a][0], "end_ms": token_ts[b][1],
                "start": ms2srt(token_ts[a][0]), "end": ms2srt(token_ts[b][1]),
                "text": join_tokens(orig_tokens[a:b + 1]),
                "orig_text": str(seg.get("text", "")), "target_text": target,
                "fallback": False,
            })
            stats["keep_duration_ms"] += max(0, token_ts[b][1] - token_ts[a][0])
        stats["split_parents"] += 1
        stats["split_subs"] += len(blocks)

    stats["brought"] = len(items)
    return {
        "version": 1,
        "created_at": (revision or {}).get("saved_at") or (revision or {}).get("created_at") or "",
        "entries_count": len((revision or {}).get("entries") or []),
        "stats": stats,
        "items": items,
    }


def save_cutlist(outputs_dir: Path, cutlist: dict) -> Path:
    """清单落盘 outputs/cutlist.json。"""
    import time

    p = Path(outputs_dir) / CUTLIST_JSON
    cutlist = dict(cutlist)
    cutlist["saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cutlist, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def load_cutlist(outputs_dir: Path) -> dict | None:
    """读取 cutlist.json（无/损坏 → None）。"""
    p = Path(outputs_dir) / CUTLIST_JSON
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log.warning("cutlist.json 损坏: %s", e)
        return None
