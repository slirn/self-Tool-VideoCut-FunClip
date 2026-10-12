"""REQ-20261013-105：短视频精剪 AI 智能布局（v1 完整版）。

参考图（reference）→ 多模态 LLM 解析 4 个区域：
- video：主视频位置/大小
- subtitle：字幕位置/大小
- cover：封面位置/大小
- bg：背景位置/大小

返回 {video: {x,y,w,h}, subtitle: ..., cover: ..., bg: ...}，
前端调 ``apply_ai_layout`` 写入 params.layout。

v1 简化：
- 不引入多模态 LLM SDK；调 ``revision_service._call_llm`` 走 text 通道，
  提示词要求 LLM 按"已知画面 1080x1920"推理（无图情况 fallback 默认布局）
- 防御式解析：剥 ```json 围栏 + 截首个 {..}；非法字段丢弃
- 无 reference 时：fallback 默认布局（1080x1920 全屏 + 字幕底部 1/4）
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from slirn_home import short_video_service as svc

log = logging.getLogger(__name__)


_AI_LAYOUT_SYSTEM_PROMPT = """你是短视频画面布局专家。分析参考图（用户上传），按 1080x1920（9:16 竖屏）给出 4 个区域的位置和大小。

输出 JSON：
{
  "video": {"x": 0, "y": 0, "w": 1080, "h": 1920, "reason": "主视频全屏"},
  "subtitle": {"x": 60, "y": 1620, "w": 960, "h": 240, "reason": "字幕底部 1/4"},
  "cover": {"x": 0, "y": 0, "w": 1080, "h": 1080, "reason": "顶部封面"},
  "bg": {"x": 0, "y": 0, "w": 1080, "h": 1920, "reason": "背景全屏"}
}

规则：
- 所有坐标 + 宽高必须 0..1080 / 0..1920 之内
- video 默认全屏（w=1080, h=1920）；cover 默认顶部 1/2（h=1080）；subtitle 默认底部 1/4（y=1440, h=480）
- bg 跟 video 同步（避免穿帮）
- reason 字段简短（一句话）
- 无图情况按默认布局输出
只输出 JSON，不要任何解释或代码块标记。"""


_AI_LAYOUT_USER_TMPL = """任务：分析参考图，给出 4 个区域布局。
{reference_note}
布局风格偏好：{style_hint}

输出 JSON 4 区域（video / subtitle / cover / bg），坐标在 1080x1920 内。"""


def _strip_json(raw: str) -> str:
    text = str(raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if fence:
        return fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        return text[start:end + 1]
    return text


def _normalize_region(d: dict) -> dict | None:
    if not isinstance(d, dict):
        return None
    try:
        return {
            "x": max(0, min(1080, int(d.get("x") or 0))),
            "y": max(0, min(1920, int(d.get("y") or 0))),
            "w": max(0, min(1080, int(d.get("w") or 0))),
            "h": max(0, min(1920, int(d.get("h") or 0))),
            "reason": str(d.get("reason") or "").strip(),
        }
    except (TypeError, ValueError):
        return None


def _default_layout() -> dict[str, dict]:
    """无 reference 时的 fallback 默认布局。"""
    return {
        "video": {"x": 0, "y": 0, "w": 1080, "h": 1920, "reason": "主视频全屏"},
        "subtitle": {"x": 60, "y": 1620, "w": 960, "h": 240, "reason": "字幕底部"},
        "cover": {"x": 0, "y": 0, "w": 1080, "h": 1080, "reason": "顶部封面"},
        "bg": {"x": 0, "y": 0, "w": 1080, "h": 1920, "reason": "背景全屏"},
    }


def parse_reference_layout(repo_root: Path | str, task_id: str, project_id: str,
                           *, style_hint: str = "现代简洁",
                           reference_path: str = "") -> dict:
    """调 LLM 解析参考图 → 返回 4 区域布局。LLM 不可用时返默认布局。

    short/long 隔离：落 project.json → stage7_finalize.params.layout，
    不写长视频 fine_compose.json。
    """
    from slirn_home import revision_service

    root = Path(repo_root)
    # reference 存在性检查（v1 不真读图，仅记录）
    if reference_path:
        ref_abs = (root / reference_path).resolve() if not Path(reference_path).is_absolute() else Path(reference_path)
        reference_note = f"参考图路径：{ref_abs}（v1 不真读图，按文件存在性推理）"
    else:
        reference_note = "无参考图（按默认布局输出）"
    user_prompt = _AI_LAYOUT_USER_TMPL.format(
        reference_note=reference_note, style_hint=style_hint or "现代简洁",
    )
    raw = ""
    try:
        raw = revision_service._call_llm(  # noqa: SLF001
            _AI_LAYOUT_SYSTEM_PROMPT, user_prompt, timeout=60,
        )
    except Exception as e:  # noqa: BLE001 — LLM 不可用兜底默认
        log.warning("[ai-layout] LLM 调用失败，用默认布局: %s", e)
        return _default_layout()
    cleaned = _strip_json(raw)
    try:
        data = json.loads(cleaned)
    except Exception as e:  # noqa: BLE001
        log.warning("[ai-layout] 解析失败，用默认: %s", e)
        return _default_layout()
    out = _default_layout()
    for k in ("video", "subtitle", "cover", "bg"):
        norm = _normalize_region(data.get(k))
        if norm:
            out[k] = norm
    return out


def apply_ai_layout(repo_root: Path | str, task_id: str, project_id: str,
                    layout: dict) -> dict:
    """把 LLM 解析的 layout 写入 params.layout。"""
    root = Path(repo_root)
    project = svc.load_project(root, task_id, project_id)
    from slirn_home import short_video_fine as _fine
    params = _fine._get_params(project)
    # 校验 4 区域
    for k in ("video", "subtitle", "cover", "bg"):
        norm = _normalize_region((layout or {}).get(k))
        if norm:
            (params.setdefault("layout", {}))[k] = norm
    s = svc.get_stage_state(project, "stage7_finalize")
    s["params"] = params
    svc.set_stage_state(project, "stage7_finalize", s)
    svc.save_project(root, project)
    return {"layout": params["layout"]}
