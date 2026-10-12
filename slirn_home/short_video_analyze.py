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
    "你是一个短视频内容导演，输入是一段长视频的 SRT 字幕。\n"
    "你的任务是从字幕里**重新组织和编排** {n_clips} 条**独立短视频**（highlights），\n"
    "每条约 60 秒（±20 秒）。\n\n"
    "⚠️ **重要前提**：highlights **不是**源视频的连续切片，而是**独立的短视频**。\n"
    "每条都要有完整的开场钩子-主体内容-收尾点题；片段之间**不连续**、不重叠、不按源时间顺序排列。\n"
    "每条 highlight 的 ``start_ms`` / ``end_ms`` 是一个**大致时间窗口**（用于粗剪定位），\n"
    "但 ``subtitle_lines`` 可以**跨源视频任意位置**挑选，按叙事/传播逻辑自由排序。\n\n"
    "关键要求（务必遵守）：\n"
    "1. **非连续区间**：相邻 highlights 的 [start_ms, end_ms] 不应连续或重叠；\n"
    "   应刻意拉开（如 [30-60s]、[180-240s]、[400-460s]、[800-860s]）。\n"
    "2. **高密度字幕**：每个 highlight 至少包含 **8 条 subtitle_lines**（60s 视频密度建议 12 条），\n"
    "   不要只挑 3-5 句。密度 = 短视频可看性。\n"
    "3. **重组字幕**：subtitle_lines **可跨源字幕任意位置**挑行，**不要求**按 src_index 升序。\n"
    "   行内顺序由叙事/逻辑决定（开头钩子→主体展开→点题收束）。\n"
    "4. **时间戳真实**：``start_ms`` / ``end_ms`` 必须是源字幕中真实存在的毫秒；\n"
    "   ``src_index`` 引用源字幕行号（1-based），只能引用实际存在的行。\n"
    "5. ``text`` 字段可微调，但应保留原意；不要凭空改写或编造原字幕没有的内容。\n\n"
    "输出严格 JSON：\n"
    "{{\n"
    "  \"highlights\": [\n"
    "    {{\n"
    "      \"id\": \"h1\",\n"
    "      \"title\": \"短小有钩子的标题（≤ 15 字）\",\n"
    "      \"start_ms\": 30000,\n"
    "      \"end_ms\": 90000,\n"
    "      \"subtitle_lines\": [\n"
    "        {{\"src_index\": 5,  \"text\": \"开头钩子句（≤ 15 字）\"}},\n"
    "        {{\"src_index\": 12, \"text\": \"主体展开句 1\"}},\n"
    "        {{\"src_index\": 7,  \"text\": \"主体展开句 2\"}},\n"
    "        {{\"src_index\": 23, \"text\": \"收束点题句\"}},\n"
    "        {{\"src_index\": 31, \"text\": \"跨片段引用的句子\"}},\n"
    "        ...至少 8 条\n"
    "      ]\n"
    "    }}, ...\n"
    "  ]\n"
    "}}\n\n"
    "**输出仅 JSON**，不要解释、不要 Markdown 代码块标记外的内容。"
)


_TEMPLATE_HOOK_FIRST = (
    _TEMPLATE_BASE_SYSTEM
    + "\n\n# 排序策略：Hook-First（钩子优先）\n"
    "**第 1 条 highlight** 必须是视频里**最抓人**的一段：\n"
    "  - ``start_ms`` 必须在 [0, 30000] 区间（视频前 30s 内）。\n"
    "  - ``subtitle_lines[0]`` 必须是**单句钩子**：≤ 15 字、有悬念/反差/痛点/惊人事实。\n"
    "    例：「90% 的人不知道」「这个错误坑了 1000 万人」「看完你会沉默」\n"
    "  - 第 1 条内部：从钩子→论证→点题（密度建议 12 行）。\n"
    "**第 2-N 条**：按**情绪强度递进**或**逻辑递进**排序（不必按源时间顺序）。\n"
    "适合：推广 / 拉新 / 悬念向 / 卖货向 短视频。\n"
    "**反例（不要这样）**：连续切 [30-60s, 60-90s, 90-120s, 120-150s] —— 这只是平铺直叙。"
)


_TEMPLATE_TOPIC_CLUSTER = (
    _TEMPLATE_BASE_SYSTEM
    + "\n\n# 排序策略：Topic-Cluster（主题聚类）\n"
    "把字幕里的主题聚成 4-5 个**独立的知识小卡**：\n"
    "  - 每条 highlight 的 ``title`` 必须是显式主题词（\"减肥误区\"/\"AI 工具三件套\"/\"新手避坑指南\"），\n"
    "    让用户一眼看懂这卡片讲什么。\n"
    "  - 每条内部按**概念-例证-总结**微结构排：\n"
    "    开场点明主题 → 中间从源字幕不同位置挑选 3-5 个例证 → 收尾总结。\n"
    "  - 高密度：12+ 行/卡。\n"
    "适合：教程 / 知识科普 / 干货分享。\n"
    "**反例（不要这样）**：连续切视频 + 没有明确主题。"
)


_TEMPLATE_STORY_ARC = (
    _TEMPLATE_BASE_SYSTEM
    + "\n\n# 排序策略：Story-Arc（起承转合）\n"
    "每条 highlight 都是一个**完整的小故事弧**，至少 12 行字幕，强制 4 段结构：\n"
    "  - **起（2-3 行铺垫）**：引入人物/情境/冲突，\"从前有个...\"/\"某天，他发现...\"\n"
    "  - **承（3-4 行展开）**：故事发展、转折前奏、细节堆叠。\n"
    "  - **转（2-3 行反转/转折）**：核心反转、关键事件、矛盾爆发。\n"
    "  - **合（2-3 行收束/点题）**：情感落点、金句、行动号召。\n"
    "从源视频挑 4 个具备完整故事弧的段；每条内部的字幕行严格按 起→承→转→合 顺序。\n"
    "适合：故事 / 人物访谈 / 案例分享。\n"
    "**反例（不要这样）**：只是把视频切 4 段，每段都缺起承转合结构。"
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


def _validate_highlights(
    payload: dict,
    srt_lines: list[SrtLine],
    *,
    title_fallback: str = "",
) -> tuple[list[dict], list[str]]:
    """校验 LLM 返回的高亮：要求每条 start_ms/end_ms 对得上 srt_lines，
    subtitle_lines 的 src_index 引用真实存在 **且时间区间在 [start_ms, end_ms] 内**。

    title_fallback（REQ-20261012-099）：非空时，LLM 未给 title 的条目用它
    （单片段重解析时 = 旧 title，保住用户改过的标题）而不是「片段 {i}」。

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

        # REQ-20261004-stage3-restructure：密度补齐 —— 短视频 60s 至少 8 行；
        # 不足时从 raw.srt 区间内按时间顺序补行（保留 LLM 已挑的在前，补齐的在后）。
        # 仅当 LLM 给了部分行（但密度不够）时跑；完全空的 subtitle_lines 走下面的兜底。
        if srt_lines and subtitle_lines:
            duration_sec = max(0, (end_ms - start_ms) / 1000.0)
            target_lines = max(8, int((duration_sec + 4) // 5))  # ceil(duration/5) 且 ≥ 8
            if len(subtitle_lines) < target_lines:
                existing_src = {int(sl.get("src_index") or 0) for sl in subtitle_lines}
                candidates = [
                    ln for ln in srt_lines
                    if ln.start_ms >= start_ms and ln.end_ms <= end_ms
                    and ln.index not in existing_src
                ]
                for ln in candidates:
                    if len(subtitle_lines) >= target_lines:
                        break
                    subtitle_lines.append({"src_index": ln.index, "text": ln.text})
                    existing_src.add(ln.index)
                if len(subtitle_lines) >= target_lines:
                    warnings.append(
                        f"highlight #{i} 字幕密度不足（{len(subtitle_lines)} < 目标 {target_lines}），"
                        f"已用区间内 SRT 行补齐到 {len(subtitle_lines)} 行"
                    )
                else:
                    warnings.append(
                        f"highlight #{i} 字幕密度不足（{len(subtitle_lines)} 行；目标 {target_lines}），"
                        f"区间内 SRT 行不够，已用尽"
                    )

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
            "title": str(
                hl.get("title")
                or (title_fallback if title_fallback.strip() else f"片段 {i}")
            )[:80],
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


# REQ-20261012-099：输出格式约束固定拼在 user payload 尾部。
# Why：用户自定义提示词（作为 system）不会自带「输出 JSON」要求；把 schema
# 放 user 侧，无论 system 换成什么，输出都可被 _strip_json_block 解析。
# 注意：本常量不经过 .format()，直接单花括号（勿从 _TEMPLATE_BASE_SYSTEM
# 复制双花括号版本）。
_OUTPUT_FORMAT_CONSTRAINT = (
    "输出严格 JSON（不要解释、不要 Markdown 代码块标记外的内容）：\n"
    "{\n"
    "  \"highlights\": [\n"
    "    {\n"
    "      \"id\": \"h1\",\n"
    "      \"title\": \"短小有钩子的标题（≤ 15 字）\",\n"
    "      \"start_ms\": 30000,\n"
    "      \"end_ms\": 90000,\n"
    "      \"subtitle_lines\": [\n"
    "        {\"src_index\": 5, \"text\": \"开头句\"},\n"
    "        {\"src_index\": 12, \"text\": \"主体句 1\"},\n"
    "        ...至少 8 条\n"
    "      ]\n"
    "    }, ...\n"
    "  ]\n"
    "}\n"
    "start_ms / end_ms 必须是源字幕中真实存在的毫秒；src_index 引用源字幕行号"
    "（1-based，按上方 [序号]），只能引用实际存在的行。"
)


# ---------- REQ-20261012-099 修订五：上游「大模型智能剪辑」同款后台流程 ----------
#
# 用户验收：Stage 3「解析」按钮改为调用上游 FunClip 首页「🧠 LLM智能裁剪」
# 对应的后台功能（funclip/launch.py llm_inference + utils/trans_utils.
# extract_timestamps）：system = 上游 prompt_head（按片段数模板化），user =
# 「这是待裁剪的视频srt字幕：」+ raw.srt，LLM 输出编号字幕列表
# `1. [开始时间-结束时间] 文本`，再按时间戳从源视频截取拼接。
#
# 偏离点：不拼上游 llm_inference 里的「视频实际时长 / 不低于 50% 时长」
# 注记 —— 那是上游「全片摘要」取向，与拆条（n 条以内）目标冲突。
_UPSTREAM_CLIP_SYSTEM_PROMPT = (
    "你是一个视频srt字幕分析剪辑器，输入视频的srt字幕，"
    "分析其中的精彩且尽可能连续的片段并裁剪出来，输出{n}条以内的片段，"
    "将片段中在时间上连续的多个句子及它们的时间戳合并为一条，"
    "注意确保文字与时间戳的正确匹配。"
    "输出需严格按照如下格式：1. [开始时间-结束时间] 文本，"
    '注意其中的连接符是"-"。开始/结束时间使用 HH:MM:SS,mmm 格式'
    "（与 srt 字幕时间戳一致）。"
)

# 上游 user 侧固定前缀（funclip/launch.py prompt_head2 原文）
_UPSTREAM_CLIP_USER_PREFIX = "这是待裁剪的视频srt字幕："

# 输出格式约束固定拼在 user payload 尾部（与旧 JSON schema 同理：system 被
# 用户自定义提示词替换时，输出仍可被 _parse_llm_clip_segments 解析）。
_UPSTREAM_CLIP_OUTPUT_CONSTRAINT = (
    "\n\n【输出格式（必须遵守）】忽略上文的其他输出格式要求（包括 JSON）：\n"
    "只输出片段列表，每条一行，格式为：序号. [开始时间-结束时间] 该片段的完整字幕文本。\n"
    "序号从 1 连续编号；时间戳取自上方 srt 中真实存在的时间（HH:MM:SS,mmm），"
    '连接符为"-"；最多输出 {n} 条，除此之外不要输出任何内容。'
)

# `1. [00:00:01,000-00:00:05,500] 文本`（上游 extract_timestamps 的逐行版，
# 序号宽松匹配：LLM 常写全角句点 / 列表符号；时间容忍 MM:SS,mmm 短格式）
_CLIP_LINE_RE = re.compile(
    r"^\s*(?:\d+[\.\、)]|[-*•])?\s*\["
    r"(\d{1,2}:\d{2}(?::\d{2})?[,.]\d{1,3})\s*-\s*"
    r"(\d{1,2}:\d{2}(?::\d{2})?[,.]\d{1,3})\s*\]\s*(.*)$"
)


def _parse_llm_time(token: str) -> int:
    """`HH:MM:SS,mmm` / `MM:SS,mmm` → 毫秒（上游 convert_time_to_millis 同款）。"""
    parts = re.split(r"[,:]", token.strip())
    try:
        nums = [int(x) for x in parts]
    except ValueError:
        return -1
    if len(nums) == 4:
        h, m, s, ms = nums
    elif len(nums) == 3:
        h, (m, s, ms) = 0, nums
    else:
        return -1
    return (h * 3600 + m * 60 + s) * 1000 + ms


def _parse_llm_clip_segments(
    raw: str,
    srt_lines: list[SrtLine],
    *,
    n_clips: int = 4,
    title_fallback: str = "",
) -> tuple[list[dict], list[str]]:
    """解析上游「大模型智能剪辑」编号字幕列表 → highlights 同构列表。

    - **自动编号**：按出现顺序重编 1..N（不信任 LLM 写的序号），编号即原始
      顺序号，后续拖拽重排时保持不变
    - 时间重叠映射：每个片段 [start, end] 与 raw.srt 行取交集 →
      subtitle_lines（src_index 引用真实行），下游 Stage 3.5/4/5/6 零改动
    - 超出 n_clips 截断并告警；时间戳非法 / 区间内无字幕行的条目跳过并告警

    Raises: ShortVideoError —— 整个返回里没有可识别的片段行。
    """
    warnings: list[str] = []
    max_ms = max((ln.end_ms for ln in srt_lines), default=0)
    srt_sorted = sorted(srt_lines, key=lambda ln: ln.index)
    cap = max(1, int(n_clips or 4))

    segments: list[dict] = []
    matched = 0
    for lineno, line in enumerate(raw.splitlines(), start=1):
        m = _CLIP_LINE_RE.match(line)
        if not m:
            continue
        matched += 1
        start_ms = _parse_llm_time(m.group(1))
        end_ms = _parse_llm_time(m.group(2))
        if start_ms < 0 or end_ms < 0:
            warnings.append(f"第 {lineno} 行时间戳无法解析，已跳过")
            continue
        start_ms = max(0, min(start_ms, max_ms))
        end_ms = max(0, min(end_ms, max_ms))
        if end_ms <= start_ms:
            warnings.append(f"第 {lineno} 行结束时间不晚于开始时间，已跳过")
            continue
        text = m.group(3).strip()
        sub_lines = [
            {"src_index": ln.index, "text": ln.text}
            for ln in srt_sorted
            if ln.start_ms < end_ms and ln.end_ms > start_ms
        ]
        if not sub_lines:
            warnings.append(f"第 {lineno} 行时间段内没有源字幕行（时间戳可能错误）")
        title = (text or title_fallback).strip()[:80] or f"片段 {len(segments) + 1}"
        segments.append({
            "id": f"h{len(segments) + 1}",
            "index": len(segments) + 1,
            "title": title,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "subtitle_lines": sub_lines,
        })
        if len(segments) >= cap:
            break

    if not segments:
        preview = raw.strip().replace("\n", " ")[:120]
        raise svc.ShortVideoError(
            "LLM 返回值里没有可识别的片段行（期望 `1. [开始-结束] 文本`）。返回开头："
            + (preview or "<空>")
        )
    if matched > len(segments):
        warnings.append(f"LLM 输出了 {matched} 条片段，超出 {cap} 条上限，已截取前 {len(segments)} 条")
    return segments, warnings


def _resolve_stage3_system_prompt(
    repo_root: Path,
    task_id: str,
    project_id: str,
    *,
    template: str,
    custom_prompt: str = "",
    prompt: str = "",
    n_clips: int = 4,
) -> tuple[str, str]:
    """决定 Stage 3 的 system 提示词。

    REQ-20261012-099 修订五（Batch F）：默认 system 换成上游「大模型智能
    剪辑」提示词（_UPSTREAM_CLIP_SYSTEM_PROMPT，n 按片段数模板化）；旧的
    hook_first / topic_cluster / story_arc 模板文本退出默认链，仅保留其
    用户自定义覆盖入口（custom_prompts.json）。优先级（高 → 低）：

    1. ``prompt``（「解析提示词」文本域；与生效默认相同则归模板路径，
       badge/doc 溯源不失真）
    2. template 的用户自定义覆盖（custom_prompts.json）
    3. 上游「大模型智能剪辑」默认提示词

    返回 (system_prompt, source)——source 取 "user" / "custom_override" /
    "template" / "custom"，写入 state 溯源。
    """
    default_system = _UPSTREAM_CLIP_SYSTEM_PROMPT.format(n=max(1, int(n_clips or 4)))
    custom_overrides = svc.load_custom_prompts(repo_root, task_id, project_id)
    override = ""
    if template in _PROMPT_TEMPLATES:
        override = str((custom_overrides.get(template) or {}).get("prompt") or "").strip()

    user_prompt = str(prompt or "").strip()
    if user_prompt and user_prompt != default_system:
        return user_prompt, "user"
    if template == "custom":
        return (custom_prompt.strip() or default_system), "custom"
    if override:
        return override, "custom_override"
    return default_system, "template"


def _resolve_stage3_image(
    repo_root: Path,
    task_id: str,
    project_id: str,
    *,
    use_image: bool,
    entry: dict | None,
) -> Path | None:
    """REQ-20261012-099：解析要发的截图。

    use_image=False → None；state 有图且文件存在 → 路径；
    否则报错（用户勾了发图却没图，是 UI/状态不一致，明确指出）。
    有图但当前模型不支持视觉 → 显式报错，不静默降级（REQ C-3）。
    """
    if not use_image:
        return None
    cur = svc.stage3_current_image(repo_root, task_id, project_id)
    if not cur:
        raise svc.ShortVideoError("未找到截图：请先在 Stage 3 上传截图（或取消勾选「发送截图」）")
    from slirn_home import revision_service
    if entry is not None and not revision_service._is_vision_entry(entry):
        raise svc.ShortVideoError(
            f"当前模型 [{entry.get('id', '?')}] 不支持图片输入，无法把截图发给 AI。"
            "请到顶栏「大模型设置」切换多模态模型（或在模型条目里声明 vision=true），"
            "或取消勾选「发送截图」。"
        )
    return svc.stage3_dir(repo_root, task_id, project_id) / cur["name"]


def run_stage3(
    repo_root: Path | str,
    task_id: str,
    project_id: str,
    *,
    template: str = svc.HIGHLIGHT_TEMPLATE_HOOK_FIRST,
    n_clips: int = 4,
    custom_prompt: str = "",
    prompt: str = "",
    use_image: bool = False,
    entry: dict | None = None,
) -> dict:
    """Stage 3「解析短视频内容」：LLM 读 raw.srt（+ 截图）→
    ``stage3/highlights.json`` + ``stage3/llm_raw.txt``。

    Args:
        template: hook_first / topic_cluster / story_arc / custom（快速模板）
        n_clips: 期望生成的片段数（3-5），写到 prompt 里
        custom_prompt: 当 template="custom" 时使用；否则忽略（向后兼容）
        prompt: REQ-20261012-099 用户录入的解析提示词；非空时优先作为 system
        use_image: 把 Stage 3 上传的截图一并发给多模态模型
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

    n_clips = max(3, min(5, int(n_clips or 4)))
    sys_p, prompt_source = _resolve_stage3_system_prompt(
        root, task_id, project_id,
        template=template, custom_prompt=custom_prompt, prompt=prompt,
        n_clips=n_clips,
    )
    image_path = _resolve_stage3_image(
        root, task_id, project_id, use_image=use_image, entry=entry,
    )

    # 写状态
    state = svc.get_stage_state(project, "stage3_analyze")
    state["status"] = "running"
    state["started_at"] = svc.now_iso()
    state["template"] = template
    state["n_clips"] = n_clips
    state["prompt_source"] = prompt_source
    state["custom_prompt_used"] = prompt_source in ("custom", "custom_override")
    if image_path is not None:
        state["image_used"] = svc._rel(root, image_path)
    else:
        state.pop("image_used", None)
    state["logs"] = list(state.get("logs") or []) + [
        f"[{svc.now_iso()}] stage3 start, template={template}, n_clips={n_clips}, "
        f"srt_lines={len(srt_lines)}, prompt_source={prompt_source}"
        + (f", image={image_path.name}" if image_path is not None else ""),
    ]
    svc.set_stage_state(project, "stage3_analyze", state)
    svc.save_project(root, project)

    # 修订五：上游「大模型智能剪辑」——user 侧 = 上游固定前缀 + 原始 SRT
    # 整文（上游 llm_inference 发的就是整段 srt 文本，不是逐行重组格式）
    user_payload = (
        _UPSTREAM_CLIP_USER_PREFIX + "\n\n" + srt_text.strip()
        + f"\n\n请从上述字幕中裁剪出不超过 {n_clips} 条片段。"
        + _UPSTREAM_CLIP_OUTPUT_CONSTRAINT.format(n=n_clips)
    )
    if image_path is not None:
        user_payload = (
            "随字幕附带一张源视频截图，请结合画面信息一起挑选片段。\n\n"
            + user_payload
        )

    t0 = time.time()
    raw = ""
    try:
        if image_path is not None:
            from slirn_home import revision_service
            raw = revision_service._call_llm_vision(
                sys_p, user_payload, [image_path], entry=entry,
            )
        else:
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

    # 解析 + 校验：优先上游编号字幕列表格式；模型仍按旧 JSON 输出时兜底旧解析
    try:
        try:
            validated, warnings = _parse_llm_clip_segments(
                raw, srt_lines, n_clips=n_clips,
            )
        except svc.ShortVideoError:
            if "highlights" not in raw:
                raise
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

    # 写 highlights.json（修订五：+ flow 溯源 + splice_order 拼接顺序基线）
    highlights_path = svc.stage_highlights_path(root, task_id, project_id)
    model_id = (entry or {}).get("id") or "default"
    splice_order = [int(h.get("index") or i + 1) for i, h in enumerate(validated)]
    out_doc = {
        "project_id": project_id,
        "source_srt": svc._rel(root, raw_srt),
        "model": model_id,
        "template": template,
        "flow": "llm_clip",
        "prompt": sys_p if prompt_source == "user" else "",
        "image": image_path.name if image_path is not None else "",
        "n_clips": len(validated),
        "splice_order": splice_order,
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
    # 修订五：编号字幕列表 + 拼接顺序基线（自然序）镜像进 state 供 UI 渲染
    state["flow"] = "llm_clip"
    state["splice_order"] = splice_order
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


# ---------- REQ-20261012-099：单片段重新解析 ----------

def _rerun_one_suffix(title: str) -> str:
    """定向指令。title 用户可编辑（可能含 { }），不用 .format 防 KeyError。

    修订五：与整跑同一套上游「大模型智能剪辑」输出格式（编号字幕行）。
    """
    return (
        "\n\n# 本次任务：单片段重新解析\n"
        f"只针对主题「{title}」重新裁剪 **1 条**片段（列表里只有 1 行）：\n"
        "- 重新划定该主题的时间段（时间戳取自源字幕真实时间）\n"
        "- 输出该片段的完整字幕文本\n"
        f"- 无更优表达时文本围绕「{title}」"
    )


def rerun_stage3_one(
    repo_root: Path | str,
    task_id: str,
    project_id: str,
    hl_index: int,
    *,
    prompt: str = "",
    use_image: bool = False,
    entry: dict | None = None,
) -> dict:
    """针对单个片段（按其标题/主题）重新解析，原位替换。

    - system = 用户提示词（空则回退 highlights.json 里上次整跑的 prompt，
      再空回退当前模板生效文本——与整跑同一优先级链）
    - 定向指令拼在 system 尾部（只出 1 条、围绕该主题）
    - 校验取第 1 条 validated；title 为空时保留旧 title
    - 替换 highlights.json 中 ``index == hl_index`` 的条目 + 同步 state 镜像
      + ``reset_downstream_stages("stage3_analyze")``
    - LLM 原文落 ``stage3/llm_raw_hl<N>.txt``（不覆盖整跑的 llm_raw.txt）
    """
    root = Path(repo_root)
    hp = svc.stage_highlights_path(root, task_id, project_id)
    if not hp.is_file():
        raise svc.ShortVideoError("highlights.json 不存在，请先运行整体解析")
    try:
        doc = json.loads(hp.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise svc.ShortVideoError(f"highlights.json 解析失败: {e}") from e
    hls = doc.get("highlights") or []
    target = next((h for h in hls if int(h.get("index") or 0) == int(hl_index)), None)
    if target is None:
        raise svc.ShortVideoError(f"片段 #{hl_index} 不存在")

    raw_srt = svc.stage_raw_srt_path(root, task_id, project_id)
    if not raw_srt.is_file() or raw_srt.stat().st_size < 4:
        raise svc.ShortVideoError("重新解析需要 Stage 2 的 raw.srt")
    srt_text = raw_srt.read_text(encoding="utf-8", errors="replace")
    srt_lines = _parse_srt_lines(srt_text)
    if not srt_lines:
        raise svc.ShortVideoError("raw.srt 解析后无有效字幕行")

    title = str(target.get("title") or f"片段 {hl_index}").strip()
    # system：用户提示词 → 上次整跑 prompt → 模板生效文本
    template = str(doc.get("template") or svc.HIGHLIGHT_TEMPLATE_HOOK_FIRST)
    if template not in _PROMPT_TEMPLATES and template != "custom":
        template = svc.HIGHLIGHT_TEMPLATE_HOOK_FIRST
    sys_p, prompt_source = _resolve_stage3_system_prompt(
        root, task_id, project_id,
        template=template, custom_prompt="", prompt=prompt,
    )
    if not str(prompt or "").strip():
        last_prompt = str(doc.get("prompt") or "").strip()
        if last_prompt:
            sys_p = last_prompt
            prompt_source = "last_run"
    sys_p = sys_p + _rerun_one_suffix(title)

    image_path = _resolve_stage3_image(
        root, task_id, project_id, use_image=use_image, entry=entry,
    )

    # state：running
    project = svc.load_project(root, task_id, project_id)
    state = svc.get_stage_state(project, "stage3_analyze")
    state["status"] = "running"
    state["started_at"] = svc.now_iso()
    state["logs"] = list(state.get("logs") or []) + [
        f"[{svc.now_iso()}] stage3 rerun-one #{hl_index} start, prompt_source={prompt_source}"
        + (f", image={image_path.name}" if image_path is not None else ""),
    ]
    svc.set_stage_state(project, "stage3_analyze", state)
    svc.save_project(root, project)

    # 修订五：与整跑同一套上游「大模型智能剪辑」user payload（只裁 1 条）
    user_payload = (
        _UPSTREAM_CLIP_USER_PREFIX + "\n\n" + srt_text.strip()
        + "\n\n请从上述字幕中只裁剪出 1 条片段（围绕上述主题）。"
        + _UPSTREAM_CLIP_OUTPUT_CONSTRAINT.format(n=1)
    )
    if image_path is not None:
        user_payload = (
            "随字幕附带一张源视频截图，请结合画面信息一起挑选片段。\n\n" + user_payload
        )

    t0 = time.time()
    try:
        if image_path is not None:
            from slirn_home import revision_service
            raw = revision_service._call_llm_vision(
                sys_p, user_payload, [image_path], entry=entry,
            )
        else:
            raw = _call_highlights_llm(sys_p, user_payload, entry=entry)
    except Exception as e:  # noqa: BLE001
        log.exception("LLM 调用失败（rerun-one）")
        project = svc.load_project(root, task_id, project_id)
        state = svc.get_stage_state(project, "stage3_analyze")
        state["status"] = "failed"
        state["finished_at"] = svc.now_iso()
        state["error"] = f"片段 #{hl_index} 重新解析 LLM 调用失败: {e}"
        state["logs"] = list(state.get("logs") or []) + [
            f"[{svc.now_iso()}] stage3 rerun-one #{hl_index} failed: {e}",
        ]
        svc.set_stage_state(project, "stage3_analyze", state)
        svc.save_project(root, project)
        return state
    elapsed = int(time.time() - t0)

    # 原文落档（单片段独立文件）
    raw_path = svc.stage3_dir(root, task_id, project_id) / f"llm_raw_hl{int(hl_index)}.txt"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(raw, encoding="utf-8")

    try:
        try:
            # 修订五：优先上游编号字幕行格式（单片段 = 取第 1 行）
            validated, warnings = _parse_llm_clip_segments(
                raw, srt_lines, n_clips=1, title_fallback=title,
            )
        except svc.ShortVideoError:
            if "highlights" not in raw:
                raise
            payload = _strip_json_block(raw)
            validated, warnings = _validate_highlights(
                payload, srt_lines, title_fallback=title,
            )
    except Exception as e:  # noqa: BLE001
        project = svc.load_project(root, task_id, project_id)
        state = svc.get_stage_state(project, "stage3_analyze")
        state["status"] = "failed"
        state["finished_at"] = svc.now_iso()
        state["error"] = f"片段 #{hl_index} 重新解析返回值无法解析: {e}"
        state["elapsed_sec"] = elapsed
        state["logs"] = list(state.get("logs") or []) + [
            f"[{svc.now_iso()}] stage3 rerun-one #{hl_index} failed: 解析失败 ({elapsed}s)",
        ]
        svc.set_stage_state(project, "stage3_analyze", state)
        svc.save_project(root, project)
        return state

    # index 保持不变；title 由 title_fallback 兜底（LLM 未给 → 保留旧 title）
    new_hl = validated[0]
    new_hl["index"] = int(hl_index)
    new_hl["title"] = str(new_hl.get("title") or title)[:80]

    # 原位替换 highlights.json
    replaced = False
    for i, h in enumerate(hls):
        if int(h.get("index") or 0) == int(hl_index):
            hls[i] = new_hl
            replaced = True
            break
    if not replaced:
        hls.append(new_hl)
    doc["highlights"] = hls
    # rerun 记录条数设上限：反复调参重跑时 warnings 不无限膨胀
    doc["warnings"] = (list(doc.get("warnings") or []) + [
        f"[{svc.now_iso()}] 片段 #{hl_index} 已单独重新解析（prompt_source={prompt_source}）",
    ])[-20:]
    if prompt_source == "user":
        doc["prompt"] = str(prompt or "").strip()
    # 镜像同步（多入口写同一份状态必须对称）：rerun 是否带了截图
    doc["image"] = image_path.name if image_path is not None else ""
    svc._write_json_atomic(hp, doc)

    # 同步 state 镜像 + 下游作废
    project = svc.load_project(root, task_id, project_id)
    state = svc.get_stage_state(project, "stage3_analyze")
    state["status"] = "done"
    state["finished_at"] = svc.now_iso()
    state["elapsed_sec"] = elapsed
    state["count"] = len(hls)
    state["prompt_source"] = prompt_source
    if image_path is not None:
        state["image_used"] = svc._rel(root, image_path)
    else:
        state.pop("image_used", None)
    state.pop("error", None)
    state["warnings"] = (list(state.get("warnings") or []) + warnings)[-20:]
    state["logs"] = list(state.get("logs") or []) + [
        f"[{svc.now_iso()}] stage3 rerun-one #{hl_index} done, "
        f"lines={len(new_hl.get('subtitle_lines') or [])}, elapsed={elapsed}s",
    ]
    state_hls = state.get("highlights") or []
    for i, sh in enumerate(state_hls):
        if int(sh.get("index") or 0) == int(hl_index):
            state_hls[i] = new_hl
            break
    else:
        state_hls.append(new_hl)
    state["highlights"] = state_hls
    svc.set_stage_state(project, "stage3_analyze", state)
    svc.reset_downstream_stages(project, "stage3_analyze")
    svc.save_project(root, project)
    return state


# ---------- REQ-20261012-099 修订五：拖拽调整拼接顺序（编号不变） ----------

def reorder_splice_order(
    repo_root: Path | str,
    task_id: str,
    project_id: str,
    order: list[int],
) -> dict:
    """写入 ``highlights.json`` 顶层 ``splice_order``（拼接顺序）。

    - 片段编号（index）是**原始顺序号**，重排永远不重编——只改拼接顺序
    - ``order`` 必须恰好包含当前全部片段编号各一次（排列校验）
    - 顺序变更后，上次拼接产物作废（stage3_splice → pending，带 stale_reason）
    """
    root = Path(repo_root)
    hp = svc.stage_highlights_path(root, task_id, project_id)
    if not hp.is_file():
        raise svc.ShortVideoError("请先运行 Stage 3 解析生成片段列表")
    try:
        doc = json.loads(hp.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise svc.ShortVideoError(f"highlights.json 解析失败: {e}") from e

    cur: list[int] = []
    for h in doc.get("highlights") or []:
        try:
            i = int(h.get("index") or 0)
        except (TypeError, ValueError):
            continue
        if i > 0:
            cur.append(i)
    cur.sort()
    if sorted(int(x) for x in order) != cur:
        raise svc.ShortVideoError(
            "order 必须恰好包含全部片段编号各一次（当前片段："
            + "、".join(f"#{i}" for i in cur) + "）"
        )

    doc["splice_order"] = [int(x) for x in order]
    svc._write_json_atomic(hp, doc)

    project = svc.load_project(root, task_id, project_id)
    state = svc.get_stage_state(project, "stage3_analyze")
    state["splice_order"] = [int(x) for x in order]
    svc.set_stage_state(project, "stage3_analyze", state)
    # 顺序变了 → 上一次拼接结果不再对应新顺序，标记待重拼
    splice = svc.get_stage_state(project, "stage3_splice")
    if splice.get("status") == "done":
        splice.update({"status": "pending", "stale_reason": "拼接顺序已调整，请重新拼接"})
        svc.set_stage_state(project, "stage3_splice", splice)
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
