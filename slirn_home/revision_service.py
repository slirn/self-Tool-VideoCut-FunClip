"""字幕修订服务 — REQ-20260915-005。

把上一阶段生成的 subtitle.json 交给大模型（用户在 ⚙️ 注册的当前模型，
OpenAI 兼容 / Anthropic 双协议，API Key 从环境变量读取 — REQ-008/REQ-20260916-001），
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

# 长字幕分批：每批段数（qwen-plus 上下文/输出长度安全边际；
# 20 段/批输出约 2K tokens，留足余量，解析失败还会减半重试 — REQ-20260915-007）
BATCH_SIZE = 20

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

# OpenAI 兼容 / Anthropic 错误码 → 中文说明（让用户一眼知道该怎么办 — REQ-20260915-007/008）
_CODE_ZH: dict[str, str] = {
    "Arrearage": "账户欠费/余额不足，请到对应厂商控制台充值后重试",
    "insufficient_quota": "配额/余额不足，请到对应厂商控制台充值后重试",
    "InvalidApiKey": "API Key 无效，请检查对应环境变量中的 Key",
    "invalid_api_key": "API Key 无效，请检查对应环境变量中的 Key",
    "authentication_error": "API Key 无效，请检查对应环境变量中的 Key",
    "Throttling": "调用被限流（请求过频或配额用尽），请稍后重试",
    "rate_limit_exceeded": "调用被限流，请稍后重试",
    "rate_limit_error": "调用被限流，请稍后重试",
    "InvalidParameter": "请求参数不合法",
    "invalid_request_error": "请求参数不合法",
    "AccessDenied": "无访问权限（可能未开通对应模型服务）",
    "permission_error": "无访问权限（可能未开通对应模型服务）",
    "DataInspectionFailed": "内容安全审查未通过",
    "model_not_found": "模型不存在（检查模型名是否为该厂商提供）",
    "not_found_error": "模型不存在（检查模型名是否为该厂商提供）",
    "overloaded_error": "服务端过载，请稍后重试",
}


def _code_zh(code: str, status: int) -> str:
    if code in _CODE_ZH:
        return _CODE_ZH[code]
    if code and code.startswith("Throttling"):
        return _CODE_ZH["Throttling"]
    return f"HTTP {status}"


class LLMBusinessError(RuntimeError):
    """确定性业务失败（欠费/Key 无效/模型不存在等）— 重试无意义，直接上抛。

    限流/过载类（Throttling*/rate_limit*/overloaded_error）仍抛普通
    RuntimeError，由 _chat_completion 退避重试。
    """


def _is_retryable_code(code: str) -> bool:
    c = str(code or "")
    return (c in ("rate_limit_exceeded", "rate_limit_error", "overloaded_error")
            or c.startswith("Throttling"))


def _extract_openai_resp(resp, entry: dict) -> str:
    """解析 OpenAI 兼容响应 → 助手正文。

    网关业务失败（欠费/限流/Key 无效）返回非 200 + error 对象而非抛异常 —
    必须先查状态码，否则真实错误被掩盖（REQ-20260915-007）。
    输出被截断（finish_reason=length）→ ValueError（上层减半重试）。
    """
    model = entry.get("id", "?")
    provider = entry.get("provider", "未知厂商")
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001 — 非 JSON 响应按原文报错
        data = None
    if resp.status_code != 200:
        err = data.get("error") if isinstance(data, dict) else None
        err = err if isinstance(err, dict) else {}
        code = str(err.get("code") or resp.status_code)
        msg = str(err.get("message") or resp.text[:300] or "无错误详情")
        rid = resp.headers.get("x-request-id") or resp.headers.get("X-Request-Id") or ""
        err_cls = RuntimeError if _is_retryable_code(code) else LLMBusinessError
        raise err_cls(
            f"{provider}（{model}）[{code}] {_code_zh(code, resp.status_code)}：{msg}"
            + (f"（request_id={rid}）" if rid else "")
        )
    if not isinstance(data, dict):
        raise LLMBusinessError(f"{provider}（{model}）响应不是 JSON 对象")
    if data.get("error"):  # 个别网关 200 也带 error
        err = data.get("error")
        err = err if isinstance(err, dict) else {}
        raise LLMBusinessError(
            f"{provider}（{model}）[{err.get('code') or 'Error'}] {err.get('message') or ''}"
        )
    choices = data.get("choices") or []
    if not isinstance(choices, list) or not choices:
        raise RuntimeError(f"{provider}（{model}）响应缺少 choices 正文")
    first = choices[0] if isinstance(choices[0], dict) else {}
    if first.get("finish_reason") == "length":
        raise ValueError("模型输出被截断（finish_reason=length），需减小批量重试")
    content = (first.get("message") or {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(f"{provider}（{model}）响应正文为空")
    return content


def _extract_anthropic_resp(resp, entry: dict) -> str:
    """解析 Anthropic 协议响应（``{base_url}/v1/messages``）→ 助手正文。

    错误体 ``{"type":"error","error":{"type","message"}}``；截断
    ``stop_reason == "max_tokens"`` → ValueError（上层减半重试）。
    """
    model = entry.get("id", "?")
    provider = entry.get("provider", "未知厂商")
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001 — 非 JSON 响应按原文报错
        data = None
    if resp.status_code != 200:
        err = data.get("error") if isinstance(data, dict) else None
        err = err if isinstance(err, dict) else {}
        code = str(err.get("type") or resp.status_code)
        msg = str(err.get("message") or resp.text[:300] or "无错误详情")
        rid = resp.headers.get("request-id") or resp.headers.get("x-request-id") or ""
        err_cls = RuntimeError if _is_retryable_code(code) else LLMBusinessError
        raise err_cls(
            f"{provider}（{model}）[{code}] {_code_zh(code, resp.status_code)}：{msg}"
            + (f"（request_id={rid}）" if rid else "")
        )
    if not isinstance(data, dict):
        raise LLMBusinessError(f"{provider}（{model}）响应不是 JSON 对象")
    if data.get("type") == "error":  # 个别网关 200 也带 error
        err = data.get("error")
        err = err if isinstance(err, dict) else {}
        raise LLMBusinessError(
            f"{provider}（{model}）[{err.get('type') or 'Error'}] {err.get('message') or ''}"
        )
    content = data.get("content")
    text = ""
    if isinstance(content, list):
        text = "".join(
            p.get("text", "") for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    if data.get("stop_reason") == "max_tokens":
        raise ValueError("模型输出被截断（stop_reason=max_tokens），需减小批量重试")
    if not text.strip():
        raise RuntimeError(f"{provider}（{model}）响应正文为空")
    return text


def _chat_completion(entry: dict, messages: list[dict],
                     retries: int = 2, timeout: float = 180.0) -> str:
    """按注册项协议调用大模型（httpx，gradio 既有依赖）— REQ-20260916-001 双协议。

    entry: llm_config 注册项 {"id","provider","base_url","api_key_env","protocol"}
    - protocol=openai（默认）：POST ``{base_url}/chat/completions`` + Bearer，
      body {model, messages, stream:false}
    - protocol=anthropic：POST ``{base_url}/v1/messages``（base 已带 /v1 则
      ``{base_url}/messages``），headers x-api-key + anthropic-version，
      body {model, max_tokens, system, messages}（system 提到顶层，max_tokens 必填）

    Key 从 entry.api_key_env 指定的系统环境变量读取（界面不存储 Key）。
    瞬时失败退避重试；确定性业务错误 → LLMBusinessError 直接上抛；截断 →
    ValueError（上层减半）。
    """
    import httpx

    env = entry.get("api_key_env", "")
    key = os.environ.get(env, "")
    if not key:
        raise RuntimeError(f"未配置环境变量 {env}（{entry.get('provider', '未知厂商')} 的 API Key）")

    protocol = str(entry.get("protocol") or "openai").strip().lower()
    base = str(entry.get("base_url", "")).rstrip("/")
    if protocol == "anthropic":
        if base.endswith("/v1/messages"):
            url = base
        elif base.endswith("/v1"):
            url = base + "/messages"
        else:
            url = base + "/v1/messages"
        system = "\n".join(m.get("content", "") for m in messages if m.get("role") == "system")
        body = {
            "model": entry.get("id", ""),
            "max_tokens": 4096,  # Anthropic 必填；20 段/批输出约 2K tokens，留余量
            "messages": [m for m in messages if m.get("role") != "system"],
        }
        if system:
            body["system"] = system
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        extract = _extract_anthropic_resp
    else:
        url = base if base.endswith("/chat/completions") else base + "/chat/completions"
        body = {"model": entry.get("id", ""), "messages": messages, "stream": False}
        headers = {"Authorization": f"Bearer {key}"}
        extract = _extract_openai_resp

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = httpx.post(url, json=body, headers=headers, timeout=timeout)
            return extract(resp, entry)
        except ValueError:
            raise  # 截断等输出质量问题：同批重试无意义，直接交上层减半
        except LLMBusinessError:
            raise  # 确定性业务失败（欠费/Key 无效等）：重试同样失败，直接上抛
        except Exception as e:  # noqa: BLE001 — httpx 异常类型不稳定
            last_err = e
            if attempt < retries:
                wait = 2.0 * (attempt + 1)
                log.warning("[revise] LLM 调用失败（第 %d 次，%.0fs 后重试）: %s", attempt + 1, wait, e)
                time.sleep(wait)
    raise RuntimeError(f"网络/服务端错误（重试 {retries} 次后仍失败）: {last_err}")


def _call_llm(system: str, user: str, entry: dict | None = None, retries: int = 2) -> str:
    """调用注册的大模型 → 文本响应。瞬时失败退避重试（2s/4s）。

    entry: llm_config 注册项（app.py 传入用户选择的当前模型，REQ-20260915-008）；
    None 时回退默认注册项 + SLIRN_LLM_MODEL 环境变量覆盖模型 id（向后兼容）。
    """
    if entry is None:
        from slirn_home.llm_config import DEFAULT_MODELS

        entry = dict(DEFAULT_MODELS[0])
        entry["id"] = os.environ.get("SLIRN_LLM_MODEL", "").strip() or entry["id"]
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    try:
        return _chat_completion(entry, messages, retries=retries)
    except ValueError:
        raise  # 截断：上层减半重试
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"大模型调用失败[{entry.get('id', '?')}]（{entry.get('provider', '')}）: {e}"
        ) from e


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
    entry: dict | None = None,
) -> bool:
    """启动大模型分析线程。已在跑 → False。成功后写 revision.json（决策重置 pending）。

    entry：本次分析使用的模型注册项 {"id","provider","base_url","api_key_env"}
    （app.py 传入用户选择的当前模型，REQ-20260915-008），全程使用并写入
    revision.json meta 留痕。
    """
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

        def _analyze(batch: list[dict]) -> list[dict]:
            """分析一批：解析失败/截断 → 减半重试；单段仍失败 → 回填 review。

            确定性失败（欠费/Key 无效等 RuntimeError）向上抛 → job 终止并透传原因
            —— 这种错误每批都会失败，减半只是浪费调用（REQ-20260915-007）。
            """
            try:
                raw = _call_llm(_SYSTEM_PROMPT, build_user_prompt(task_name, hotwords, batch), entry)
                return parse_llm_suggestions(raw, batch)
            except ValueError as e:
                if len(batch) == 1:
                    log.warning("[revise][%s] 段 %s 分析失败（%s），回填人工复核", task_id, batch[0].get("i"), e)
                    return [{
                        "i": int(batch[0]["i"]), "category": "review", "keep_text": None,
                        "note": f"模型分析失败（{e}），请人工复核",
                    }]
                mid = len(batch) // 2
                log.warning("[revise][%s] 批解析失败，减半重试（%d 段）: %s", task_id, len(batch), e)
                return _analyze(batch[:mid]) + _analyze(batch[mid:])

        try:
            batches = [segments[k:k + BATCH_SIZE] for k in range(0, len(segments), BATCH_SIZE)] or [[]]
            suggestions: list[dict] = []
            for bi, batch in enumerate(batches):
                _stage(f"调用大模型 ({bi + 1}/{len(batches)})")
                suggestions.extend(_analyze(batch))
                if bi < len(batches) - 1:
                    time.sleep(0.6)  # 批间间隔，降低限流概率

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
                "model": (entry or {}).get("id") or "qwen-plus",
                "provider": (entry or {}).get("provider", ""),
                "protocol": (entry or {}).get("protocol", "openai"),
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
