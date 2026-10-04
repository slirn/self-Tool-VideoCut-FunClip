"""短视频工作台 Stage 3（AI 拆条）执行模块。

REQ-20261003-098：第 3 阶段用 LLM 读取 ``stage2/raw.srt``，按用户选的 prompt 模板
输出 3-5 条 highlights，每条：

  - 起始/结束毫秒（覆盖回源视频坐标）
  - 字幕行重组（``subtitle_lines`` 每条 ``src_index`` 引用 raw.srt 行号）
  - 自定义标题（用户可改）
  - 时长目标 60 秒 ±20 容差

模板（用户在 UI 选；用户也能自带覆盖）：

- ``hook_first`` —— Hook 优先型；第一条放最抓人的钩子
- ``topic_cluster`` —— 主题聚类；按内容主题分成独立"知识小卡"
- ``story_arc`` —— 起承转合；每条都是完整的小故事弧

LLM 客户端沿用 ``revision_service._call_llm``（注册模型 + 默认 deepseek）。
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

from slirn_home import short_video_service as svc

log = logging.getLogger(__name__)


# ---------- Prompt 模板 ----------

_TEMPLATE_BASE_SYSTEM = (
    "你是一个短视频内容导演，输入是一段长视频的 SRT 字幕。你的任务是"
    "从字幕里找出 {n_clips} 个最适合短视频传播的精彩片段，**每个片段约 60 秒**（±20 秒），"
    "片段之间相对独立。\n\n"
    "关键要求（务必遵守）：\n"
    "1. **字幕重组**：每条 highlight 的 ``subtitle_lines`` 不是源字幕的连续区间复制，"
    "而是把多个零散句按主题/叙事重组；可跨源字幕任意位置挑行。"
    "2. **重新排序**：highlights 数组的顺序**不按源时间顺序**，按传播效果排序（钩子/主题/故事节奏）。"
    "3. **时间戳有效**：``start_ms`` / ``end_ms`` 必须是源字幕中真实存在的毫秒（可取子条目实际首尾），"
    "不能凭空指定。每条覆盖时长 60 秒 ±20。"
    "4. ``src_index`` 引用源字幕行号（1-based），只能引用实际存在的行；文本字段可微调但要保留原意。\n\n"
    "输出严格 JSON：\n"
    "{{\n"
    "  \"highlights\": [\n"
    "    {{\n"
    "      \"id\": \"h1\",\n"
    "      \"title\": \"短小有钩子的标题\",\n"
    "      \"start_ms\": 12345,\n"
    "      \"end_ms\": 72345,\n"
    "      \"subtitle_lines\": [\n"
    "        {{\"src_index\": 12, \"text\": \"原句 1\"}},\n"
    "        {{\"src_index\": 5,  \"text\": \"原句 2\"}}\n"
    "      ]\n"
    "    }}, ...\n"
    "  ]\n"
    "}}"
)


_TEMPLATE_HOOK_FIRST = (
    _TEMPLATE_BASE_SYSTEM
    + "\n\n# 排序策略：Hook-First\n"
    "第一条放视频里**最抓人、最有冲击力**的一段（往往是把悬念 / 反转 / 痛点浓缩到 60 秒）。\n"
    "剩下的 highlights 按「情绪强度」或「逻辑递进」排序（不必按源时间顺序）。\n"
    "适合：推广 / 拉新 / 悬念向 / 卖货向 短视频。"
)


_TEMPLATE_TOPIC_CLUSTER = (
    _TEMPLATE_BASE_SYSTEM
    + "\n\n# 排序策略：Topic-Cluster\n"
    "按字幕里的主题聚成 4-5 个簇，每条 highlight 是一个**独立的知识小卡**。\n"
    "从不同源字幕位置挑选与该簇主题最相关的句子（可穿插），形成该主题的浓缩讲解。\n"
    "适合：教程 / 知识科普 / 干货 短视频。"
)


_TEMPLATE_STORY_ARC = (
    _TEMPLATE_BASE_SYSTEM
    + "\n\n# 排序策略：Story-Arc（起承转合）\n"
    "每条 highlight 都是一个**完整的小故事弧**：起承转合四段。\n"
    "从源视频挑 4 个具备完整叙事弧的段；每条内部的字幕行按起承转合顺序排列。\n"
    "适合：故事 / 人物访谈 / 案例分享 短视频。"
)


_PROMPT_TEMPLATES = {
    svc.HIGHLIGHT_TEMPLATE_HOOK_FIRST: _TEMPLATE_HOOK_FIRST,
    svc.HIGHLIGHT_TEMPLATE_TOPIC_CLUSTER: _TEMPLATE_TOPIC_CLUSTER,
    svc.HIGHLIGHT_TEMPLATE_STORY_ARC: _TEMPLATE_STORY_ARC,
}


# ---------- SRT 解析 + 校验 ----------

_SRT_TIME_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)


@dataclass
class SrtLine:
    index: int          # 1-based
    start_ms: int
    end_ms: int
    text: str


def _parse_srt_lines(text: str) -> list[SrtLine]:
    """行级 SRT 解析：每行包含一个 -->/时间戳对就视为一条字幕。

    兼容两种格式：
    1. 标准 SRT：块间空行；每块第一行序号、第二行时间戳、剩余行文本
    2. funasr 直出：可能缺空行、缺序号；按行扫，扫到时间戳就开新条目
    """
    lines = text.splitlines()
    out: list[SrtLine] = []
    cur_index = 0
    cur_time: tuple[int, int] | None = None
    cur_text: list[str] = []

    def _flush() -> None:
        nonlocal cur_time, cur_text, cur_index
        if cur_time is not None:
            text_joined = " ".join(x.strip() for x in cur_text if x.strip()).strip()
            if text_joined:
                out.append(SrtLine(cur_index, cur_time[0], cur_time[1], text_joined))
        cur_time = None
        cur_text = []

    for line in lines:
        s = line.strip()
        if not s:
            _flush()
            continue
        m = _SRT_TIME_RE.search(s)
        if m:
            h1, mn1, s1, ms1, h2, mn2, s2, ms2 = m.groups()
            start = (int(h1) * 3600 + int(mn1) * 60 + int(s1)) * 1000 + int(ms1)
            end = (int(h2) * 3600 + int(mn2) * 60 + int(s2)) * 1000 + int(ms2)
            if cur_time is not None:
                _flush()
            cur_time = (start, end)
            cur_index += 1
            continue
        if s.isdigit() and len(s) <= 4:
            # 序号行：忽略（不重置 cur_index）
            continue
        cur_text.append(s)
    _flush()
    return out


def _validate_highlights(payload: dict, srt_lines: list[SrtLine]) -> tuple[list[dict], list[str]]:
    """校验 LLM 返回的高亮：要求每条 start_ms/end_ms 对得上 srt_lines，
    subtitle_lines 的 src_index 引用真实存在 **且时间区间在 [start_ms, end_ms] 内**。

    返回 (validated_highlights, warnings)。
    """
    raw_highlights = payload.get("highlights") or []
    if not isinstance(raw_highlights, list) or not raw_highlights:
        raise svc.ShortVideoError("LLM 未返回有效 highlights 列表")

    warnings: list[str] = []
    valid_srt_indices = {line.index for line in srt_lines}
    srt_index_to_line = {line.index: line for line in srt_lines}

    validated: list[dict] = []
    for i, hl in enumerate(raw_highlights[:5], start=1):
        if not isinstance(hl, dict):
            warnings.append(f"highlight #{i} 不是 dict，已跳过")
            continue
        try:
            start_ms = int(hl.get("start_ms") or 0)
            end_ms = int(hl.get("end_ms") or 0)
        except (TypeError, ValueError):
            warnings.append(f"highlight #{i} 时间戳无效，已跳过")
            continue
        if end_ms <= start_ms:
            warnings.append(f"highlight #{i} end_ms <= start_ms，已跳过")
            continue
        duration = end_ms - start_ms
        if duration < 10000 or duration > 120000:
            warnings.append(f"highlight #{i} 时长 {duration}ms 不在 [10s, 120s] 区间")
            # 不直接丢，但记 warning 留给用户后续编辑

        # 把 start_ms / end_ms 吸附到最近的字幕行时间戳（误差 < 500ms 才吸附）
        if srt_lines:
            snap_start = min(srt_lines, key=lambda ln: abs(ln.start_ms - start_ms))
            snap_end = min(srt_lines, key=lambda ln: abs(ln.end_ms - end_ms))
            if abs(snap_start.start_ms - start_ms) < 500:
                start_ms = snap_start.start_ms
            if abs(snap_end.end_ms - end_ms) < 500:
                end_ms = snap_end.end_ms

        # 字幕行：每条必须有 src_index；引用合法 + 时间区间在 [start_ms, end_ms] 内才采纳
        # 200ms 容差对应 snap 后 start_ms/end_ms 与最近 SRT 行的可能偏差。
        TOL_MS = 200
        subtitle_lines: list[dict] = []
        for sl in (hl.get("subtitle_lines") or []):
            if not isinstance(sl, dict):
                continue
            try:
                src_index = int(sl.get("src_index") or 0)
            except (TypeError, ValueError):
                continue
            if src_index not in valid_srt_indices:
                warnings.append(f"highlight #{i} 引用了不存在的 src_index={src_index}，跳过该行")
                continue
            line = srt_index_to_line[src_index]
            # 该 SRT 行必须落在 [start_ms - TOL, end_ms + TOL] 内
            in_range = (
                line.start_ms >= start_ms - TOL_MS
                and line.end_ms <= end_ms + TOL_MS
            )
            if not in_range:
                warnings.append(
                    f"highlight #{i} src_index={src_index} 时间 [{line.start_ms},{line.end_ms}]"
                    f" 不在 clip [{start_ms},{end_ms}] 内，跳过该行"
                )
                continue
            text = str(sl.get("text") or line.text).strip()
            subtitle_lines.append({"src_index": src_index, "text": text})

        if not subtitle_lines:
            # 兜底：直接用 src_index 范围内的所有 srt_lines 行（rearrange fallback）
            in_range = [
                ln for ln in srt_lines
                if ln.start_ms >= start_ms and ln.end_ms <= end_ms
            ]
            if in_range:
                subtitle_lines = [{"src_index": ln.index, "text": ln.text} for ln in in_range[:6]]
                warnings.append(f"highlight #{i} LLM 未给有效 subtitle_lines，回退到区间内行")

        validated.append({
            "index": i,
            "id": str(hl.get("id") or f"h{i}")[:40],
            "title": str(hl.get("title") or f"片段 {i}")[:80],
            "start_ms": start_ms,
            "end_ms": end_ms,
            "duration_ms": end_ms - start_ms,
            "subtitle_lines": subtitle_lines,
        })
    if not validated:
        raise svc.ShortVideoError("LLM 返回的高亮全部无效")
    return validated, warnings


def clean_highlight_subtitles(
    highlight: dict,
    srt_index_to_line: dict[int, SrtLine],
    *,
    tol_ms: int = 200,
) -> tuple[list[dict], list[str]]:
    """就地校验一条已存在 highlight 的 subtitle_lines：剔除 src_index 对应 SRT
    行时间不在 [start_ms, end_ms]（±tol_ms 容差）内的项。

    返回 (cleaned_subtitle_lines, warnings)。Caller 自行决定是否覆盖原数据。
    """
    start_ms = int(highlight.get("start_ms") or 0)
    end_ms = int(highlight.get("end_ms") or 0)
    cleaned: list[dict] = []
    warnings: list[str] = []
    for sl in (highlight.get("subtitle_lines") or []):
        if not isinstance(sl, dict):
            continue
        try:
            src_index = int(sl.get("src_index") or 0)
        except (TypeError, ValueError):
            continue
        line = srt_index_to_line.get(src_index)
        if line is None:
            warnings.append(f"src_index={src_index} 不存在")
            continue
        if not (line.start_ms >= start_ms - tol_ms and line.end_ms <= end_ms + tol_ms):
            warnings.append(
                f"src_index={src_index} 时间 [{line.start_ms},{line.end_ms}]"
                f" 不在 clip [{start_ms},{end_ms}] 内"
            )
            continue
        cleaned.append({"src_index": src_index, "text": str(sl.get("text") or line.text).strip()})
    return cleaned, warnings


# ---------- LLM 调度 ----------

def _strip_json_block(raw: str) -> dict:
    """从 LLM 文本里抠 JSON object；容错代码块标记。

    REQ-20261004-bugfix：处理三类历史见过的 LLM 输出异常：

    1. 含 U+FFFD（httpx 已用 errors='replace' 替换无效 UTF-8）；
       直接清掉，通常无害。
    2. **多个顶层对象用逗号分隔**：`{"highlights":[{h1}]},{"highlights":[{h2}]}`；
       老逻辑只取首尾 {}，中间对象被截掉 + 出现 "Extra data"。
       这里尝试「逐个对象切分、合并 highlights 数组」。
    3. 顶层直接是数组 `[…]` 而非对象；正常 json.loads 即可。

    解析失败时给出 position + context 方便排查。
    """
    text = re.sub(r"```(?:json)?\s*|\s*```", "", raw or "").strip()
    text = text.replace("�", "")
    # 顶层直接是数组：少数 LLM 直接返回 [...]（无外层 {} 包裹）
    arr0 = text.find("[")
    brace0 = text.find("{")
    if arr0 >= 0 and (brace0 < 0 or arr0 < brace0):
        arr_end = text.rfind("]")
        if arr_end > arr0:
            try:
                arr = json.loads(text[arr0 : arr_end + 1])
                if isinstance(arr, list):
                    return {"highlights": arr}
            except json.JSONDecodeError:
                pass  # 落到下面的对象路径再试
    start = brace0
    if start < 0:
        raise svc.ShortVideoError("LLM 未返回 JSON 对象")

    # 切分多个顶层对象：用 brace 配对逐个截取
    objs: list[dict] = []
    i = start
    n = len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth = 0
        in_str = False
        esc = False
        j = i
        while j < n:
            c = text[j]
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = not in_str
            elif not in_str:
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        break
            j += 1
        if depth != 0 or j >= n:
            break  # brace 不配对，停止切分
        try:
            obj = json.loads(text[i : j + 1])
            if isinstance(obj, dict):
                objs.append(obj)
        except json.JSONDecodeError:
            # 单个对象解析失败，跳过继续
            pass
        i = j + 1
        # 跳过 , 或空白
        while i < n and text[i] in " \t\r\n,":
            i += 1

    if not objs:
        # 切分不出来：最后试一次整段解析并给出 position context
        end = text.rfind("}")
        if end <= start:
            raise svc.ShortVideoError("LLM 未返回 JSON 对象")
        try:
            obj = json.loads(text[start : end + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError as e:
            ctx_start = max(0, e.pos - 40)
            ctx_end = min(len(text), e.pos + 40)
            ctx = text[start + ctx_start : start + ctx_end]
            raise svc.ShortVideoError(
                f"LLM 返回值无法解析: {e.msg} (pos {e.pos}, context: …{ctx!r}…)"
            ) from e
        raise svc.ShortVideoError("LLM 返回值无法解析（无有效 JSON 对象）")

    # 单对象：直接返回
    if len(objs) == 1:
        return objs[0]

    # 多对象：合并 highlights 数组（其它字段取第一个）
    merged: dict = dict(objs[0])
    highlights: list = []
    for o in objs:
        h = o.get("highlights")
        if isinstance(h, list):
            highlights.extend(h)
    if highlights:
        merged["highlights"] = highlights
    return merged


def _call_highlights_llm(
    system_prompt: str,
    user_payload: str,
    *,
    entry: dict | None,
) -> str:
    """调 revision_service._call_llm。"""
    from slirn_home import revision_service
    return revision_service._call_llm(
        system_prompt, user_payload, entry=entry, retries=1,
    )


# ---------- 主流程 ----------

@dataclass
class AnalyzeResult:
    template: str
    model: str
    highlights: list[dict]
    warnings: list[str]
    raw_llm: str
    elapsed_sec: int


def run_stage3(
    repo_root: Path | str,
    task_id: str,
    project_id: str,
    *,
    template: str = svc.HIGHLIGHT_TEMPLATE_HOOK_FIRST,
    n_clips: int = 4,
    custom_prompt: str = "",
    entry: dict | None = None,
) -> dict:
    """Stage 3: LLM 读 raw.srt → ``stage3/highlights.json`` + ``stage3/llm_raw.txt``。

    Args:
        template: hook_first / topic_cluster / story_arc / custom
        n_clips: 期望生成的片段数（3-5），写到 prompt 里
        custom_prompt: 当 template="custom" 时使用；否则忽略
        entry: llm_config 注册项；None 时用当前默认模型
    """
    root = Path(repo_root)
    project = svc.load_project(root, task_id, project_id)

    raw_srt = svc.stage_raw_srt_path(root, task_id, project_id)
    if not raw_srt.is_file() or raw_srt.stat().st_size < 4:
        raise svc.ShortVideoError("Stage 3 需要先跑 Stage 2 提取字幕")

    srt_text = raw_srt.read_text(encoding="utf-8", errors="replace")
    srt_lines = _parse_srt_lines(srt_text)
    if not srt_lines:
        raise svc.ShortVideoError("raw.srt 解析后无有效字幕行")

    # 选择模板
    if template == "custom":
        sys_p = custom_prompt.strip() or _PROMPT_TEMPLATES[svc.HIGHLIGHT_TEMPLATE_HOOK_FIRST]
    elif template in _PROMPT_TEMPLATES:
        sys_p = _PROMPT_TEMPLATES[template]
    else:
        raise svc.ShortVideoError(f"未知模板: {template}")
    n_clips = max(3, min(5, int(n_clips or 4)))

    # 写状态
    state = svc.get_stage_state(project, "stage3_analyze")
    state["status"] = "running"
    state["started_at"] = svc.now_iso()
    state["template"] = template
    state["n_clips"] = n_clips
    state["logs"] = list(state.get("logs") or []) + [
        f"[{svc.now_iso()}] stage3 start, template={template}, n_clips={n_clips}, srt_lines={len(srt_lines)}",
    ]
    svc.set_stage_state(project, "stage3_analyze", state)
    svc.save_project(root, project)

    # 把 SRT 拆成 (src_index, start_ms, end_ms, text) 列表给 LLM
    srt_for_llm = "\n".join(
        f"[{ln.index}] {_fmt_ms(ln.start_ms)}-{_fmt_ms(ln.end_ms)} {ln.text}"
        for ln in srt_lines
    )
    user_payload = (
        f"以下是源视频的 SRT（每行 ``[序号] HH:MM:SS,mmm-HH:MM:SS,mmm 文本``）：\n\n"
        f"{srt_for_llm}\n\n"
        f"请生成 {n_clips} 条 highlights。**输出仅 JSON**（不要解释、不要 Markdown 代码块标记外的内容）。"
    )

    t0 = time.time()
    raw = ""
    try:
        raw = _call_highlights_llm(sys_p, user_payload, entry=entry)
    except Exception as e:  # noqa: BLE001
        log.exception("LLM 调用失败")
        state = svc.get_stage_state(project, "stage3_analyze")
        state["status"] = "failed"
        state["finished_at"] = svc.now_iso()
        state["error"] = f"LLM 调用失败: {e}"
        state["logs"] = list(state.get("logs") or []) + [
            f"[{svc.now_iso()}] stage3 failed: LLM 调用失败: {e}",
        ]
        svc.set_stage_state(project, "stage3_analyze", state)
        svc.save_project(root, project)
        return state
    elapsed = int(time.time() - t0)

    # 落 llm_raw.txt
    llm_raw_path = svc.stage_llm_raw_path(root, task_id, project_id)
    llm_raw_path.parent.mkdir(parents=True, exist_ok=True)
    llm_raw_path.write_text(raw, encoding="utf-8")

    # 解析 + 校验
    try:
        payload = _strip_json_block(raw)
        validated, warnings = _validate_highlights(payload, srt_lines)
    except Exception as e:  # noqa: BLE001
        state = svc.get_stage_state(project, "stage3_analyze")
        state["status"] = "failed"
        state["finished_at"] = svc.now_iso()
        state["error"] = f"LLM 返回值无法解析: {e}"
        state["elapsed_sec"] = elapsed
        state["logs"] = list(state.get("logs") or []) + [
            f"[{svc.now_iso()}] stage3 failed: LLM 返回值无法解析 ({elapsed}s)",
        ]
        svc.set_stage_state(project, "stage3_analyze", state)
        svc.save_project(root, project)
        return state

    # 写 highlights.json
    highlights_path = svc.stage_highlights_path(root, task_id, project_id)
    model_id = (entry or {}).get("id") or "default"
    out_doc = {
        "project_id": project_id,
        "source_srt": svc._rel(root, raw_srt),
        "model": model_id,
        "template": template,
        "n_clips": len(validated),
        "highlights": validated,
        "warnings": warnings,
        "created_at": svc.now_iso(),
    }
    highlights_path.write_text(
        json.dumps(out_doc, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    state = svc.get_stage_state(project, "stage3_analyze")
    state["status"] = "done"
    state["finished_at"] = svc.now_iso()
    state["elapsed_sec"] = elapsed
    state["highlights_path"] = svc._rel(root, highlights_path)
    state["warnings"] = warnings
    state["count"] = len(validated)
    # REQ-20261004-bugfix：清掉上一次失败的 error，避免 UI 显示陈旧报错。
    state.pop("error", None)
    state["logs"] = list(state.get("logs") or []) + [
        f"[{svc.now_iso()}] stage3 done, highlights={len(validated)}, warnings={len(warnings)}, llm={model_id}, elapsed={elapsed}s",
    ]
    svc.set_stage_state(project, "stage3_analyze", state)
    svc.save_project(root, project)

    # Stage 3 改 → 下游 stage 4-6 全部作废
    svc.reset_downstream_stages(project, "stage3_analyze")
    svc.save_project(root, project)

    return state


def _fmt_ms(ms: int) -> str:
    h, rem = divmod(ms, 3600 * 1000)
    m, rem = divmod(rem, 60 * 1000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


# ---------- CLI ----------

def _cli() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Stage 3: LLM analyze highlights")
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--template", default=svc.HIGHLIGHT_TEMPLATE_HOOK_FIRST,
                    choices=list(svc.HIGHLIGHT_TEMPLATES) + ["custom"])
    ap.add_argument("--n-clips", type=int, default=4)
    ap.add_argument("--custom-prompt", default="")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    state = run_stage3(
        args.repo_root, args.task_id, args.project_id,
        template=args.template, n_clips=args.n_clips,
        custom_prompt=args.custom_prompt,
    )
    print(json.dumps({k: v for k, v in state.items() if k != "logs"}, ensure_ascii=False, indent=2))
    return 0 if state.get("status") == "done" else 1


if __name__ == "__main__":
    raise SystemExit(_cli())