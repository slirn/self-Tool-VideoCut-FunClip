"""短视频多素材混剪项目服务。

短视频项目与长视频六阶段流水线分离：

- 项目元数据：``tasks/<task_id>/short_video/<project_id>/project.json``
- 上传素材：``tasks/<task_id>/short_video/<project_id>/assets/``
- 渲染产物：``tasks/<task_id>/outputs/short_videos/<project_id>/``

本模块只负责项目、素材、分镜规范化和持久化；实际编码在
``short_video_render.py``。外部 LLM 只用于生成结构化分镜，任何失败都会回退
到本地规则，避免把渲染主链路绑定到外部服务。
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger(__name__)

SHORT_VIDEO_DIR = "short_video"
ASSETS_DIR = "assets"
OUTPUTS_DIRNAME = "outputs"
OUTPUTS_REL = Path("outputs") / "short_videos"
PROJECT_FILENAME = "project.json"
SCHEMA_VERSION = 1

# REQ-20261003-098：单源 AI 拆条工作台
# - kind="mixcut"（默认）= REQ-094 多素材混剪旧路径
# - kind="split" = 6 阶段 AI 拆条新路径
# 旧项目不主动迁移；新项目默认 kind="split"（可由调用方覆盖）。
PROJECT_KIND_MIXCUT = "mixcut"
PROJECT_KIND_SPLIT = "split"
PROJECT_KINDS = {PROJECT_KIND_MIXCUT, PROJECT_KIND_SPLIT}

# Stage 6（精剪混编）目录与产物命名
STAGE6_DIR = "stage6"
STAGE5_DIR = "stage5"
STAGE4_DIR = "stage4"
STAGE3_DIR = "stage3"
STAGE3_VERIFY_DIR = "stage3_verify"
STAGE2_DIR = "stage2"
STAGE1_DIR = "stage1"
FINAL_MP4_PREFIX = "final"
FINAL_SRT_PREFIX = "final_continuous"
RAW_SRT_NAME = "raw.srt"
RAW_JSON_NAME = "raw.json"
HIGHLIGHTS_JSON_NAME = "highlights.json"
HIGHLIGHTS_LLM_RAW_NAME = "llm_raw.txt"
COARSE_MP4_PREFIX = "coarse"
COARSE_SRT_PREFIX = "coarse"
REFINED_SRT_PREFIX = "refined"
STAGE6_JOB_FILENAME = "stage6_jobs.json"

# Stage 3 模板 ID（与模板 prompt 一一对应）
HIGHLIGHT_TEMPLATE_HOOK_FIRST = "hook_first"
HIGHLIGHT_TEMPLATE_TOPIC_CLUSTER = "topic_cluster"
HIGHLIGHT_TEMPLATE_STORY_ARC = "story_arc"
HIGHLIGHT_TEMPLATES = (
    HIGHLIGHT_TEMPLATE_HOOK_FIRST,
    HIGHLIGHT_TEMPLATE_TOPIC_CLUSTER,
    HIGHLIGHT_TEMPLATE_STORY_ARC,
)

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".ts", ".mpeg", ".m4v"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
ALLOWED_TRANSITIONS = {
    "none", "fade", "wipeleft", "wiperight", "slideup", "slidedown", "circleopen",
}


class ShortVideoError(ValueError):
    """短视频项目输入或状态错误。"""


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def new_id(prefix: str) -> str:
    return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"


def default_config() -> dict:
    return {
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "duration_min": 30,
        "duration_max": 60,
        "variants": 3,
        "transition": "fade",
        "transition_duration": 0.5,
        "bgm_volume_db": -16.0,
        "allow_external_llm": True,
    }


def _clamp_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, n))


def _clamp_float(value: Any, default: float, low: float, high: float) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, n))


def sanitize_config(cfg: dict | None) -> dict:
    base = default_config()
    if not isinstance(cfg, dict):
        return base
    for key in ("width", "height", "fps", "duration_min", "duration_max", "variants"):
        if key in cfg:
            base[key] = _clamp_int(cfg.get(key), base[key], 1, 10000)
    base["width"] = 1080
    base["height"] = 1920
    base["fps"] = _clamp_int(base.get("fps"), 30, 15, 60)
    base["duration_min"] = _clamp_int(base.get("duration_min"), 30, 5, 600)
    base["duration_max"] = _clamp_int(base.get("duration_max"), 60, base["duration_min"], 900)
    base["variants"] = _clamp_int(base.get("variants"), 3, 1, 5)
    if cfg.get("transition") in ALLOWED_TRANSITIONS:
        base["transition"] = cfg["transition"]
    base["transition_duration"] = _clamp_float(
        cfg.get("transition_duration"), 0.5, 0.1, 2.0
    )
    base["bgm_volume_db"] = _clamp_float(cfg.get("bgm_volume_db"), -16.0, -40.0, 0.0)
    base["allow_external_llm"] = bool(cfg.get("allow_external_llm", True))
    return base


def _rel(repo_root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def _abs(repo_root: Path, value: str | Path) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (repo_root / p).resolve()


def project_dir(repo_root: Path | str, task_id: str, project_id: str) -> Path:
    return Path(repo_root) / "tasks" / task_id / SHORT_VIDEO_DIR / project_id


def assets_dir(repo_root: Path | str, task_id: str, project_id: str) -> Path:
    return project_dir(repo_root, task_id, project_id) / ASSETS_DIR


def output_dir(repo_root: Path | str, task_id: str, project_id: str) -> Path:
    return Path(repo_root) / "tasks" / task_id / OUTPUTS_REL / project_id


def _write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_project(repo_root: Path | str, task_id: str, project_id: str) -> dict:
    path = project_dir(repo_root, task_id, project_id) / PROJECT_FILENAME
    if not path.exists():
        raise ShortVideoError(f"短视频项目不存在: {project_id}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise ShortVideoError(f"短视频项目损坏: {project_id}") from e
    if not isinstance(data, dict):
        raise ShortVideoError(f"短视频项目格式错误: {project_id}")
    return _normalize_project(data, task_id, project_id)


def save_project(repo_root: Path | str, project: dict) -> dict:
    task_id = str(project.get("task_id") or "").strip()
    project_id = str(project.get("id") or "").strip()
    if not task_id or not project_id:
        raise ShortVideoError("项目缺少 task_id 或 id")
    project["updated_at"] = now_iso()
    _write_json_atomic(project_dir(repo_root, task_id, project_id) / PROJECT_FILENAME, project)
    return project


def _normalize_project(data: dict, task_id: str, project_id: str) -> dict:
    data["_schema"] = SCHEMA_VERSION
    data["id"] = project_id
    data["task_id"] = task_id
    data.setdefault("name", "未命名短视频")
    data.setdefault("brief", "")
    data.setdefault("base_material_id", "")
    data.setdefault("created_at", now_iso())
    data.setdefault("updated_at", data["created_at"])
    # REQ-20261003-098：旧项目（缺 kind）默认 mixcut，新建走 split
    data.setdefault("kind", PROJECT_KIND_MIXCUT)
    if data["kind"] not in PROJECT_KINDS:
        data["kind"] = PROJECT_KIND_MIXCUT
    data["config"] = sanitize_config(data.get("config"))
    data.setdefault("materials", [])
    data.setdefault("storyboard", {"status": "empty", "variants": []})
    data.setdefault("render_jobs", [])
    # REQ-20261003-098：6 阶段流水线状态机（按 kind 决定是否初始化）
    pipeline = data.get("pipeline") or {}
    if not isinstance(pipeline, dict):
        pipeline = {}
    if data["kind"] == PROJECT_KIND_SPLIT:
        for stage_key, defaults in (
            ("stage1_source", {"status": "pending"}),
            ("stage2_extract", {"status": "pending"}),
            ("stage3_analyze", {"status": "pending"}),
            ("stage3_verify", {"status": "pending"}),
            ("stage4_coarse", {"status": "pending"}),
            ("stage5_refine", {"status": "pending"}),
            ("stage6_finalize", {"status": "pending"}),
        ):
            pipeline.setdefault(stage_key, dict(defaults))
        data["pipeline"] = pipeline
    return data


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _safe_asset_name(filename: str) -> str:
    raw = Path(filename or "asset.bin").name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(raw).stem).strip("._") or "asset"
    ext = Path(raw).suffix.lower()
    return stem[:80] + ext


def kind_from_extension(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in AUDIO_EXTS:
        return "audio"
    return "file"


def probe_media(path: Path) -> dict:
    """使用 ffprobe 读取媒体元数据；失败返回最小信息。"""
    result = {"duration": 0.0, "width": 0, "height": 0, "has_audio": False}
    if not path.exists():
        return result
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration:stream=codec_type,width,height,duration",
        "-of", "json", str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, encoding="utf-8")
        data = json.loads(proc.stdout or "{}") if proc.returncode == 0 else {}
    except Exception:  # noqa: BLE001
        return result
    fmt_duration = (data.get("format") or {}).get("duration")
    try:
        result["duration"] = max(0.0, float(fmt_duration or 0.0))
    except (TypeError, ValueError):
        pass
    for stream in data.get("streams") or []:
        ctype = stream.get("codec_type")
        if ctype == "audio":
            result["has_audio"] = True
        elif ctype == "video":
            try:
                result["width"] = int(stream.get("width") or 0)
                result["height"] = int(stream.get("height") or 0)
                if not result["duration"]:
                    result["duration"] = max(0.0, float(stream.get("duration") or 0.0))
            except (TypeError, ValueError):
                pass
    return result


def _auto_source_paths(repo_root: Path, task_id: str) -> list[Path]:
    task_dir = repo_root / "tasks" / task_id
    preferred_outputs = [
        task_dir / "outputs" / "optimize_compose.mp4",
        task_dir / "outputs" / "rough_compose.mp4",
        task_dir / "outputs" / "fine_export.mp4",
    ]
    # 同一成片可能同时存在优化版、粗剪版和精剪版，默认只导入最成熟的一版，
    # 避免 8 个 1080p 解码器同时打开导致解码缓冲竞争和内存峰值。
    paths = [next((p for p in preferred_outputs if p.exists()), None)]
    meta_path = task_dir / "metadata.json"
    if meta_path.exists():
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            seg = data.get("segment") or {}
            if seg.get("path"):
                paths.append(_abs(repo_root, seg["path"]))
            if data.get("original_video_source"):
                paths.append(_abs(repo_root, data["original_video_source"]))
        except Exception:  # noqa: BLE001
            pass
    out: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        if path is None:
            continue
        key = str(path.resolve()).lower()
        if key not in seen and path.exists():
            out.append(path)
            seen.add(key)
    return [p for p in out if p is not None]


def _material_from_path(repo_root: Path, path: Path, kind: str, source: str) -> dict:
    info = probe_media(path)
    return {
        "id": new_id("mat"),
        "kind": kind,
        "name": path.name,
        "path": _rel(repo_root, path),
        "source": source,
        "duration": round(float(info.get("duration") or 0.0), 3),
        "width": int(info.get("width") or 0),
        "height": int(info.get("height") or 0),
        "has_audio": bool(info.get("has_audio")),
        "created_at": now_iso(),
    }


def create_project(
    repo_root: Path | str,
    task_id: str,
    name: str,
    brief: str = "",
    config: dict | None = None,
    *,
    kind: str = PROJECT_KIND_SPLIT,
) -> dict:
    root = Path(repo_root)
    tid = str(task_id or "").strip()
    if not tid:
        raise ShortVideoError("缺少 task_id")
    if not (root / "tasks" / tid).exists():
        raise ShortVideoError(f"任务不存在: {tid}")
    project_kind = str(kind or "").strip() or PROJECT_KIND_SPLIT
    if project_kind not in PROJECT_KINDS:
        raise ShortVideoError(f"未知项目类型: {project_kind}")
    project_id = new_id("sv")
    project = {
        "_schema": SCHEMA_VERSION,
        "id": project_id,
        "task_id": tid,
        "name": (name or "短视频混剪").strip()[:80],
        "brief": str(brief or "").strip()[:4000],
        "kind": project_kind,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "config": sanitize_config(config),
        "base_material_id": "",
        "materials": [],
        "storyboard": {"status": "empty", "variants": [], "model": "", "generated_at": ""},
        "render_jobs": [],
    }
    # REQ-20261003-098：仅 split 流程自动导入任务源视频；
    # mixcut 旧路径继续自动导入多个视频素材供 B-roll。
    if project_kind == PROJECT_KIND_SPLIT:
        for path in _auto_source_paths(root, tid):
            project["materials"].append(_material_from_path(root, path, "video", "auto"))
    save_project(root, project)
    return project


def list_projects_for_tasks(repo_root: Path | str, task_ids: Iterable[str]) -> list[dict]:
    root = Path(repo_root)
    out: list[dict] = []
    for tid in task_ids:
        base = root / "tasks" / tid / SHORT_VIDEO_DIR
        if not base.exists():
            continue
        for p in base.glob(f"*/{PROJECT_FILENAME}"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    out.append(_normalize_project(data, tid, p.parent.name))
            except Exception:  # noqa: BLE001
                log.warning("跳过损坏的短视频项目: %s", p)
    out.sort(key=lambda p: p.get("updated_at") or "", reverse=True)
    return out


def delete_project(repo_root: Path | str, task_id: str, project_id: str) -> bool:
    root = Path(repo_root)
    pdir = project_dir(root, task_id, project_id)
    odir = output_dir(root, task_id, project_id)
    allowed_root = (root / "tasks").resolve()
    if not _is_relative_to(pdir, allowed_root) or not _is_relative_to(odir, allowed_root):
        raise ShortVideoError("拒绝删除任务目录之外的路径")
    existed = pdir.exists() or odir.exists()
    if pdir.exists():
        shutil.rmtree(pdir)
    if odir.exists():
        shutil.rmtree(odir)
    return existed


def add_material_path(
    repo_root: Path | str,
    task_id: str,
    project_id: str,
    source_path: Path | str,
    kind: str = "auto",
    display_name: str = "",
) -> dict:
    """把一个文件收进项目素材库。

    ``display_name``：用户选文件时的原始文件名（上传链路的临时文件名是一串
    随机字符，直接用会显示无意义字符串）。素材的 ``name`` 字段展示原始名，
    磁盘文件名仍做安全化 + 唯一前缀（防穿越 / 防重名覆盖）。
    """
    root = Path(repo_root)
    project = load_project(root, task_id, project_id)
    src = Path(source_path)
    if not src.exists() or not src.is_file():
        raise ShortVideoError(f"素材文件不存在: {src}")
    original_name = Path(str(display_name or "")).name or src.name
    resolved_kind = kind_from_extension(original_name) if kind in ("", "auto") else kind
    if resolved_kind == "bg_image" and Path(original_name).suffix.lower() not in IMAGE_EXTS:
        raise ShortVideoError("背景图片仅支持图片文件")
    if resolved_kind not in ("video", "image", "audio", "bg_image"):
        raise ShortVideoError("素材类型仅支持 video / image / audio / bg_image")
    dst_dir = assets_dir(root, task_id, project_id)
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"{new_id('asset')}_{_safe_asset_name(original_name)}"
    if src.resolve() != dst.resolve():
        shutil.copy2(src, dst)
    material = _material_from_path(root, dst, resolved_kind, "upload")
    material["name"] = original_name
    project.setdefault("materials", []).append(material)
    save_project(root, project)
    return material


def remove_material(
    repo_root: Path | str,
    task_id: str,
    project_id: str,
    material_id: str,
) -> dict:
    """从项目素材库删除一个素材（REQ-20261001-096）。

    - 上传素材（source=upload）：连磁盘文件一起删（仅限本项目 assets 目录内）；
      任务自动导入的素材（source=auto）源文件属于任务本身，只移除引用不删文件。
    - 同步清理所有引用：基础视频、分镜 segments / overlays、BGM、背景图。
      基础视频被删后 ``base_material_id`` 置空，重新生成分镜时要求再选。
    """
    root = Path(repo_root)
    project = load_project(root, task_id, project_id)
    mid = str(material_id or "").strip()
    mat = next(
        (m for m in project.get("materials") or [] if m.get("id") == mid),
        None,
    )
    if not mat:
        raise ShortVideoError(f"素材不存在: {mid}")
    project["materials"] = [m for m in project["materials"] if m.get("id") != mid]
    if str(project.get("base_material_id") or "") == mid:
        project["base_material_id"] = ""
    for variant in (project.get("storyboard") or {}).get("variants") or []:
        if not isinstance(variant, dict):
            continue
        variant["segments"] = [
            s for s in variant.get("segments") or [] if s.get("material_id") != mid
        ]
        variant["overlays"] = [
            o for o in variant.get("overlays") or [] if o.get("material_id") != mid
        ]
        if variant.get("bgm_material_id") == mid:
            variant["bgm_material_id"] = ""
        if variant.get("bg_material_id") == mid:
            variant["bg_material_id"] = ""
    if str(mat.get("source") or "") == "upload":
        target = _abs(root, mat.get("path") or "")
        assets_root = assets_dir(root, task_id, project_id).resolve()
        if _is_relative_to(target, assets_root) and target.is_file():
            try:
                target.unlink(missing_ok=True)
            except OSError as e:  # noqa: PERF203
                log.warning("[short_video] 素材文件删除失败: %s (%s)", target, e)
    save_project(root, project)
    return project


def _fallback_variant(project: dict, index: int, base_id: str = "") -> dict:
    materials = project.get("materials") or []
    images = [m for m in materials if m.get("kind") == "image"]
    audios = [m for m in materials if m.get("kind") == "audio"]
    # REQ-20261001-095：主片段只用用户选定的基础视频，不再轮询全部视频素材
    base = next((m for m in materials if m.get("id") == base_id
                 and m.get("kind") == "video"), None)
    if not base:
        raise ShortVideoError("请先选择基础视频素材")
    cfg = sanitize_config(project.get("config"))
    target = min(cfg["duration_max"], max(cfg["duration_min"], 42 + index * 6))
    n_segments = 4
    segment_duration = max(3.0, min(8.0, target / n_segments))
    mat_duration = max(0.0, float(base.get("duration") or 0.0))
    if mat_duration > 0:
        segment_duration = min(segment_duration, mat_duration)
        n_segments = max(1, min(n_segments, int(mat_duration / max(1.0, segment_duration)) or 1))
    segments = []
    for i in range(n_segments):
        start = 0.0
        if mat_duration > 0 and n_segments > 1:
            span = max(0.0, mat_duration - segment_duration)
            start = round(span * i / (n_segments - 1), 3)
        segments.append({
            "id": f"seg_{index + 1}_{i + 1}",
            "material_id": base["id"],
            "start": start,
            "duration": round(max(1.0, segment_duration), 3),
            "transition": cfg["transition"],
        })
    overlays = []
    if images:
        overlays.append({
            "id": f"ov_{index + 1}_1",
            "material_id": images[index % len(images)]["id"],
            "start": 2.0,
            "duration": 3.0,
            "x": 690,
            "y": 150,
            "scale": 0.38,
        })
    brief = str(project.get("brief") or project.get("name") or "精彩内容").strip()
    chunks = [x.strip() for x in re.split(r"[。！？!?；;\n]+", brief) if x.strip()]
    if not chunks:
        chunks = ["精彩内容", "马上来看", "关注获取更多"]
    subtitles = []
    total = sum(float(s["duration"]) for s in segments)
    slot = max(1.5, min(4.0, total / max(1, len(chunks))))
    for i, text in enumerate(chunks[:12]):
        start = round(i * slot, 3)
        subtitles.append({
            "start": start,
            "end": round(min(total, start + slot * 0.92), 3),
            "text": text[:40],
        })
    return {
        "id": f"v{index + 1}",
        "name": f"版本 {index + 1}",
        "title": str(project.get("name") or "短视频")[:30],
        "hook": brief[:24],
        "cta": "关注获取更多内容",
        "segments": segments,
        "overlays": overlays,
        "subtitles": subtitles,
        "bgm_material_id": audios[index % len(audios)]["id"] if audios else "",
        "bgm_volume_db": cfg["bgm_volume_db"],
    }


def _extract_json(raw: str) -> dict:
    text = re.sub(r"```(?:json)?\s*|\s*```", "", raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ShortVideoError("LLM 未返回 JSON 对象")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ShortVideoError("LLM 返回值不是 JSON 对象")
    return data


def _llm_storyboard(repo_root: Path, project: dict, base_id: str = "") -> tuple[dict, str]:
    from slirn_home import llm_config, revision_service

    entry = llm_config.get_current_entry(repo_root)
    if not entry:
        raise ShortVideoError("未配置 LLM 模型")
    if not os.environ.get(str(entry.get("api_key_env") or ""), ""):
        raise ShortVideoError("当前模型 API Key 环境变量未配置")
    mats = [
        {
            "id": m.get("id"),
            "kind": m.get("kind"),
            "name": m.get("name"),
            "duration": m.get("duration"),
        }
        for m in (project.get("materials") or [])
    ]
    system = (
        "你是短视频混剪导演和分镜编辑。根据用户 Brief 与素材清单，输出可执行的"
        "结构化分镜 JSON。不要输出解释或 Markdown。所有素材引用必须使用给定 id。"
        "用户已指定基础视频（base_material_id）：segments 的 material_id 只能使用"
        "该基础视频（可在不同起点截取多段）；其余素材只能用于 overlays / BGM。"
    )
    user = json.dumps({
        "brief": project.get("brief") or "",
        "project_name": project.get("name") or "",
        "config": project.get("config") or default_config(),
        "base_material_id": base_id,
        "materials": mats,
        "required_shape": {
            "variants": [{
                "id": "v1",
                "name": "版本 1",
                "title": "标题",
                "hook": "开头 Hook",
                "cta": "结尾行动号召",
                "segments": [{
                    "material_id": "mat_id",
                    "start": 0,
                    "duration": 5,
                    "transition": "fade",
                }],
                "overlays": [{
                    "material_id": "mat_id",
                    "start": 2,
                    "duration": 3,
                    "x": 690,
                    "y": 150,
                    "scale": 0.38,
                }],
                "subtitles": [{"start": 0, "end": 2.5, "text": "字幕"}],
                "bgm_material_id": "mat_id",
                "bgm_volume_db": -16,
            }]
        },
    }, ensure_ascii=False)
    raw = revision_service._call_llm(system, user, entry=entry, retries=1)
    return _extract_json(raw), str(entry.get("id") or "")


def _normalize_variant(
    variant: dict,
    project: dict,
    index: int,
) -> dict:
    materials = {m.get("id"): m for m in project.get("materials") or []}
    cfg = sanitize_config(project.get("config"))
    v = variant if isinstance(variant, dict) else {}
    result = {
        "id": str(v.get("id") or f"v{index + 1}")[:40],
        "name": str(v.get("name") or f"版本 {index + 1}")[:60],
        "title": str(v.get("title") or project.get("name") or "短视频")[:60],
        "hook": str(v.get("hook") or project.get("brief") or "精彩内容")[:80],
        "cta": str(v.get("cta") or "关注获取更多内容")[:60],
        "segments": [],
        "overlays": [],
        "subtitles": [],
        "bgm_material_id": str(v.get("bgm_material_id") or ""),
        "bgm_volume_db": _clamp_float(v.get("bgm_volume_db"), cfg["bgm_volume_db"], -40.0, 0.0),
        # REQ-20261001-095：背景图只接受 bg_image 素材（普通 image 是 B-roll）
        "bg_material_id": "",
    }
    bg_mat = materials.get(str(v.get("bg_material_id") or ""))
    if bg_mat and bg_mat.get("kind") == "bg_image":
        result["bg_material_id"] = bg_mat["id"]
    for seg in (v.get("segments") or [])[:6]:
        if not isinstance(seg, dict):
            continue
        mat = materials.get(seg.get("material_id"))
        if not mat or mat.get("kind") != "video":
            continue
        duration = _clamp_float(seg.get("duration"), 5.0, 0.5, 30.0)
        if float(mat.get("duration") or 0) > 0:
            duration = min(duration, float(mat["duration"]))
        transition = str(seg.get("transition") or cfg["transition"])
        if transition not in ALLOWED_TRANSITIONS:
            transition = cfg["transition"]
        result["segments"].append({
            "id": str(seg.get("id") or new_id("seg"))[:50],
            "material_id": mat["id"],
            "start": round(max(0.0, _clamp_float(seg.get("start"), 0.0, 0.0, 86400.0)), 3),
            "duration": round(max(0.5, duration), 3),
            "transition": transition,
        })
    for ov in (v.get("overlays") or [])[:8]:
        if not isinstance(ov, dict):
            continue
        mat = materials.get(ov.get("material_id"))
        if not mat or mat.get("kind") not in ("video", "image"):
            continue
        result["overlays"].append({
            "id": str(ov.get("id") or new_id("ov"))[:50],
            "material_id": mat["id"],
            "start": round(max(0.0, _clamp_float(ov.get("start"), 0.0, 0.0, 86400.0)), 3),
            "duration": round(_clamp_float(ov.get("duration"), 3.0, 0.5, 30.0), 3),
            "x": _clamp_int(ov.get("x"), 690, -2000, 3000),
            "y": _clamp_int(ov.get("y"), 150, -3000, 4000),
            "scale": round(_clamp_float(ov.get("scale"), 0.38, 0.05, 3.0), 4),
        })
    for sub in (v.get("subtitles") or [])[:40]:
        if not isinstance(sub, dict):
            continue
        text = str(sub.get("text") or "").strip()
        if not text:
            continue
        start = max(0.0, _clamp_float(sub.get("start"), 0.0, 0.0, 86400.0))
        end = max(start + 0.2, _clamp_float(sub.get("end"), start + 2.0, 0.0, 86400.0))
        result["subtitles"].append({
            "start": round(start, 3),
            "end": round(end, 3),
            "text": text[:120],
        })
    if not result["segments"]:
        raise ShortVideoError("分镜没有有效视频片段")
    total = sum(float(s["duration"]) for s in result["segments"])
    if result["subtitles"]:
        result["subtitles"] = [
            {
                **s,
                "start": round(min(float(s["start"]), max(0.0, total - 0.2)), 3),
                "end": round(min(float(s["end"]), total), 3),
            }
            for s in result["subtitles"]
            if float(s["end"]) <= total + 0.001 or float(s["start"]) < total
        ]
    return result


def generate_storyboard(
    repo_root: Path | str,
    task_id: str,
    project_id: str,
    *,
    use_external_llm: bool | None = None,
    base_material_id: str | None = None,
) -> dict:
    """生成分镜。REQ-20261001-095：必须由用户指定基础视频（不再自动挑选）。

    ``base_material_id`` 显式传参优先，否则读项目里保存的选择；
    缺失 / 非法 → 抛错（前端提示用户先在下拉里选择）。
    """
    root = Path(repo_root)
    project = load_project(root, task_id, project_id)
    base_id = str(base_material_id or "").strip() or str(project.get("base_material_id") or "")
    base_mat = next(
        (m for m in project.get("materials") or []
         if m.get("id") == base_id and m.get("kind") == "video"),
        None,
    )
    if not base_mat:
        raise ShortVideoError("请先选择基础视频（生成分镜的主素材）")
    project["base_material_id"] = base_mat["id"]
    outputs_dir = root / "tasks" / task_id / "outputs"
    exec_id = ""
    try:
        from slirn_home import execution_history

        exec_id = execution_history.record_start(
            outputs_dir,
            execution_history.KIND_SHORT_VIDEO_AI,
            extra={"project_id": project_id},
        )
    except Exception:  # noqa: BLE001
        pass
    cfg = sanitize_config(project.get("config"))
    allow = cfg["allow_external_llm"] if use_external_llm is None else bool(use_external_llm)
    model = ""
    error = ""
    variants_raw: list[dict] = []
    if allow:
        try:
            payload, model = _llm_storyboard(root, project, base_mat["id"])
            variants_raw = list(payload.get("variants") or [])
        except Exception as e:  # noqa: BLE001
            error = str(e)
            log.warning("[short_video] LLM 分镜失败，转本地兜底: %s", e)
    if not variants_raw:
        model = "local-rules"
        variants_raw = [_fallback_variant(project, i, base_mat["id"])
                        for i in range(cfg["variants"])]
    variants = []
    for i, raw in enumerate(variants_raw[: cfg["variants"]]):
        try:
            # LLM 违规引用了非基础视频 → 统一替换为基础视频再规范化
            if isinstance(raw, dict):
                for seg in raw.get("segments") or []:
                    if isinstance(seg, dict) and seg.get("material_id") != base_mat["id"]:
                        seg["material_id"] = base_mat["id"]
            variants.append(_normalize_variant(raw, project, i))
        except ShortVideoError:
            variants.append(
                _normalize_variant(_fallback_variant(project, i, base_mat["id"]), project, i)
            )
    if len(variants) < cfg["variants"]:
        for i in range(len(variants), cfg["variants"]):
            variants.append(
                _normalize_variant(_fallback_variant(project, i, base_mat["id"]), project, i)
            )
    project["storyboard"] = {
        "status": "ready",
        "model": model,
        "generated_at": now_iso(),
        "error": error,
        "base_material_id": base_mat["id"],
        "variants": variants,
    }
    save_project(root, project)
    if exec_id:
        try:
            from slirn_home import execution_history

            execution_history.patch_extra(
                outputs_dir, exec_id, {"project_id": project_id, "model": model}
            )
            execution_history.record_finish(outputs_dir, exec_id, success=True, error="")
        except Exception:  # noqa: BLE001
            pass
    return project


def update_project(
    repo_root: Path | str,
    task_id: str,
    project_id: str,
    payload: dict,
) -> dict:
    root = Path(repo_root)
    project = load_project(root, task_id, project_id)
    if "name" in payload:
        project["name"] = str(payload.get("name") or project["name"]).strip()[:80]
    if "brief" in payload:
        project["brief"] = str(payload.get("brief") or "").strip()[:4000]
    if "base_material_id" in payload:
        value = str(payload.get("base_material_id") or "").strip()
        if value:
            mat = next(
                (m for m in project.get("materials") or []
                 if m.get("id") == value and m.get("kind") == "video"),
                None,
            )
            if not mat:
                raise ShortVideoError("基础视频必须是项目里的视频素材")
            value = mat["id"]
        project["base_material_id"] = value
    if isinstance(payload.get("config"), dict):
        project["config"] = sanitize_config({**project.get("config", {}), **payload["config"]})
    if isinstance(payload.get("storyboard"), dict):
        raw_variants = payload["storyboard"].get("variants")
        if isinstance(raw_variants, list):
            normalized = []
            for i, variant in enumerate(raw_variants[: project["config"]["variants"]]):
                normalized.append(_normalize_variant(variant, project, i))
            project["storyboard"] = {
                **project.get("storyboard", {}),
                "status": "ready",
                "variants": normalized,
                "updated_at": now_iso(),
            }
    save_project(root, project)
    return project


def material_abs_path(repo_root: Path | str, project: dict, material_id: str) -> Path:
    for material in project.get("materials") or []:
        if material.get("id") == material_id:
            return _abs(Path(repo_root), material.get("path") or "")
    raise ShortVideoError(f"素材不存在: {material_id}")


def select_source_material(
    repo_root: Path | str,
    task_id: str,
    project_id: str,
    material_id: str,
) -> dict:
    """Stage 1：把指定素材标记为「源视频」（split 流程的主素材）。

    与旧 mixcut 的 ``base_material_id`` 共享同一字段（语义上一致），
    校验素材存在 + kind=video。保存后会自动重置下游阶段。
    """
    root = Path(repo_root)
    project = load_project(root, task_id, project_id)
    mat = next(
        (m for m in project.get("materials") or []
         if m.get("id") == material_id and m.get("kind") == "video"),
        None,
    )
    if not mat:
        raise ShortVideoError("源视频必须是项目里的视频素材")
    project["base_material_id"] = mat["id"]
    pipeline = project.setdefault("pipeline", {})
    pipeline["stage1_source"] = {
        "status": "done",
        "source_material_id": mat["id"],
        "source_name": mat.get("name") or "",
        "duration": float(mat.get("duration") or 0.0),
        "width": int(mat.get("width") or 0),
        "height": int(mat.get("height") or 0),
        "set_at": now_iso(),
    }
    # 源变了 → 下游 stage 2-6 全部作废
    reset_downstream_stages(project, "stage1_source")
    save_project(root, project)
    return project


# ---------- REQ-20261003-098：Stage 6（精剪混编）辅助 ----------

def stage6_dir(repo_root: Path | str, task_id: str, project_id: str) -> Path:
    """Stage 6 产物目录：``tasks/<tid>/short_video/<pid>/stage6/``。

    与长视频项目目录（``tasks/<tid>/outputs/...``）完全隔离 —— 长视频的
    cut_by_srt.py 产物不会写到本目录，反之亦然。
    """
    return project_dir(repo_root, task_id, project_id) / STAGE6_DIR


def stage5_dir(repo_root: Path | str, task_id: str, project_id: str) -> Path:
    return project_dir(repo_root, task_id, project_id) / STAGE5_DIR


def stage4_dir(repo_root: Path | str, task_id: str, project_id: str) -> Path:
    return project_dir(repo_root, task_id, project_id) / STAGE4_DIR


def stage3_dir(repo_root: Path | str, task_id: str, project_id: str) -> Path:
    return project_dir(repo_root, task_id, project_id) / STAGE3_DIR


def stage3_verify_dir(repo_root: Path | str, task_id: str, project_id: str) -> Path:
    return project_dir(repo_root, task_id, project_id) / STAGE3_VERIFY_DIR


def stage2_dir(repo_root: Path | str, task_id: str, project_id: str) -> Path:
    return project_dir(repo_root, task_id, project_id) / STAGE2_DIR


def stage1_dir(repo_root: Path | str, task_id: str, project_id: str) -> Path:
    return project_dir(repo_root, task_id, project_id) / STAGE1_DIR


def stage_raw_srt_path(repo_root: Path | str, task_id: str, project_id: str) -> Path:
    return stage2_dir(repo_root, task_id, project_id) / RAW_SRT_NAME


def stage_raw_json_path(repo_root: Path | str, task_id: str, project_id: str) -> Path:
    return stage2_dir(repo_root, task_id, project_id) / RAW_JSON_NAME


def stage_highlights_path(repo_root: Path | str, task_id: str, project_id: str) -> Path:
    return stage3_dir(repo_root, task_id, project_id) / HIGHLIGHTS_JSON_NAME


def stage_llm_raw_path(repo_root: Path | str, task_id: str, project_id: str) -> Path:
    return stage3_dir(repo_root, task_id, project_id) / HIGHLIGHTS_LLM_RAW_NAME


CUSTOM_PROMPTS_NAME = "custom_prompts.json"


def custom_prompts_path(repo_root: Path | str, task_id: str, project_id: str) -> Path:
    """REQ-20261004-stage3-prompt-edit：用户修改后的 3 个模板提示词。"""
    return stage3_dir(repo_root, task_id, project_id) / CUSTOM_PROMPTS_NAME


def load_custom_prompts(repo_root: Path | str, task_id: str, project_id: str) -> dict:
    """读 custom_prompts.json。文件不存在/损坏 → 返回空 dict。

    返回 {template_name: {"prompt": "...", "updated_at": "..."}}。
    """
    p = custom_prompts_path(repo_root, task_id, project_id)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def save_custom_prompt(
    repo_root: Path | str,
    task_id: str,
    project_id: str,
    template: str,
    prompt: str,
) -> dict:
    """保存用户对某模板的修改提示词。覆盖同名 key。"""
    root = Path(repo_root)
    data = load_custom_prompts(root, task_id, project_id)
    data[template] = {
        "prompt": str(prompt or "").strip(),
        "updated_at": now_iso(),
    }
    p = custom_prompts_path(root, task_id, project_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(p, data)
    return data[template]


def reset_custom_prompt(
    repo_root: Path | str,
    task_id: str,
    project_id: str,
    template: str,
) -> bool:
    """删除某模板的用户修改（恢复系统默认）。"""
    root = Path(repo_root)
    data = load_custom_prompts(root, task_id, project_id)
    if template not in data:
        return False
    del data[template]
    p = custom_prompts_path(root, task_id, project_id)
    if data:
        _write_json_atomic(p, data)
    else:
        # 空 dict → 删文件
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    return True


def stage4_paths(
    repo_root: Path | str,
    task_id: str,
    project_id: str,
    index: int,
) -> tuple[Path, Path]:
    """Stage 4 单条 highlight 的 (coarse mp4, coarse srt) 路径。"""
    base = stage4_dir(repo_root, task_id, project_id)
    return (
        base / f"{COARSE_MP4_PREFIX}_{index:02d}.mp4",
        base / f"{COARSE_SRT_PREFIX}_{index:02d}.srt",
    )


def stage5_refined_srt_path(
    repo_root: Path | str,
    task_id: str,
    project_id: str,
    index: int,
) -> Path:
    return stage5_dir(repo_root, task_id, project_id) / f"{REFINED_SRT_PREFIX}_{index:02d}.srt"


def stage5_default_srt_path(
    repo_root: Path | str,
    task_id: str,
    project_id: str,
    index: int,
) -> Path:
    """Stage 5 默认（ASR 原始）字幕；用户未手动修正时使用此文件。"""
    return stage5_dir(repo_root, task_id, project_id) / f"{REFINED_SRT_PREFIX}_{index:02d}_asr.srt"


def stage6_paths(
    repo_root: Path | str,
    task_id: str,
    project_id: str,
    index: int,
) -> tuple[Path, Path]:
    """Stage 6 单条 highlight 的 (final mp4, continuous srt) 路径。

    ``index`` 从 1 开始（与 Stage 4/5 的命名一致）。
    """
    base = stage6_dir(repo_root, task_id, project_id)
    return (
        base / f"{FINAL_MP4_PREFIX}_{index:02d}.mp4",
        base / f"{FINAL_SRT_PREFIX}_{index:02d}.srt",
    )


def get_stage6_state(project: dict) -> dict:
    """读 stage6 状态字段（缺则视为空）。"""
    pipeline = project.get("pipeline") or {}
    state = pipeline.get("stage6_finalize") or {}
    if not isinstance(state, dict):
        state = {}
    state.setdefault("status", "pending")
    state.setdefault("highlights", [])
    state.setdefault("logs", [])
    return state


def set_stage6_state(project: dict, state: dict) -> None:
    pipeline = project.setdefault("pipeline", {})
    pipeline["stage6_finalize"] = state


# ---------- 通用阶段状态读写 ----------

_STAGE_STATE_KEYS = {
    "stage1_source": "stage1_source",
    "stage2_extract": "stage2_extract",
    "stage3_analyze": "stage3_analyze",
    "stage3_verify": "stage3_verify",
    "stage4_coarse": "stage4_coarse",
    "stage5_refine": "stage5_refine",
    "stage6_finalize": "stage6_finalize",
}


def get_stage_state(project: dict, stage: str) -> dict:
    """读某阶段状态字段。"""
    key = _STAGE_STATE_KEYS.get(stage)
    if not key:
        raise ValueError(f"未知阶段: {stage}")
    pipeline = project.get("pipeline") or {}
    state = pipeline.get(key) or {}
    if not isinstance(state, dict):
        state = {}
    state.setdefault("status", "pending")
    return state


def set_stage_state(project: dict, stage: str, state: dict) -> None:
    key = _STAGE_STATE_KEYS.get(stage)
    if not key:
        raise ValueError(f"未知阶段: {stage}")
    pipeline = project.setdefault("pipeline", {})
    pipeline[key] = state


def reset_downstream_stages(project: dict, after_stage: str) -> None:
    """把指定阶段之后的所有阶段重置为 pending（用于上游阶段重跑时清空下游）。

    ``after_stage`` 应是 "stage1_source" / "stage2_extract" / ... / "stage5_refine"。
    "stage6_finalize" 之后没有下游，无需重置。
    """
    order = [
        "stage1_source",
        "stage2_extract",
        "stage3_analyze",
        "stage3_verify",
        "stage4_coarse",
        "stage5_refine",
        "stage6_finalize",
    ]
    if after_stage not in order:
        return
    idx = order.index(after_stage)
    pipeline = project.setdefault("pipeline", {})
    for stage_key in order[idx + 1:]:
        if stage_key in pipeline:
            pipeline[stage_key] = {"status": "pending"}


def reorder_subtitle_line(
    repo_root: Path | str,
    task_id: str,
    project_id: str,
    hl_index: int,
    src_index: int,
    before_src_index: int | None,
) -> dict:
    """REQ-20261004-stage3-restructure：拖拽重排某条字幕行。

    - 读 ``stage3/highlights.json``，找到 ``index == hl_index`` 的 highlight
    - 把 ``src_index == src_index`` 的 subtitle_line 从原位置移除
    - 把这条插入到 ``src_index == before_src_index`` 这条**之前**（None = 末尾）
    - 写回 highlights.json + 同步更新 pipeline.stage3_analyze.highlights 镜像
    - 触发 ``reset_downstream_stages("stage3_analyze")``（下游 Stage 4/5/6 作废）

    返回更新后的 highlight dict（含新 subtitle_lines 顺序）。
    """
    root = Path(repo_root)
    hp = stage_highlights_path(root, task_id, project_id)
    if not hp.is_file():
        raise ShortVideoError("highlights.json 不存在，请先跑 Stage 3")
    try:
        doc = json.loads(hp.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise ShortVideoError(f"highlights.json 解析失败: {e}") from e
    hls = doc.get("highlights") or []
    target = None
    target_pos = -1
    for i, h in enumerate(hls):
        if int(h.get("index") or 0) == int(hl_index):
            target = h
            target_pos = i
            break
    if target is None:
        raise ShortVideoError(f"highlight #{hl_index} 不存在")

    lines = target.get("subtitle_lines") or []
    moving = None
    moving_pos = -1
    for i, sl in enumerate(lines):
        try:
            if int(sl.get("src_index") or 0) == int(src_index):
                moving = sl
                moving_pos = i
                break
        except (TypeError, ValueError):
            continue
    if moving is None:
        raise ShortVideoError(f"highlight #{hl_index} 没有 src_index={src_index} 的字幕行")
    # 移除
    lines.pop(moving_pos)
    # 找 before 位置（移除后的位置）
    insert_at = len(lines)
    if before_src_index is not None:
        for i, sl in enumerate(lines):
            try:
                if int(sl.get("src_index") or 0) == int(before_src_index):
                    insert_at = i
                    break
            except (TypeError, ValueError):
                continue
    lines.insert(insert_at, moving)
    target["subtitle_lines"] = lines
    hls[target_pos] = target

    # 写 highlights.json（保留其他字段）
    doc["highlights"] = hls
    _write_json_atomic(hp, doc)

    # 同步 project state
    project = load_project(root, task_id, project_id)
    pl = project.setdefault("pipeline", {})
    s3 = pl.setdefault("stage3_analyze", {})
    state_hls = s3.get("highlights") or []
    for sh in state_hls:
        if int(sh.get("index") or 0) == int(hl_index):
            sh["subtitle_lines"] = list(lines)
            break
    else:
        state_hls.append({
            "index": int(hl_index),
            "title": target.get("title"),
            "start_ms": target.get("start_ms"),
            "end_ms": target.get("end_ms"),
            "subtitle_lines": list(lines),
        })
        s3["highlights"] = state_hls
    s3["warnings"] = list(s3.get("warnings") or []) + [
        f"[{now_iso()}] subtitle_lines 重排：hl #{hl_index} src_index={src_index} → before src_index={before_src_index}",
    ]
    # 字幕顺序变了 → 下游全部作废
    reset_downstream_stages(project, "stage3_analyze")
    save_project(root, project)
    return target


def find_long_video_state_root(task_dir: Path) -> list[Path]:
    """返回 ``task_dir`` 下所有「长视频 cut_by_srt.py 可能写入」的位置。

    Stage 6 必须验证：本次调用的产物**没有**写到这些路径之下。
    仅用于运行时校验（产物隔离测试），不参与业务逻辑。
    """
    roots: list[Path] = []
    outputs = task_dir / "outputs"
    if outputs.exists():
        for child in outputs.iterdir():
            if child.is_dir():
                roots.append(child)
            elif child.is_file() and child.suffix.lower() in {".mp4", ".srt", ".txt"}:
                roots.append(child)
    return roots
