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
OUTPUTS_REL = Path("outputs") / "short_videos"
PROJECT_FILENAME = "project.json"
SCHEMA_VERSION = 1

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
    data.setdefault("created_at", now_iso())
    data.setdefault("updated_at", data["created_at"])
    data["config"] = sanitize_config(data.get("config"))
    data.setdefault("materials", [])
    data.setdefault("storyboard", {"status": "empty", "variants": []})
    data.setdefault("render_jobs", [])
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
) -> dict:
    root = Path(repo_root)
    tid = str(task_id or "").strip()
    if not tid:
        raise ShortVideoError("缺少 task_id")
    if not (root / "tasks" / tid).exists():
        raise ShortVideoError(f"任务不存在: {tid}")
    project_id = new_id("sv")
    project = {
        "_schema": SCHEMA_VERSION,
        "id": project_id,
        "task_id": tid,
        "name": (name or "短视频混剪").strip()[:80],
        "brief": str(brief or "").strip()[:4000],
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "config": sanitize_config(config),
        "materials": [],
        "storyboard": {"status": "empty", "variants": [], "model": "", "generated_at": ""},
        "render_jobs": [],
    }
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
) -> dict:
    root = Path(repo_root)
    project = load_project(root, task_id, project_id)
    src = Path(source_path)
    if not src.exists() or not src.is_file():
        raise ShortVideoError(f"素材文件不存在: {src}")
    resolved_kind = kind_from_extension(src.name) if kind in ("", "auto") else kind
    if resolved_kind not in ("video", "image", "audio"):
        raise ShortVideoError("素材类型仅支持 video / image / audio")
    dst_dir = assets_dir(root, task_id, project_id)
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"{new_id('asset')}_{_safe_asset_name(src.name)}"
    if src.resolve() != dst.resolve():
        shutil.copy2(src, dst)
    material = _material_from_path(root, dst, resolved_kind, "upload")
    project.setdefault("materials", []).append(material)
    save_project(root, project)
    return material


def _fallback_variant(project: dict, index: int) -> dict:
    materials = project.get("materials") or []
    videos = [m for m in materials if m.get("kind") == "video"]
    images = [m for m in materials if m.get("kind") == "image"]
    audios = [m for m in materials if m.get("kind") == "audio"]
    if not videos:
        raise ShortVideoError("至少需要一个视频素材")
    cfg = sanitize_config(project.get("config"))
    target = min(cfg["duration_max"], max(cfg["duration_min"], 42 + index * 6))
    n_segments = max(4, min(5, len(videos) + 3))
    segment_duration = max(3.0, min(8.0, target / n_segments))
    segments = []
    for i in range(n_segments):
        mat = videos[(i + index) % len(videos)]
        duration = segment_duration
        if float(mat.get("duration") or 0) > 0:
            duration = min(duration, float(mat["duration"]))
        segments.append({
            "id": f"seg_{index + 1}_{i + 1}",
            "material_id": mat["id"],
            "start": 0.0,
            "duration": round(max(1.0, duration), 3),
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


def _llm_storyboard(repo_root: Path, project: dict) -> tuple[dict, str]:
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
    )
    user = json.dumps({
        "brief": project.get("brief") or "",
        "project_name": project.get("name") or "",
        "config": project.get("config") or default_config(),
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
    }
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
) -> dict:
    root = Path(repo_root)
    project = load_project(root, task_id, project_id)
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
            payload, model = _llm_storyboard(root, project)
            variants_raw = list(payload.get("variants") or [])
        except Exception as e:  # noqa: BLE001
            error = str(e)
            log.warning("[short_video] LLM 分镜失败，转本地兜底: %s", e)
    if not variants_raw:
        model = "local-rules"
        variants_raw = [_fallback_variant(project, i) for i in range(cfg["variants"])]
    variants = []
    for i, raw in enumerate(variants_raw[: cfg["variants"]]):
        try:
            variants.append(_normalize_variant(raw, project, i))
        except ShortVideoError:
            variants.append(_normalize_variant(_fallback_variant(project, i), project, i))
    if len(variants) < cfg["variants"]:
        for i in range(len(variants), cfg["variants"]):
            variants.append(_normalize_variant(_fallback_variant(project, i), project, i))
    project["storyboard"] = {
        "status": "ready",
        "model": model,
        "generated_at": now_iso(),
        "error": error,
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
