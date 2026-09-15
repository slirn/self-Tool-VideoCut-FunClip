"""字幕修订服务 — REQ-20260915-005。

把上一阶段生成的 subtitle.json 交给大模型（DashScope qwen，DASHSCOPE_API_KEY），
逐段产出处理建议（keep 保留 / delete 整行删除 / split 切分修剪 / review 人工复核），
落盘 outputs/revision.json；用户逐条决策（decision + user_note）合并回写。
后台线程 job 管理与 asr_service 同模式（内存 job 表 + 阶段级进度）。

设计见 docs/design/DESIGN-20260915-005-subtitle-review.md。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

REVISION_JSON = "revision.json"

# 模型建议类别 → (中文标签, 徽章色)
LLM_CATEGORIES: dict[str, tuple[str, str]] = {
    "keep": ("完整保留", "keep"),
    "delete": ("整行删除", "delete"),
    "split": ("切分修剪", "split"),
    "review": ("人工复核", "review"),
}

# 手动决策类别 → 中文标签（pending = 未决策）
USER_DECISIONS: dict[str, str] = {
    "pending": "未决策",
    "accept": "采纳建议",
    "keep": "保留",
    "delete": "删除",
    "split": "切分",
}

# 长字幕分批：每批段数（qwen-plus 上下文/输出长度安全边际）
BATCH_SIZE = 40

_SYSTEM_PROMPT = """你是专业的视频字幕修订顾问。用户会给你视频字幕段列表（JSON 数组，每项含序号 i、起止时间、文本）。请对**每一段**判断修订方式，并给出具体分析说明。

判定标准：
- "keep"：有效内容，完整保留。
- "delete"：整行删除 — 纯口癖、口头禅、语气词（嗯、啊、呃、那个、就是说、然后等）、无意义寒暄、与主题无关的废话、整行都是重复啰嗦。
- "split"：行内需进一步切分/修剪 — 例如一句话中重复多次的内容只保留一次；有效内容中夹杂的语气词应剔除。keep_text 字段给出建议保留后的文本。
- "review"：无法判断（语义不明、可能依赖上下文）。

输出要求：只输出 JSON 数组，不要任何其他文字。每项格式：
{"i": 段序号, "category": "keep|delete|split|review", "keep_text": "建议保留的文本（仅 split 需要，其余为 null）", "note": "具体分析说明：指出问题词、为何删/留/切，切分的依据"}

note 必须具体（例如：「行首『嗯』为语气词；『大家好』重复 2 次建议保留 1 次」），不要泛泛而谈。必须覆盖输入的每一个 i。"""


def build_user_prompt(task_name: str, hotwords: list[str], segments: list[dict]) -> str:
    """构造 user 消息：任务上下文 + 热词（保护专有名词不被误删）+ 段列表。"""
    lines = [f"视频任务：{task_name or '（未命名）'}"]
    if hotwords:
        lines.append(f"任务热词（专有名词/人名等，判定时注意不要误删）：{'、'.join(hotwords)}")
    payload = [
        {"i": int(s["i"]), "start": s.get("start", ""), "end": s.get("end", ""), "text": s.get("text", "")}
        for s in segments
    ]
    lines.append("字幕段列表：")
    lines.append(json.dumps(payload, ensure_ascii=False))
    return "\n".join(lines)


# =============== 大模型调用（拆出便于测试 monkeypatch） ===============

def _call_llm(system: str, user: str, retries: int = 1) -> str:
    """DashScope qwen 调用 → 文本响应。网络类失败重试 1 次。"""
    import dashscope
    from dashscope import Generation

    key = os.environ.get("DASHSCOPE_API_KEY", "")
    if not key:
        raise RuntimeError("未配置 DASHSCOPE_API_KEY（无法调用大模型）")
    model = os.environ.get("SLIRN_LLM_MODEL", "qwen-plus")
    dashscope.api_key = key
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = Generation.call(
                model, messages=messages, result_format="message",
                stream=False, incremental_output=False,
            )
            return resp["output"]["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001 — dashscope 异常类型不稳定
            last_err = e
            if attempt < retries:
                log.warning("[revise] LLM 调用失败（第 %d 次）: %s", attempt + 1, e)
                time.sleep(1.5)
    raise RuntimeError(f"大模型调用失败: {last_err}")


_FENCE_RE = re.compile(r"```(?:json)?\s*|\s*```")


def parse_llm_suggestions(raw: str, segments: list[dict]) -> list[dict]:
    """防御式解析模型输出 → 与段一一对应的建议列表。

    - 剥 ```json 围栏；截取首个 '[' 到最后一个 ']'（容忍前后废话）
    - 未知类别 → review；缺 i / i 不在段内 → 丢弃
    - 模型漏答的段 → 回填 review（保证每段必有建议 — REQ-5.2）
    """
    by_id = {int(s["i"]): s for s in segments}
    parsed: dict[int, dict] = {}
    cleaned = _FENCE_RE.sub("", raw or "").strip()
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start >= 0 and end > start:
        cleaned = cleaned[start:end + 1]
    try:
        arr = json.loads(cleaned)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"模型输出不是合法 JSON: {e}") from e
    if not isinstance(arr, list):
        raise ValueError("模型输出不是 JSON 数组")
    for item in arr:
        if not isinstance(item, dict):
            continue
        try:
            i = int(item.get("i"))
        except (TypeError, ValueError):
            continue
        if i not in by_id:
            continue
        cat = str(item.get("category") or "").strip().lower()
        if cat not in LLM_CATEGORIES:
            cat = "review"
        keep_text = item.get("keep_text") if cat == "split" else None
        if cat == "split" and not (isinstance(keep_text, str) and keep_text.strip()):
            keep_text = None
        note = str(item.get("note") or "").strip()
        if not note:
            note = "模型未给出说明"
        parsed[i] = {"category": cat, "keep_text": keep_text, "note": note[:500]}
    # 漏答回填
    out: list[dict] = []
    for s in segments:
        i = int(s["i"])
        sug = parsed.get(i) or {
            "category": "review", "keep_text": None, "note": "模型未返回该段，请人工复核",
        }
        out.append({"i": i, **sug})
    return out


# =============== 后台 job 管理（与 asr_service 同模式） ===============

# task_id → {"state": "running|done|error", "stage": str, "error": str|None,
#            "started_at": float, "finished_at": float|None, "entries_count": int}
_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()


def job_status(task_id: str) -> dict | None:
    with _JOBS_LOCK:
        j = _JOBS.get(task_id)
        return dict(j) if j else None


def start_job(
    task_id: str,
    segments: list[dict],
    task_name: str,
    hotwords: list[str],
    outputs_dir: Path,
    on_success: Callable[[list[dict]], None] | None = None,
) -> bool:
    """启动大模型分析线程。已在跑 → False。成功后写 revision.json（决策重置 pending）。"""
    with _JOBS_LOCK:
        existing = _JOBS.get(task_id)
        if existing and existing.get("state") == "running":
            return False
        _JOBS[task_id] = {
            "state": "running", "stage": "准备提示词", "error": None,
            "started_at": time.time(), "finished_at": None, "entries_count": 0,
        }

    def _run():
        job = _JOBS[task_id]

        def _stage(name: str) -> None:
            job["stage"] = name
            log.info("[revise][%s] %s", task_id, name)

        try:
            batches = [segments[k:k + BATCH_SIZE] for k in range(0, len(segments), BATCH_SIZE)] or [[]]
            suggestions: list[dict] = []
            for bi, batch in enumerate(batches):
                _stage(f"调用大模型 ({bi + 1}/{len(batches)})")
                user = build_user_prompt(task_name, hotwords, batch)
                raw = _call_llm(_SYSTEM_PROMPT, user)
                _stage(f"解析结果 ({bi + 1}/{len(batches)})")
                suggestions.extend(parse_llm_suggestions(raw, batch))

            _stage("保存结果")
            entries = []
            for s, sug in zip(segments, suggestions):
                entries.append({
                    "i": int(s["i"]),
                    "start_ms": int(s.get("start_ms", 0)), "end_ms": int(s.get("end_ms", 0)),
                    "start": s.get("start", ""), "end": s.get("end", ""),
                    "text": s.get("text", ""),
                    "category": sug["category"], "keep_text": sug["keep_text"], "note": sug["note"],
                    "decision": "pending", "user_note": "",
                })
            meta = {
                "version": 1,
                "model": os.environ.get("SLIRN_LLM_MODEL", "qwen-plus"),
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "saved_at": None,
                "segments_count": len(entries),
                "entries": entries,
            }
            outputs_dir.mkdir(parents=True, exist_ok=True)
            (outputs_dir / REVISION_JSON).write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            job["entries_count"] = len(entries)
            job["state"] = "done"
            job["stage"] = "完成"
            job["finished_at"] = time.time()
            log.info("[revise][%s] 完成：%d 条建议", task_id, len(entries))
            if on_success:
                try:
                    on_success(entries)
                except Exception as e:  # noqa: BLE001
                    log.warning("[revise][%s] on_success 回调失败: %s", task_id, e)
        except Exception as e:  # noqa: BLE001 — 后台线程必须全兜底
            log.exception("[revise][%s] 分析失败", task_id)
            job["state"] = "error"
            job["error"] = str(e)
            job["finished_at"] = time.time()

    threading.Thread(target=_run, name=f"revise-{task_id}", daemon=True).start()
    return True


# =============== 读取 / 决策合并 ===============

def load_revision(outputs_dir: Path) -> dict | None:
    """读取 revision.json（无/损坏 → None）。"""
    p = Path(outputs_dir) / REVISION_JSON
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log.warning("revision.json 损坏: %s", e)
        return None


def merge_decisions(revision: dict, decisions: list[dict]) -> tuple[dict, int]:
    """把用户决策合并进 revision（不落盘）。返回 (新 meta, 生效条数)。

    decisions: [{"i": int, "decision": str, "user_note": str}]；
    非法 decision / 未知 i 的条目跳过。
    """
    by_id = {int(e["i"]): e for e in revision.get("entries", [])}
    applied = 0
    for d in decisions or []:
        try:
            i = int(d.get("i"))
        except (TypeError, ValueError):
            continue
        entry = by_id.get(i)
        if entry is None:
            continue
        dec = str(d.get("decision") or "").strip().lower()
        if dec not in USER_DECISIONS:
            continue
        entry["decision"] = dec
        entry["user_note"] = str(d.get("user_note") or "").strip()[:500]
        applied += 1
    return revision, applied


def all_decided(revision: dict) -> bool:
    """全部条目 decision != pending（修订完成的判定 — REQ-5.4）。"""
    entries = revision.get("entries") or []
    return bool(entries) and all(e.get("decision") != "pending" for e in entries)
