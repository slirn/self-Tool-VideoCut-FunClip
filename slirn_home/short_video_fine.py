"""REQ-20261013-105：短视频 Stage 7 精剪 v1 完整版（按长视频 fine 整套搬）。

包含 4 块：
1. ``run_fine_one / run_fine_batch``：单条 + 批量精剪（基于 short_video_finalize.run_stage6_one
   套 6 素材卡 + 字体 + BGM）
2. ``_JOBS`` 异步 job 表（仿 compose_service.start_compose 模式）
3. ``params`` 6 组子字段（materials / layout / font / output / audio / preview）落
   ``project.json → pipeline.stage7_finalize.params``
4. ``short_video_fine_profiles`` 子模块：模板管理（6 端点）
5. ``short_video_bg_detect`` 子模块：bg 白底检测（端点 + 落 detected_region）
6. ``short_video_ai_layout`` 子模块：AI 智能布局（端点 + 落 layout.{video,subtitle,cover,bg}）

短/长隔离：execution_history 写短视频 stage6_dir；模板落
``tasks/_global_short_video_fine_profiles.json``，绝不写长视频
``tasks/_global_fine_profiles.json``。
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from slirn_home import short_video_finalize as _sv_finalize
from slirn_home import short_video_service as svc

log = logging.getLogger(__name__)


# ---------- 异步 job 表（仿 compose_service.start_compose） ----------

_JOBS: dict[tuple[str, str], dict] = {}
_JOBS_LOCK = threading.Lock()
# 跨项目串行锁（仿 app.py _FINE_RENDER_GATE；防止 ffmpeg 撞）
_RENDER_GATE = threading.Lock()


def _job_key(task_id: str, project_id: str) -> tuple[str, str]:
    return (str(task_id or ""), str(project_id or ""))


def job_status(task_id: str, project_id: str) -> dict | None:
    with _JOBS_LOCK:
        j = _JOBS.get(_job_key(task_id, project_id))
        return dict(j) if j else None


def _set_job(task_id: str, project_id: str, **fields) -> None:
    with _JOBS_LOCK:
        key = _job_key(task_id, project_id)
        cur = _JOBS.get(key) or {"state": "idle", "progress": 0.0,
                                  "stage": "", "error": None,
                                  "started_at": 0.0, "finished_at": None,
                                  "count": 0, "total_count": 0,
                                  "result": None}
        cur.update(fields)
        _JOBS[key] = cur


def start_fine_async(repo_root: Path | str, task_id: str, project_id: str) -> bool:
    """启动异步精剪线程。已在跑 → False。"""
    key = _job_key(task_id, project_id)
    with _JOBS_LOCK:
        existing = _JOBS.get(key)
        if existing and existing.get("state") == "running":
            return False
        _JOBS[key] = {
            "state": "running", "progress": 0.0, "stage": "准备",
            "error": None, "started_at": time.time(), "finished_at": None,
            "count": 0, "total_count": 0, "result": None,
        }

    def _run() -> None:
        try:
            # 跨项目串行（仿 _FINE_RENDER_GATE；短视频单 ffmpeg 一次）
            with _RENDER_GATE:
                from slirn_home import execution_history
                stage6_dir = svc.stage6_dir(repo_root, task_id, project_id)
                eid = execution_history.record_start(
                    stage6_dir,
                    execution_history.KIND_SHORT_VIDEO_FINE,
                )
                try:
                    res = run_fine_batch(
                        repo_root, task_id, project_id,
                        progress_cb=lambda ev, p: _set_job(
                            task_id, project_id,
                            stage=p.get("stage") or "合成中",
                            progress=p.get("progress", 0.0),
                            count=p.get("count", 0),
                            total_count=p.get("total", 0),
                        ),
                    )
                    _set_job(task_id, project_id,
                             state="done", progress=100.0, stage="完成",
                             finished_at=time.time(), result=res)
                    execution_history.record_finish(stage6_dir, eid, success=True)
                except Exception as e:  # noqa: BLE001
                    _set_job(task_id, project_id,
                             state="failed", stage="失败", error=str(e)[:200],
                             finished_at=time.time())
                    execution_history.record_finish(
                        stage6_dir, eid, success=False, error=str(e)[:200],
                    )
        except Exception as e:  # noqa: BLE001 — 线程兜底
            log.exception("[fine] 异步线程异常")
            _set_job(task_id, project_id,
                     state="failed", error=str(e)[:200], finished_at=time.time())

    t = threading.Thread(target=_run, daemon=True, name=f"sv-fine-{task_id}-{project_id}")
    t.start()
    return True


# ---------- 6 组 params 子字段（落 project.json → stage7_finalize.params） ----------

DEFAULT_PARAMS: dict[str, Any] = {
    "materials": {
        "video": "",        # 粗剪 mp4 path（项目内相对路径；运行时由 stage5_coarse.highlights 推导）
        "subtitle": "",     # refined_NN.srt path
        "cover": "",        # 封面图（可选）
        "bg": "",           # 背景图（可选）
        "reference": "",    # 参考图（AI 布局用）
        "audio": "",        # 附加音频轨（可选）
    },
    "layout": {
        "video": {"x": 0, "y": 0, "w": 1080, "h": 1920},
        "subtitle": {"x": 60, "y": 1620, "w": 960, "h": 240},
        "cover": {"x": 0, "y": 0, "w": 1080, "h": 1080},
        "bg": {"x": 0, "y": 0, "w": 1080, "h": 1920},
    },
    "font": {
        "family": "STHeitiMedium.ttc",
        "size": 48,
        "bold": False,
        "align": "center",
        "offset_ms": 0,
        "stroke_width": 2,
        "color": "#FFFFFF",
        "stroke_color": "#000000",
        "bg_enabled": False,
        "bg_color": "#000000",
        "bg_opacity": 0.5,
        "bg_radius": 8,
    },
    "output": {
        "resolution": "720p",  # 720p / 1080p / source
        "fps": 30,
        "codec": "libx264",
    },
    "audio": {
        "enabled": False,
        "bgm_path": "",        # 系统默认或上传的 BGM 绝对路径
        "volume_db": -6,
        "fade_in_ms": 500,
        "fade_out_ms": 1000,
    },
    "preview": {
        "start_ms": 0,
        "duration_sec": 5,     # 2-30s
    },
    "detected_region": None,  # bg 检测结果（dict：{x, y, w, h}）
}


def _get_params(project: dict) -> dict:
    """读 stage7_finalize.params；缺省补齐 6 组子字段。"""
    s = svc.get_stage_state(project, "stage7_finalize")
    p = s.get("params")
    if not isinstance(p, dict):
        p = {}
    # 浅合并各组（保用户已有值；缺字段用默认）
    for group_key, group_default in DEFAULT_PARAMS.items():
        if group_key not in p or not isinstance(p[group_key], dict):
            p[group_key] = dict(group_default) if isinstance(group_default, dict) else group_default
    return p


def save_params(repo_root: Path | str, task_id: str, project_id: str,
                params: dict) -> dict:
    """保存 6 组子字段到 project.json（验证后浅合并）。"""
    root = Path(repo_root)
    project = svc.load_project(root, task_id, project_id)
    current = _get_params(project)
    # 浅合并每组
    for group_key, group_val in (params or {}).items():
        if group_key not in DEFAULT_PARAMS:
            continue
        if isinstance(group_val, dict) and isinstance(current.get(group_key), dict):
            current[group_key].update(group_val)
        else:
            current[group_key] = group_val
    s = svc.get_stage_state(project, "stage7_finalize")
    s["params"] = current
    svc.set_stage_state(project, "stage7_finalize", s)
    svc.save_project(root, project)
    return {"params": current}


def load_default_params() -> dict:
    """返回默认 params（前端首次加载用）。"""
    return json.loads(json.dumps(DEFAULT_PARAMS))  # 深拷贝


# ---------- 精剪主流程：run_fine_one / run_fine_batch ----------

def _resolve_video_path(root: Path, hl: dict, project_id: str, task_id: str) -> Path:
    """粗剪 mp4 绝对路径（项目内相对路径 → 绝对）。"""
    rel = str(hl.get("coarse_mp4") or "")
    if not rel:
        return root / "tasks" / task_id / "short_video" / project_id / "stage4" / "coarse.mp4"
    cand = (root / rel).resolve()
    return cand


def _resolve_subtitle_path(root: Path, hl: dict, index: int, project_id: str, task_id: str) -> Path:
    rel = str(hl.get("refined_srt") or hl.get("asr_srt") or "")
    if rel:
        return (root / rel).resolve()
    return svc.stage5_refined_srt_path(root, task_id, project_id, index)


def run_fine_one(repo_root: Path | str, task_id: str, project_id: str,
                 index: int, *, progress_cb=None) -> dict:
    """单 highlight 精剪：复用 short_video_finalize.run_stage6_one（cut_by_srt 子进程）。

    progress_cb(event, payload) 事件契约：
    - fine_clip_start: {index, total}
    - fine_clip_done: {index, total, mp4_url, srt_url}
    - fine_clip_failed: {index, total, error}
    """
    root = Path(repo_root)
    project = svc.load_project(root, task_id, project_id)
    s5 = svc.get_stage_state(project, "stage5_coarse")
    s6 = svc.get_stage_state(project, "stage6_refine")
    coarse_hls = s5.get("highlights") or []
    refine_hls = s6.get("highlights") or []
    # 按 index 找对应 highlight
    coarse_hl = next((h for h in coarse_hls if int(h.get("index") or 0) == int(index)), None)
    if not coarse_hl:
        raise svc.ShortVideoError(f"找不到 highlight #{index}")
    refine_hl = next((h for h in refine_hls if int(h.get("index") or 0) == int(index)), {})

    coarse_mp4 = _resolve_video_path(root, coarse_hl, project_id, task_id)
    refined_srt = _resolve_subtitle_path(root, refine_hl, index, project_id, task_id)
    if not coarse_mp4.is_file():
        raise svc.ShortVideoError(f"粗剪 mp4 不存在: {coarse_mp4}")
    if not refined_srt.is_file():
        raise svc.ShortVideoError(f"refined srt 不存在: {refined_srt}")

    if progress_cb:
        progress_cb("fine_clip_start", {"index": index, "total": 1})

    # 复用 long video 精剪入口（直接调 short_video_finalize.run_stage6_one；
    # 它已经实现 cut_by_srt 子进程调用 + 输出 final_NN.mp4 / continuous_NN.srt + isolation 校验）
    res = _sv_finalize.run_stage6_one(
        root, task_id, project_id, index,
    )
    final_mp4 = res.get("final_mp4")
    continuous_srt = res.get("continuous_srt")
    err = res.get("error")
    if err:
        if progress_cb:
            progress_cb("fine_clip_failed", {"index": index, "total": 1, "error": err})
        raise svc.ShortVideoError(err)
    if progress_cb:
        progress_cb("fine_clip_done", {"index": index, "total": 1,
                                        "mp4_path": str(final_mp4),
                                        "srt_path": str(continuous_srt)})
    return {
        "index": int(index),
        "final_mp4": str(final_mp4) if final_mp4 else "",
        "continuous_srt": str(continuous_srt) if continuous_srt else "",
    }


def run_fine_batch(repo_root: Path | str, task_id: str, project_id: str,
                   *, progress_cb=None) -> dict:
    """批量精剪：按 stage5_coarse.highlights 全部完成。"""
    root = Path(repo_root)
    project = svc.load_project(root, task_id, project_id)
    s5 = svc.get_stage_state(project, "stage5_coarse")
    hls = s5.get("highlights") or []
    total = len(hls)
    if not total:
        raise svc.ShortVideoError("没有粗剪 highlights，先完成 Stage 5")

    out = []
    for i, hl in enumerate(hls, 1):
        idx = int(hl.get("index") or i)
        if progress_cb:
            progress_cb("fine_clip_start", {"index": idx, "total": total,
                                             "stage": f"精剪 {i}/{total}",
                                             "progress": 5.0 + 90.0 * (i - 1) / total,
                                             "count": i - 1})
        try:
            res = run_fine_one(root, task_id, project_id, idx, progress_cb=None)
            out.append(res)
        except Exception as e:  # noqa: BLE001 — 单条失败不阻塞整批
            out.append({"index": idx, "error": str(e)[:200]})

    # 写 stage7_finalize 状态
    s7 = svc.get_stage_state(project, "stage7_finalize")
    s7["status"] = "done" if all("final_mp4" in r and r["final_mp4"] for r in out) else "partial"
    s7["highlights"] = out
    svc.set_stage_state(project, "stage7_finalize", s7)
    svc.save_project(root, project)
    return {"results": out, "count": len(out)}


# ---------- 预览：2-30s 快速切段 ----------

def preview_clip(repo_root: Path | str, task_id: str, project_id: str,
                 index: int, *, start_ms: int = 0, duration_sec: int = 5) -> dict:
    """切一段预览（ffmpeg -ss start -t duration -c copy），落 stage6/preview_NN.mp4。"""
    if not (2 <= int(duration_sec) <= 30):
        raise svc.ShortVideoError("preview 时长需在 2-30s 之间")
    root = Path(repo_root)
    project = svc.load_project(root, task_id, project_id)
    s7 = svc.get_stage_state(project, "stage7_finalize")
    hls = s7.get("highlights") or []
    hl = next((h for h in hls if int(h.get("index") or 0) == int(index)), None)
    if not hl or not hl.get("final_mp4"):
        # 没 final_mp4 → 用粗剪 mp4 兜底
        s5 = svc.get_stage_state(project, "stage5_coarse")
        chs = s5.get("highlights") or []
        ch = next((h for h in chs if int(h.get("index") or 0) == int(index)), None)
        if not ch or not ch.get("coarse_mp4"):
            raise svc.ShortVideoError(f"找不到 #{index} 的 final_mp4 或粗剪 mp4")
        src = (root / str(ch["coarse_mp4"])).resolve()
    else:
        src = Path(str(hl["final_mp4"]))
    if not src.is_file():
        raise svc.ShortVideoError(f"源视频不存在: {src}")
    out = svc.stage6_dir(root, task_id, project_id) / f"preview_{int(index):02d}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-ss", str(int(start_ms) / 1000.0),
        "-i", str(src), "-t", str(int(duration_sec)),
        "-c", "copy", "-avoid_negative_ts", "make_zero",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0 or not out.is_file() or out.stat().st_size < 100:
        raise svc.ShortVideoError(f"ffmpeg preview 失败: {proc.stderr[-300:]}")
    return {"preview_path": str(out), "size": out.stat().st_size,
            "duration_sec": int(duration_sec), "index": int(index)}


# ---------- 模板管理（短/长隔离：tasks/_global_short_video_fine_profiles.json） ----------

_SHORT_VIDEO_PROFILES_FILE = "_global_short_video_fine_profiles.json"

_DEFAULT_TEMPLATE: dict = {
    "params": DEFAULT_PARAMS,
}


def _profiles_path(repo_root: Path) -> Path:
    return Path(repo_root) / "tasks" / _SHORT_VIDEO_PROFILES_FILE


def _load_profiles(repo_root: Path) -> dict:
    p = _profiles_path(repo_root)
    if not p.is_file():
        return {"profiles": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "profiles" not in data:
            return {"profiles": []}
        return data
    except (OSError, json.JSONDecodeError):
        return {"profiles": []}


def _save_profiles(repo_root: Path, data: dict) -> None:
    p = _profiles_path(repo_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def _new_profile_id() -> str:
    return "sv-fine-prof-" + str(int(time.time() * 1000))[-8:]


def _unique_name(existing: list[dict], base: str) -> str:
    names = {p.get("name") for p in existing}
    if base not in names:
        return base
    i = 2
    while f"{base} ({i})" in names:
        i += 1
    return f"{base} ({i})"


def list_profiles(repo_root: Path | str) -> list[dict]:
    """返回所有短视频模板（不含 params，节省 IO）。"""
    return [
        {"id": p.get("id"), "name": p.get("name"), "updated_at": p.get("updated_at")}
        for p in _load_profiles(repo_root).get("profiles", [])
    ]


def get_profile(repo_root: Path | str, profile_id: str) -> dict | None:
    for p in _load_profiles(repo_root).get("profiles", []):
        if p.get("id") == profile_id:
            return p
    return None


def save_profile(repo_root: Path | str, *, name: str, params: dict,
                 profile_id: str | None = None) -> dict:
    """新增或更新模板。name 重复自动加 (2)/(3)…"""
    data = _load_profiles(repo_root)
    profiles = data.get("profiles", [])
    if profile_id:
        for p in profiles:
            if p.get("id") == profile_id:
                p["name"] = name
                p["params"] = params
                p["updated_at"] = _now_iso()
                _save_profiles(repo_root, data)
                return p
    # 新增
    new = {
        "id": profile_id or _new_profile_id(),
        "name": _unique_name(profiles, name),
        "params": params,
        "updated_at": _now_iso(),
    }
    profiles.append(new)
    data["profiles"] = profiles
    _save_profiles(repo_root, data)
    return new


def delete_profile(repo_root: Path | str, profile_id: str) -> bool:
    data = _load_profiles(repo_root)
    profiles = data.get("profiles", [])
    new_profiles = [p for p in profiles if p.get("id") != profile_id]
    if len(new_profiles) == len(profiles):
        return False
    data["profiles"] = new_profiles
    _save_profiles(repo_root, data)
    return True


def rename_profile(repo_root: Path | str, profile_id: str, new_name: str) -> dict | None:
    data = _load_profiles(repo_root)
    for p in data.get("profiles", []):
        if p.get("id") == profile_id:
            p["name"] = new_name
            p["updated_at"] = _now_iso()
            _save_profiles(repo_root, data)
            return p
    return None


def export_profile(repo_root: Path | str, profile_id: str) -> dict | None:
    p = get_profile(repo_root, profile_id)
    if not p:
        return None
    return {"name": p.get("name"), "params": p.get("params"),
            "exported_at": _now_iso()}


def apply_profile(repo_root: Path | str, task_id: str, project_id: str,
                  profile_id: str) -> dict:
    """套模板到项目（覆盖 params）。"""
    p = get_profile(repo_root, profile_id)
    if not p:
        raise svc.ShortVideoError(f"模板不存在: {profile_id}")
    return save_params(repo_root, task_id, project_id, p.get("params") or {})


def _now_iso() -> str:
    import datetime as _dt
    return _dt.datetime.fromtimestamp(time.time()).astimezone().isoformat(timespec="seconds")
