"""优化字幕服务（成片重识别 + 不明确字词提取替换）— REQ-20260917-030。

把粗剪合成成片（rough_compose.mp4）+ 任务热词交给 ASR（asr_service 同链路，
sd=False 只识别字幕、不分离说话人），成片时间基的字幕行集再交大模型语义分析：
提取「不明确的字/词」（疑似同音/近音误识别、语义不通、与热词表冲突的片段），
逐处给出替换建议与理由。人工逐处分辨（采纳/不采纳/改替换值）后保存：
替换对应关系 + 替换后行文本（new_text）+ 按目标词聚合统计落盘
optimize_subtitle.json，阶段推进 FINE_SUBTITLE_REVIEWED。

设计要点（DESIGN-20260917-030）：
- 识别复用 asr_service 内部函数（模型单例 + 16k 单声道抽取），绝不写
  subtitle.json/srt —— 那是切分/修订全链路的根基产物
- 替换是局部片段级：before 必须逐字摘自行文本、after ≠ before、重叠剔除
  （复用 fine_service.apply_replacements）—— 模型只提议，人工终审
- 词频按替换目标词（after）分组：同一目标词的多种错误写法计同一词多次出现
- 后台线程 job 与 asr/revision/fine 同模式（内存 job 表 + 阶段级进度）
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable

from slirn_home.fine_service import apply_replacements
from slirn_home import execution_history

log = logging.getLogger(__name__)

OPTIMIZE_JSON = "optimize_subtitle.json"

# 分批行数：与 fine_service 同口径，40 行/批
BATCH_SIZE = 40

OPT_SYSTEM = (
    "你是字幕校对助手。用户给你一段视频重新识别出的字幕行列表（JSON 数组，"
    "每项含行 id 与文本），并附一份热词表（专有名词/人名/术语的正确写法，可能为空）。\n"
    "这段文本由 ASR 对口语录音转写，常混有同音/近音误写。请逐行仔细检查，"
    "提取「不明确的字/词」——疑似语音识别错误的片段。\n\n"
    "重点信号（命中任意一条即标记）：\n"
    "- 同音/近音误写（如「神精网络」应为「神经网络」）\n"
    "- 句中突兀的数字/字母（在口语句里说不通）\n"
    "- 语义不通或与上下文不搭的组合\n"
    "- 与热词表写法不一致的专有名词\n"
    "- 疑似重复识别的叠字（如「视频视频」）\n\n"
    "判定标准：\n"
    "- 只标记局部可疑片段：不得整行改写、不得增删其他内容、不得调整语序\n"
    "- 每处给出最合理的猜测修正（after，只写替换后的文字本身，不要带解释/括号）"
    "和简短理由（reason）\n"
    "- 行内已正确、只是口语化/语气词/书面口语差异 → 不标记\n"
    "- 态度：宁多提议不替人决策——拿不准但值得人工核对的也要标记，最终由人工分辨；"
    "整段录音至少评估出最可疑的几处，确信完全无误才放过\n\n"
    "输出要求：只输出 JSON 数组（没有发现时输出 []），不要任何其他文字。"
    "仅含有发现的行，每项格式：\n"
    '{"id": "行id", "unclear": [{"before": "行内原文片段（必须逐字摘自行文本）", '
    '"after": "建议替换后的文字", "reason": "简短理由"}]}\n'
    "before 必须逐字等于行文本中的一段连续原文；同一行的多个片段互不重叠。"
)

# 首轮零发现时复检用的追加指令 — 口语转写几乎总有可疑处，零发现多半是漏检而非干净
OPT_STRICT_SUFFIX = (
    "\n\n【复检要求】上一轮检查一无所获，但口语 ASR 转写几乎总含可疑片段。"
    "请降低标记门槛重新逐行检查：叠字重复、突兀数字/字母、语义不通、近音误写，"
    "哪怕只有三成把握也要提议（人工最终分辨）。整段至少给出 3 处候选；"
    "若确属完全干净才能输出 []。"
)


def build_user_prompt(task_name: str, hotwords: list[str], lines: list[dict]) -> str:
    """构造 user 消息：任务上下文 + 热词表 + 成片识别行列表（id + 文本）。"""
    parts = [f"视频任务：{task_name or '（未命名）'}"]
    parts.append(f"热词表：{'、'.join(hotwords) if hotwords else '（空）'}")
    parts.append("字幕行列表（粗剪成片重新识别）：")
    parts.append(json.dumps(
        [{"id": str(ln["id"]), "text": str(ln.get("text", ""))} for ln in lines],
        ensure_ascii=False,
    ))
    return "\n".join(parts)


# =============== 解析与校验（纯函数，便于测试） ===============

def _normalize_occ(rep: dict, text: str) -> dict | None:
    """校验并归一单个不明确片段。不合法 → None（丢弃，宁缺勿错）。

    - before 非空且是 text 的连续子串（模型摘抄必须逐字）
    - after 非空且 != before（建议修正不能是原样）
    - reason 可缺省（仅展示用）
    """
    if not isinstance(rep, dict):
        return None
    before = str(rep.get("before") or "")
    after = str(rep.get("after") or "")
    if not before or not after or before == after:
        return None
    if before not in text:
        return None
    return {"before": before, "after": after, "reason": str(rep.get("reason") or "")}


def parse_occurrences(raw: str, lines: list[dict]) -> dict[str, list[dict]]:
    """防御式解析模型输出 → {行 id: 生效出现列表}（只含有发现的行）。

    剥 ```json 围栏、截取首个 '[' 到最后一个 ']'；未知 id 丢弃；
    出现项过 _normalize_occ + apply_replacements（重叠剔除，附 pos）。
    """
    import re

    by_id = {str(ln["id"]): str(ln.get("text", "")) for ln in lines}
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw or "").strip()
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start >= 0 and end > start:
        cleaned = cleaned[start:end + 1]
    try:
        arr = json.loads(cleaned)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"模型输出不是合法 JSON: {e}") from e
    if not isinstance(arr, list):
        raise ValueError("模型输出不是 JSON 数组")
    out: dict[str, list[dict]] = {}
    for item in arr:
        if not isinstance(item, dict):
            continue
        rid = str(item.get("id") or "")
        if rid not in by_id:
            continue
        occ_in = item.get("unclear")
        if not isinstance(occ_in, list):
            continue
        occs = [o for o in (_normalize_occ(o, by_id[rid]) for o in occ_in) if o]
        if not occs:
            continue
        _, applied = apply_replacements(by_id[rid], occs)
        if applied:
            out[rid] = applied
    return out


# =============== 聚合与统计（纯函数） ===============

def aggregate_words(occurrences: list[dict]) -> list[dict]:
    """按替换目标词（after）聚合：[{word, count}]，次数降序、词升序。

    同一目标词的多种错误写法（「神精网络」「神经网洛」→「神经网络」）
    计为同一词的多次出现 — 回答"这个词出现了几次"。
    """
    counts: dict[str, int] = {}
    for o in occurrences or []:
        if not o.get("applied", True):
            continue
        w = str(o.get("after") or "")
        if w:
            counts[w] = counts.get(w, 0) + 1
    return [{"word": k, "count": counts[k]} for k in sorted(counts, key=lambda x: (-counts[x], x))]


def build_mapping(occurrences: list[dict]) -> list[dict]:
    """替换对应关系汇总：[{after, variants: [{before, count}]}]（只计生效项）。

    与 aggregate_words 同分组口径；variants 展示该目标词下被替换的各种原文写法。
    """
    variants: dict[str, dict[str, int]] = {}
    for o in occurrences or []:
        if not o.get("applied", True):
            continue
        after = str(o.get("after") or "")
        before = str(o.get("before") or "")
        if after and before:
            variants.setdefault(after, {})
            variants[after][before] = variants[after].get(before, 0) + 1
    out = []
    for after in sorted(variants, key=lambda a: (-sum(variants[a].values()), a)):
        out.append({
            "after": after,
            "count": sum(variants[after].values()),
            "variants": [{"before": b, "count": c}
                         for b, c in sorted(variants[after].items(), key=lambda kv: (-kv[1], kv[0]))],
        })
    return out


def apply_to_segments(segments: list[dict], occurrences: list[dict]) -> list[dict]:
    """把生效出现项应用到行集：每段补 new_text（无生效项的段不加该字段）。"""
    by_seg: dict[str, list[dict]] = {}
    for o in occurrences or []:
        if o.get("applied", True):
            by_seg.setdefault(str(o.get("seg")), []).append(o)
    out = []
    for seg in segments:
        s = dict(seg)
        occs = by_seg.get(str(s.get("i")))
        if occs:
            reps = [{"before": o["before"], "after": o["after"]}
                    for o in sorted(occs, key=lambda x: int(x.get("pos", 0)))]
            new_text, _applied = apply_replacements(str(s.get("text", "")), reps)
            if _applied:
                s["new_text"] = new_text
        out.append(s)
    return out


def build_srt(segments: list[dict]) -> str:
    """优化后行集 → 标准 SRT（new_text 优先；与 subtitle.srt 同构）。"""
    from slirn_home import asr_service

    norm = [{"i": s["i"], "start": s["start"], "end": s["end"],
             "text": str(s.get("new_text") or s.get("text", ""))}
            for s in segments]
    return asr_service.segments_to_srt(norm)


def effective_stats(data: dict) -> dict:
    """生效统计：识别行数 / 提取出现数 / 生效替换数 / 未采纳数 / 涉及词数。"""
    occs = data.get("occurrences") or []
    live = [o for o in occs if o.get("applied", True)]
    return {
        "lines": len(data.get("segments") or []),
        "occurrences": len(occs),
        "applied": len(live),
        "skipped": len(occs) - len(live),
        "words": len(aggregate_words(occs)),
    }


# =============== 后台 job 管理（与 fine_service 同模式） ===============

# task_id → {"state": "running|done|error", "stage": str, "progress": 0-100,
#            "error": str|None, "started_at": float, "finished_at": float|None,
#            "lines": int, "occurrences": int}
_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()


def job_status(task_id: str) -> dict | None:
    with _JOBS_LOCK:
        j = _JOBS.get(task_id)
        return dict(j) if j else None


def start_job(
    task_id: str,
    video_path: Path,
    hotwords: list[str],
    task_name: str,
    outputs_dir: Path,
    entry: dict | None = None,
    on_success: Callable[[dict], None] | None = None,
    *,
    auto: bool = False,
) -> bool:
    """启动优化字幕线程（ASR 识别成片 → 大模型提取 → 落盘）。已在跑 → False。

    video_path：粗剪成片（rough_compose.mp4）；entry：本次使用的模型注册项。

    REQ-20260919-075：auto 透传到 execution_history（pipeline 自动调用标记）。
    """
    with _JOBS_LOCK:
        existing = _JOBS.get(task_id)
        if existing and existing.get("state") == "running":
            return False
        _JOBS[task_id] = {
            "state": "running", "stage": "加载模型", "progress": 0.0, "error": None,
            "started_at": time.time(), "finished_at": None,
            "lines": 0, "occurrences": 0,
        }

    # REQ-20260918-053：执行日志 — 优化字幕（LLM 调用，分钟级）
    # REQ-20260919-075：auto 透传；用户手动调为 False，pipeline 自动调为 True
    exec_id = execution_history.record_start(
        outputs_dir, execution_history.KIND_OPTIMIZE,
        extra={"model": (entry or {}).get("id") or ""},
        auto=auto)

    def _run():
        job = _JOBS[task_id]

        def _stage(name: str, pct: float) -> None:
            job["stage"] = name
            job["progress"] = round(pct, 1)
            log.info("[opt][%s] %s", task_id, name)

        try:
            # ---- ① ASR 识别成片（复用字幕生成链路；不写 subtitle.json） ----
            from slirn_home import asr_service

            _stage("加载模型", 5.0)
            hotword_str = " ".join(w.strip() for w in hotwords if w and w.strip())
            _stage("识别粗剪成片", 15.0)
            state = asr_service._run_recognition(str(video_path), hotword_str, False)
            segments = asr_service.segments_from_sentences(state.get("sentences") or [])
            if not segments:
                raise RuntimeError("成片识别结果为空（无有效字幕行）")
            job["lines"] = len(segments)
            _stage("识别完成", 40.0)
            log.info("[opt][%s] 成片识别：%d 行", task_id, len(segments))

            # ---- ② 大模型分批提取不明确字词 ----
            def _analyze(batch: list[dict], strict: bool = False) -> dict[str, list[dict]]:
                """分析一批：解析失败/截断 → 减半重试；单行仍失败 → 放弃该批
                （提议性质的提取，不阻塞流程，宁缺勿错）。"""
                from slirn_home.revision_service import _call_llm

                system = OPT_SYSTEM + (OPT_STRICT_SUFFIX if strict else "")
                try:
                    raw = _call_llm(system, build_user_prompt(task_name, hotwords, batch), entry)
                    return parse_occurrences(raw, batch)
                except ValueError as e:
                    if len(batch) == 1:
                        log.warning("[opt][%s] 行 %s 分析失败（%s），跳过",
                                    task_id, batch[0].get("id"), e)
                        return {}
                    mid = len(batch) // 2
                    log.warning("[opt][%s] 批解析失败，减半重试（%d 行）: %s",
                                task_id, len(batch), e)
                    return {**_analyze(batch[:mid], strict), **_analyze(batch[mid:], strict)}

            def _analyze_all(strict: bool = False) -> dict[str, list[dict]]:
                found: dict[str, list[dict]] = {}
                for bi, batch in enumerate(batches):
                    _stage(f"大模型分析 ({bi + 1}/{len(batches)})" + ("·复检" if strict else ""),
                           40 + (bi + 1) / max(1, len(batches)) * 50)
                    found.update(_analyze(batch, strict))
                    if bi < len(batches) - 1:
                        time.sleep(0.6)  # 批间间隔，降低限流概率
                return found

            lines = [{"id": str(s["i"]), "text": str(s.get("text", ""))} for s in segments]
            batches = [lines[k:k + BATCH_SIZE] for k in range(0, len(lines), BATCH_SIZE)] or [[]]
            found = _analyze_all()
            # 全部批次零发现时严格复检一轮 — 大模型抽样波动下可能漏掉整段，
            # 复检一轮显著降低「点了开始却什么都没提出来」的概率（仍零则视为确实干净）。
            if not found and len(lines) >= 5:
                log.info("[opt][%s] 首轮零发现，严格复检一轮（%d 行）", task_id, len(lines))
                _stage("零发现复检", 92.0)
                found = _analyze_all(strict=True)

            # ---- ③ 落盘（出现项默认全部"待采纳"，applied=True 待人工改判；
            #      reviewed=False 待人工处理 — REQ-038 词级处理进度口径）----
            _stage("保存结果", 95.0)
            occurrences = []
            for rid in sorted(found, key=int):
                for occ in found[rid]:
                    occurrences.append({
                        "occ_id": len(occurrences),
                        "seg": int(rid), "pos": int(occ.get("pos", 0)),
                        "before": str(occ["before"]), "after": str(occ["after"]),
                        "reason": str(occ.get("reason") or ""),
                        "applied": True,
                        "reviewed": False,
                    })
            data = {
                "version": 1,
                "video": Path(video_path).name,
                "asr": {"model": "seaco-paraformer", "sd": False},
                "model": (entry or {}).get("id") or "qwen-plus",
                "provider": (entry or {}).get("provider", ""),
                "protocol": (entry or {}).get("protocol", "openai"),
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "saved_at": None,
                "hotwords": list(hotwords),
                "segments": segments,
                "occurrences": occurrences,
                "words": aggregate_words(occurrences),
                "mapping": build_mapping(occurrences),
                "stats": {"lines": len(segments), "occurrences": len(occurrences)},
            }
            out_dir = Path(outputs_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / OPTIMIZE_JSON).write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            job["occurrences"] = len(occurrences)
            job["state"] = "done"
            job["stage"] = "完成"
            job["progress"] = 100.0
            job["finished_at"] = time.time()
            log.info("[opt][%s] 完成：识别 %d 行 · 不明确出现 %d 处（%d 个词）",
                     task_id, len(segments), len(occurrences), len(data["words"]))
            # REQ-20260918-053：执行日志 — 补结果摘要 + 标记 success
            try:
                execution_history.patch_extra(outputs_dir, exec_id,
                                                {"lines": len(segments),
                                                 "occurrences": len(occurrences),
                                                 "words": len(data["words"])})
                # REQ-20260919-075：完成时回填具体执行情况（行数 + 词数 + 出现次数）
                execution_history.patch_fields(outputs_dir, exec_id, {
                    "description": (f"优化字幕保存：识别 {len(segments)} 行字幕，"
                                    f"提取 {len(occurrences)} 处不明确字词（{len(data['words'])} 个词）")
                })
                execution_history.record_finish(outputs_dir, exec_id, success=True, error="")
            except Exception as eh:  # noqa: BLE001
                log.warning("[opt][%s] history record_finish 失败: %s", task_id, eh)
            if on_success:
                try:
                    on_success(data)
                except Exception as e:  # noqa: BLE001 — 回调失败不影响结果
                    log.warning("[opt][%s] on_success 回调失败: %s", task_id, e)
        except Exception as e:  # noqa: BLE001 — 后台线程必须全兜底
            log.exception("[opt][%s] 优化字幕失败", task_id)
            msg = str(e)
            if isinstance(e, MemoryError) or "Unable to allocate" in msg:
                msg += "（内存不足：请关闭其他应用（如上游 FunClip 服务）后重试）"
            job["state"] = "error"
            job["error"] = msg
            job["finished_at"] = time.time()
            # REQ-20260918-053：执行日志 — 失败落盘
            try:
                execution_history.record_finish(outputs_dir, exec_id, success=False, error=msg)
            except Exception as eh:  # noqa: BLE001
                log.warning("[opt][%s] history record_finish(failed) 失败: %s", task_id, eh)

    threading.Thread(target=_run, name=f"optimize-{task_id}", daemon=True).start()
    return True


# =============== 读取 / 决策保存 ===============

def load_optimize(outputs_dir: Path) -> dict | None:
    """读取 optimize_subtitle.json（无/损坏 → None）。"""
    p = Path(outputs_dir) / OPTIMIZE_JSON
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log.warning("optimize_subtitle.json 损坏: %s", e)
        return None


def save_decisions(outputs_dir: Path, decisions: list[dict]) -> tuple[dict, int]:
    """把人工决定合并落盘。返回 (data, 生效条数)。

    decisions：[{"occ_id": int, "applied": bool, "after": str, "reviewed": bool}]
    （全量口径 — 未列出的出现项视为不采纳/未处理）。生效要求 after 非空且
    != before（人工编辑改回原文 = 不采纳，服务端兜底）。保存时重算各行
    new_text、词频与对应关系汇总，saved_at 置当前时间（阶段完成标记）。
    """
    data = load_optimize(outputs_dir)
    if data is None:
        raise RuntimeError("optimize_subtitle.json 不存在")
    dec_by_id: dict[int, dict] = {}
    for d in decisions or []:
        try:
            dec_by_id[int(d.get("occ_id"))] = d
        except (TypeError, ValueError):
            continue
    applied_n = 0
    for occ in data.get("occurrences") or []:
        d = dec_by_id.get(int(occ["occ_id"]))
        after = str((d or {}).get("after", occ.get("after")))
        want = bool((d or {}).get("applied"))
        # REQ-038：处理进度落盘 — 出现处切换过 ✓/✕ 或编辑过替换值即 reviewed
        occ["reviewed"] = bool((d or {}).get("reviewed"))
        if want and after and after != str(occ.get("before")):
            occ["applied"] = True
            occ["after"] = after  # 人工编辑过的替换值
            applied_n += 1
        else:
            occ["applied"] = False
    data["segments"] = apply_to_segments(data.get("segments") or [], data["occurrences"])
    data["words"] = aggregate_words(data["occurrences"])
    data["mapping"] = build_mapping(data["occurrences"])
    data["stats"] = effective_stats(data)
    data["saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    p = Path(outputs_dir) / OPTIMIZE_JSON
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data, applied_n
