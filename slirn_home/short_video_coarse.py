"""短视频工作台 Stage 4（粗剪合成）执行模块。

REQ-20261003-098：第 4 阶段读 ``stage3/highlights.json`` + 源视频，
对每条 highlight 调 ``moviepy.editor.VideoFileClip.subclip(start, end)`` 导 mp4。
产物 ``stage4/coarse_NN.mp4`` + ``stage4/coarse_NN.srt``。

实现要点：

- MoviePy 的 ``video.audio.write_audiofile`` 在本机对部分短视频会触发 numpy 内存错误
  （参见 highlight_picker.py 的 bypass 路径）。本模块绕开它，直接用 ffmpeg 抽 wav + librosa
  不行（这里我们用 moviepy.subclip 即可，不需要 ffmpeg 抽 audio）。
- 每条 highlight 独立 try，一条失败不阻塞其他。
- MoviePy 加载视频到内存；源视频很大时内存占用高 —— 控制在源视频 ≤ 500 MB 时使用，
  否则建议后续扩展 ffmpeg 直接 subclip（避免全片 decode）。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from slirn_home import short_video_service as svc

log = logging.getLogger(__name__)


@dataclass
class CoarseClipResult:
    index: int
    coarse_mp4: Path
    coarse_srt: Path
    status: str        # done / failed
    duration_sec: float
    elapsed_sec: int
    error: str = ""


# ---------- REQ-20261013-103：异步粗剪（仿 compose_service.start_compose） ----------

# 内存 job 表：key = (task_id, project_id) 元组，区分不同项目
_JOBS: dict[tuple[str, str], dict] = {}
_JOBS_LOCK = threading.Lock()


def _job_key(task_id: str, project_id: str) -> tuple[str, str]:
    return (str(task_id or ""), str(project_id or ""))


def coarse_job_status(task_id: str, project_id: str) -> dict | None:
    """读内存 job 状态；服务重启后内存清空 → 返回 None（前端按 idle 处理）。"""
    with _JOBS_LOCK:
        j = _JOBS.get(_job_key(task_id, project_id))
        return dict(j) if j else None


def _set_job(task_id: str, project_id: str, **fields) -> None:
    """原子更新 _JOBS[(tid, pid)]；保留未传字段。"""
    with _JOBS_LOCK:
        key = _job_key(task_id, project_id)
        cur = _JOBS.get(key) or {"state": "idle", "progress": 0.0,
                                  "stage": "", "error": None,
                                  "started_at": 0.0, "finished_at": None,
                                  "count": 0, "total_count": 0}
        cur.update(fields)
        _JOBS[key] = cur


def start_coarse_async(
    repo_root: Path | str,
    task_id: str,
    project_id: str,
    *,
    start_ost_ms: int = 0,
    end_ost_ms: int = 100,
) -> bool:
    """启动异步粗剪线程。已在跑 → False。

    仿 ``compose_service.start_compose`` 模式：daemon 线程跑 run_stage4，
    通过 progress_cb 回调更新 _JOBS[(tid, pid)] 进度。
    """
    key = _job_key(task_id, project_id)
    with _JOBS_LOCK:
        existing = _JOBS.get(key)
        if existing and existing.get("state") == "running":
            return False
        _JOBS[key] = {
            "state": "running", "progress": 0.0, "stage": "准备",
            "error": None, "started_at": time.time(), "finished_at": None,
            "count": 0, "total_count": 0,
        }

    def _run():
        # 进度回调：每个 clip done 时更新 progress / count
        def _cb(event: str, payload: dict) -> None:
            if event == "stage4_start":
                _set_job(task_id, project_id,
                         total_count=int(payload.get("count") or 0),
                         stage="合成中", progress=5.0)
            elif event == "stage4_clip_done":
                done = _JOBS.get(key, {}).get("count", 0) + 1
                total = max(1, _JOBS.get(key, {}).get("total_count", 1))
                _set_job(task_id, project_id,
                         count=done,
                         progress=round(5.0 + 90.0 * done / total, 1),
                         stage=f"合成 {done}/{total}")
            elif event == "stage4_clip_failed":
                done = _JOBS.get(key, {}).get("count", 0) + 1
                _set_job(task_id, project_id, count=done)
            elif event in ("stage4_done", "stage4_failed"):
                _set_job(task_id, project_id,
                         state="done" if event == "stage4_done" else "failed",
                         progress=100.0 if event == "stage4_done" else _JOBS.get(key, {}).get("progress", 0.0),
                         stage="完成" if event == "stage4_done" else "失败",
                         finished_at=time.time())

        # 写执行历史（短视频自己的 stage5_dir；不写长视频 outputs）
        from slirn_home import execution_history
        stage5_dir = svc.stage5_dir(repo_root, task_id, project_id)
        exec_id = execution_history.record_start(
            stage5_dir,
            execution_history.KIND_SHORT_VIDEO_COARSE,
            extra={"start_ost_ms": start_ost_ms, "end_ost_ms": end_ost_ms},
        )
        try:
            run_stage4(repo_root, task_id, project_id,
                       start_ost_ms=start_ost_ms, end_ost_ms=end_ost_ms,
                       progress_cb=_cb)
            # run_stage4 内部已通过 _cb 把 state 推到 done/failed；这里取最终
            final = _JOBS.get(key, {})
            ok = final.get("state") == "done"
            execution_history.record_finish(
                stage5_dir, exec_id, success=ok,
                error="" if ok else str(final.get("error") or "粗剪合成未全部成功"),
            )
        except Exception as e:  # noqa: BLE001 — 后台线程兜底
            log.exception("[stage5_async][%s/%s] 失败: %s", task_id, project_id, e)
            _set_job(task_id, project_id,
                     state="failed", error=str(e),
                     finished_at=time.time())
            try:
                execution_history.record_finish(
                    stage5_dir, exec_id, success=False, error=str(e)[:500],
                )
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=_run, name=f"sv-stage5-{task_id}-{project_id}",
                     daemon=True).start()
    return True


def _write_coarse_srt(
    out_srt: Path,
    highlight: dict,
    *,
    segments: list[tuple[int, int]] | None = None,
) -> None:
    """把 highlight.subtitle_lines 写成 SRT 文本。

    - segments 不为 None：按 segments 顺序累加真实时长写时间戳（多窗拼接模式）。
      subtitle_lines[i] 与 segments[i] 一一对应。
    - segments 为 None：保留旧行为，时间戳全部 00:00:00,000 占位（Stage 5 重 ASR 后覆盖）。
    """
    lines = highlight.get("subtitle_lines") or []
    if not lines:
        out_srt.write_text("", encoding="utf-8")
        return
    out_lines: list[str] = []
    cumulative_ms = 0
    for i, sl in enumerate(lines, start=1):
        text = str(sl.get("text") or "").strip()
        if not text:
            continue
        if segments is not None and (i - 1) < len(segments):
            seg_start, seg_end = segments[i - 1]
            seg_dur = max(0, seg_end - seg_start)
            sub_start = cumulative_ms
            sub_end = cumulative_ms + seg_dur
            cumulative_ms += seg_dur
            start_s = _fmt_srt_time(sub_start)
            end_s = _fmt_srt_time(sub_end)
            out_lines.append(str(i))
            out_lines.append(f"{start_s} --> {end_s}")
        else:
            out_lines.append(str(i))
            out_lines.append(f"00:00:00,000 --> 00:00:00,000")
        out_lines.append(text)
        out_lines.append("")
    out_srt.write_text("\n".join(out_lines), encoding="utf-8")


def _fmt_srt_time(ms: int) -> str:
    """毫秒 → ``HH:MM:SS,mmm`` 格式。"""
    h, rem = divmod(max(0, ms), 3600 * 1000)
    m, rem = divmod(rem, 60 * 1000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


def _coarse_one(
    source_video: Path,
    highlight: dict,
    out_mp4: Path,
    out_srt: Path,
    *,
    start_ost_ms: int = 0,
    end_ost_ms: int = 100,
    segments: list[tuple[int, int]] | None = None,
) -> CoarseClipResult:
    """粗剪一条 highlight。

    segments 不为 None：按多段（毫秒）顺序切片 + 拼接；用于按 subtitle_lines 时间戳切。
    segments 为 None：保留旧行为，按 highlight 整体 [start_ms, end_ms] 一刀切。
    """
    index = max(1, int(highlight.get("index") or 0))
    out_mp4.parent.mkdir(parents=True, exist_ok=True)

    # 先写 srt（即使后面 mp4 失败也落地，方便排查）
    _write_coarse_srt(out_srt, highlight, segments=segments)

    # 决定 segments 实际用哪一组
    if segments is None:
        start_ms = max(0, int(highlight.get("start_ms") or 0)) + start_ost_ms
        end_ms = max(start_ms, int(highlight.get("end_ms") or 0)) + end_ost_ms
        segments = [(start_ms, end_ms)]

    t0 = time.time()
    try:
        import moviepy.editor as mpy
        video = mpy.VideoFileClip(str(source_video))
        try:
            # 把所有 segments 限制在 video.duration 内
            vid_dur = video.duration
            seg_list: list[tuple[float, float]] = []
            for s_ms, e_ms in segments:
                s_sec = max(0, s_ms) / 1000.0
                e_sec = min(vid_dur, e_ms) / 1000.0
                if e_sec <= s_sec:
                    continue
                seg_list.append((s_sec, e_sec))
            if not seg_list:
                raise ValueError("segments 为空或全部无效")

            clips = [video.subclip(s, e) for s, e in seg_list]
            try:
                if len(clips) == 1:
                    final = clips[0]
                else:
                    final = mpy.concatenate_videoclips(clips)
                try:
                    final.write_videofile(
                        str(out_mp4),
                        audio_codec="aac",
                        codec="libx264",
                        preset="medium",
                        verbose=False,
                        logger=None,
                    )
                finally:
                    final.close()
            finally:
                for c in clips:
                    try:
                        c.close()
                    except Exception:  # noqa: BLE001
                        pass
        finally:
            video.close()
    except Exception as e:  # noqa: BLE001
        return CoarseClipResult(
            index=index, coarse_mp4=out_mp4, coarse_srt=out_srt,
            status="failed", duration_sec=0.0,
            elapsed_sec=int(time.time() - t0),
            error=f"moviepy subclip/concat 失败: {e}",
        )

    elapsed = int(time.time() - t0)
    if not out_mp4.is_file() or out_mp4.stat().st_size < 1024:
        return CoarseClipResult(
            index=index, coarse_mp4=out_mp4, coarse_srt=out_srt,
            status="failed", duration_sec=0.0,
            elapsed_sec=elapsed,
            error="write_videofile 未产出有效 mp4",
        )

    # 读时长做记录（实际产出可能与 segments 累加有 PTS 漂移）
    duration_sec = sum(max(0, e - s) for s, e in segments) / 1000.0
    try:
        import subprocess
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(out_mp4)],
            capture_output=True, text=True, timeout=30, encoding="utf-8",
        )
        if probe.returncode == 0:
            duration_sec = max(0.0, float((probe.stdout or "").strip()))
    except Exception:  # noqa: BLE001
        pass

    return CoarseClipResult(
        index=index, coarse_mp4=out_mp4, coarse_srt=out_srt,
        status="done", duration_sec=duration_sec, elapsed_sec=elapsed,
    )


def run_stage4(
    repo_root: Path | str,
    task_id: str,
    project_id: str,
    *,
    start_ost_ms: int = 0,
    end_ost_ms: int = 100,
    progress_cb=None,
) -> dict:
    """Stage 5: 读 highlights.json + 源视频 → ``stage4/coarse_NN.{mp4,srt}``。

    REQ-20261013-103：增加 ``progress_cb(event, payload)`` 注入；默认 None
    保持向后兼容（不传回调走原行为）。回调事件：
    - ``stage4_start``: {"count": N}
    - ``stage4_clip_done``: {"index": idx, "count": N, "duration_sec": ...}
    - ``stage4_clip_failed``: {"index": idx, "error": ...}
    - ``stage4_done`` / ``stage4_failed``: {"count": N}

    返回最新 pipeline.stage5_coarse 状态。
    """
    """Stage 4: 读 highlights.json + 源视频 → ``stage4/coarse_NN.{mp4,srt}``。

    返回最新 pipeline.stage5_coarse 状态。
    """
    root = Path(repo_root)
    project = svc.load_project(root, task_id, project_id)

    src_id = project.get("base_material_id") or ""
    src = next(
        (m for m in project.get("materials") or []
         if m.get("id") == src_id and m.get("kind") == "video"),
        None,
    )
    if not src:
        raise svc.ShortVideoError("Stage 4 需要先在 Stage 1 选源视频")
    source_video = svc.material_abs_path(root, project, src["id"])

    highlights_path = svc.stage_highlights_path(root, task_id, project_id)
    if not highlights_path.is_file():
        raise svc.ShortVideoError("Stage 4 需要先跑 Stage 3 生成 highlights.json")
    try:
        hl_doc = json.loads(highlights_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise svc.ShortVideoError(f"highlights.json 解析失败: {e}")
    raw_highlights = hl_doc.get("highlights") or []
    if not raw_highlights:
        raise svc.ShortVideoError("highlights.json 没有有效片段")

    # 解析 raw.srt：subtitle_lines[].src_index → SRT 行 (start_ms, end_ms)
    # 用于按字幕行时间戳多窗拼接（REQ-20261004-stage3-restructure）。
    srt_path = svc.stage_raw_srt_path(root, task_id, project_id)
    srt_lines: list = []
    if srt_path.is_file():
        try:
            from slirn_home.short_video_analyze import _parse_srt_lines
            srt_lines = _parse_srt_lines(srt_path.read_text(encoding="utf-8"))
        except Exception as parse_err:  # noqa: BLE001
            log.warning("[stage4] raw.srt 解析失败: %s", parse_err)
    srt_index_to_line = {ln.index: ln for ln in srt_lines}

    def _resolve_segments(hl: dict) -> list[tuple[int, int]]:
        """按 subtitle_lines[].src_index 解析成 [(seg_start_ms, seg_end_ms), ...]。

        - 行内空 src_index / 找不到 / 时间无效 → 跳过
        - 完全无 segments → 返回 []，由 _coarse_one fallback 到整体区间切
        """
        out: list[tuple[int, int]] = []
        for sl in (hl.get("subtitle_lines") or []):
            if not isinstance(sl, dict):
                continue
            try:
                idx = int(sl.get("src_index") or 0)
            except (TypeError, ValueError):
                continue
            ln = srt_index_to_line.get(idx)
            if ln is None or ln.end_ms <= ln.start_ms:
                continue
            out.append((int(ln.start_ms), int(ln.end_ms)))
        return out

    state = svc.get_stage_state(project, "stage5_coarse")
    state["status"] = "running"
    state["started_at"] = svc.now_iso()
    state["count"] = len(raw_highlights)
    state["logs"] = list(state.get("logs") or []) + [
        f"[{svc.now_iso()}] stage4 start, {len(raw_highlights)} clips",
    ]
    svc.set_stage_state(project, "stage5_coarse", state)
    svc.save_project(root, project)

    if progress_cb:
        try:
            progress_cb("stage4_start", {"count": len(raw_highlights)})
        except Exception as cb_err:  # noqa: BLE001
            log.warning("progress_cb raised: %s", cb_err)

    results: list[CoarseClipResult] = []
    for hl in raw_highlights:
        idx = int(hl.get("index") or (len(results) + 1))
        out_mp4, out_srt = svc.stage4_paths(root, task_id, project_id, idx)
        segs = _resolve_segments(hl)
        # 优先按字幕行多窗拼接；无有效 segments 时 fallback 到整体区间切
        if segs:
            res = _coarse_one(
                source_video, hl, out_mp4, out_srt,
                start_ost_ms=start_ost_ms, end_ost_ms=end_ost_ms,
                segments=segs,
            )
        else:
            res = _coarse_one(
                source_video, hl, out_mp4, out_srt,
                start_ost_ms=start_ost_ms, end_ost_ms=end_ost_ms,
            )
        results.append(res)
        if progress_cb:
            try:
                progress_cb(
                    f"stage4_clip_{res.status}",
                    {"index": idx, "duration_sec": res.duration_sec, "error": res.error},
                )
            except Exception as cb_err:  # noqa: BLE001
                log.warning("progress_cb raised: %s", cb_err)

    any_failed = any(r.status != "done" for r in results)

    state = svc.get_stage_state(project, "stage5_coarse")
    state["status"] = "done" if not any_failed else "failed"
    state["finished_at"] = svc.now_iso()
    # REQ-20261013-103：增量字段 — 所有 done 片段时长累加；前端状态卡显示用
    state["total_duration_sec"] = round(
        sum(r.duration_sec for r in results if r.status == "done"), 3)
    state["highlights"] = [
        {
            "index": r.index,
            "coarse_mp4": svc._rel(root, r.coarse_mp4) if r.coarse_mp4 else "",
            "coarse_srt": svc._rel(root, r.coarse_srt) if r.coarse_srt else "",
            "status": r.status,
            "duration_sec": round(r.duration_sec, 3),
            "elapsed_sec": r.elapsed_sec,
            "error": r.error,
        }
        for r in results
    ]
    _log_lines = list(state.get("logs") or [])
    for r in results:
        _log_lines.append(
            f"[{svc.now_iso()}] clip #{r.index} {r.status} dur={r.duration_sec:.1f}s elapsed={r.elapsed_sec}s"
            + (f" err={r.error}" if r.error else "")
        )
    _log_lines.append(
        f"[{svc.now_iso()}] stage4 {'done' if not any_failed else 'failed'}, "
        f"{sum(1 for r in results if r.status == 'done')}/{len(results)} clips ok"
    )
    state["logs"] = _log_lines
    svc.set_stage_state(project, "stage5_coarse", state)
    svc.save_project(root, project)

    # Stage 4 改 → 下游 stage 5/6 全部作废
    if not any_failed:
        svc.reset_downstream_stages(project, "stage5_coarse")
        svc.save_project(root, project)

    if progress_cb:
        try:
            progress_cb(
                "stage4_done" if not any_failed else "stage4_failed",
                {"count": len(results)},
            )
        except Exception as cb_err:  # noqa: BLE001
            log.warning("progress_cb raised: %s", cb_err)

    return state


# ---------- REQ-20261013-103：粗剪成片删除 / 连续 SRT 预览 ----------

def delete_coarse(
    repo_root: Path | str,
    task_id: str,
    project_id: str,
    *,
    auto: bool = False,
    auto_session_id: str = "",
) -> dict:
    """删除 Stage 5 粗剪成片（N 条 mp4 + N 条占位 srt）。仿 compose_rough_delete。

    同步、毫秒级；写执行历史 KIND_SHORT_VIDEO_COARSE_DELETE；
    作废下游 stage 6/7（reset_downstream_stages）。
    """
    from slirn_home import execution_history

    root = Path(repo_root)
    project = svc.load_project(root, task_id, project_id)
    stage4 = svc.stage4_dir(root, task_id, project_id)
    stage5 = svc.stage5_dir(root, task_id, project_id)

    removed: list[str] = []
    # 删 stage4 目录下所有 coarse_NN.{mp4,srt}（不删 stage4 目录本身）
    if stage4.is_dir():
        for p in sorted(stage4.iterdir()):
            if p.is_file() and (p.suffix.lower() in (".mp4", ".srt")) and p.name.startswith("coarse_"):
                try:
                    p.unlink()
                    removed.append(svc._rel(root, p))
                except OSError as e:
                    log.warning("[stage5][delete] %s 失败: %s", p, e)
    # 删 stage5/preview_subs.srt 缓存
    preview = stage5 / "preview_subs.srt"
    if preview.is_file():
        try:
            preview.unlink()
            removed.append(svc._rel(root, preview))
        except OSError:
            pass

    # 写执行历史（短视频自己的 stage5_dir）
    try:
        eid = execution_history.record_start(
            stage5,
            execution_history.KIND_SHORT_VIDEO_COARSE_DELETE,
            extra={"removed": removed},
            auto=auto,
            auto_session_id=auto_session_id,
        )
        if removed:
            execution_history.patch_fields(stage5, eid, {
                "description": f"删除粗剪成品：{' + '.join(removed)}",
            })
        else:
            execution_history.patch_fields(stage5, eid, {
                "description": "粗剪成片不存在，无需删除（无操作）",
            })
        execution_history.record_finish(stage5, eid, success=True, error="")
    except Exception as e:  # noqa: BLE001
        log.warning("[stage5][history] delete 记录失败: %s", e)

    # 重置 stage5_coarse 状态（status=pending）+ 作废下游
    state = svc.get_stage_state(project, "stage5_coarse")
    state["status"] = "pending"
    state["highlights"] = []
    state["count"] = 0
    state["total_duration_sec"] = 0.0
    state.pop("finished_at", None)
    state["logs"] = list(state.get("logs") or []) + [
        f"[{svc.now_iso()}] stage5 coarse deleted, {len(removed)} files removed",
    ]
    svc.set_stage_state(project, "stage5_coarse", state)
    svc.reset_downstream_stages(project, "stage5_coarse")
    svc.save_project(root, project)

    if removed:
        return {"deleted": True, "removed": removed,
                "remaining": [], "message": f"已删除 {' + '.join(removed)}"}
    return {"deleted": False, "removed": [], "remaining": [],
            "message": "粗剪成片不存在，无需删除"}


# 连续 SRT 缓存文件名（落 stage5/preview_subs.srt，避免每次都现拼）
_PREVIEW_SUBS_FILENAME = "preview_subs.srt"


def preview_continuous_subs(
    repo_root: Path | str,
    task_id: str,
    project_id: str,
) -> dict:
    """跨 N 条 coarse_NN.srt 拼成连续时间轴 SRT（仿 compose_rough_preview_subs）。

    返回 ``{"srt": "....", "clips": N, "cached": bool}``。
    缓存到 ``stage5/preview_subs.srt``，第二次取直接读（除非 highlights 变化）。
    """
    from slirn_home.compose_service import format_srt

    root = Path(repo_root)
    project = svc.load_project(root, task_id, project_id)
    stage4 = svc.stage4_dir(root, task_id, project_id)
    stage5 = svc.stage5_dir(root, task_id, project_id)
    state = svc.get_stage_state(project, "stage5_coarse")
    highlights = state.get("highlights") or []
    if not highlights:
        return {"srt": "", "clips": 0, "cached": False}

    # 取缓存（与 state.highlights 数量一致即直接返回）
    preview = stage5 / _PREVIEW_SUBS_FILENAME
    expected_count = len(highlights)
    if preview.is_file():
        try:
            cached = preview.read_text(encoding="utf-8", errors="replace")
            # 简单启发：缓存内容含 expected_count 个 "coarse_"
            if cached.count("-->") == expected_count:
                return {"srt": cached, "clips": expected_count, "cached": True}
        except Exception:  # noqa: BLE001
            pass

    # 现拼：把每条 coarse_NN.srt 当作一个 unit（start_ms = 累加偏移；end_ms = 累加+该条时长）
    from slirn_home.short_video_finalize import _ffprobe_duration
    units: list[dict] = []
    offset_ms = 0
    for hl in highlights:
        rel_mp4 = hl.get("coarse_mp4") or ""
        if not rel_mp4:
            continue
        mp4 = (root / rel_mp4).resolve()
        if not mp4.is_file():
            continue
        dur = _ffprobe_duration(mp4)
        units.append({
            "id": str(hl.get("index")),
            "start_ms": offset_ms,
            "end_ms": offset_ms + int(round(dur * 1000)),
            "text": f"clip_{hl.get('index')}",
        })
        offset_ms += int(round(dur * 1000))

    if not units:
        return {"srt": "", "clips": 0, "cached": False}

    srt_text = format_srt(units, text_overrides=None)
    # 落缓存
    try:
        stage5.mkdir(parents=True, exist_ok=True)
        preview.write_text(srt_text, encoding="utf-8")
    except OSError as e:
        log.warning("[stage5][preview] 缓存失败: %s", e)

    return {"srt": srt_text, "clips": len(units), "cached": False}


# ---------- REQ-20261012-099 修订六：Stage 3 短视频生成（逐组截取拼接） ----------

def _render_splice_video(
    source_video: Path,
    seg_list: list[tuple[float, float]],
    out_mp4: Path,
) -> None:
    """moviepy 按 seg_list（秒）切片 + 顺序拼接（与 _coarse_one 同一套写法）。

    独立成函数便于测试注入（moviepy 需要真实视频文件）。
    """
    import moviepy.editor as mpy
    video = mpy.VideoFileClip(str(source_video))
    try:
        clips = [video.subclip(s, e) for s, e in seg_list]
        try:
            final = clips[0] if len(clips) == 1 else mpy.concatenate_videoclips(clips)
            try:
                final.write_videofile(
                    str(out_mp4),
                    audio_codec="aac", codec="libx264", preset="medium",
                    verbose=False, logger=None,
                )
            finally:
                final.close()
        finally:
            for c in clips:
                try:
                    c.close()
                except Exception:  # noqa: BLE001
                    pass
    finally:
        video.close()


def _hl_line_window(
    sl: dict,
    srt_lookup: dict[int, object],
) -> tuple[int, int] | None:
    """字幕行 → 切割窗（毫秒）。优先行自带时间戳，缺失时查 raw.srt 源行。"""
    try:
        s = int(sl.get("start_ms") or 0)
        e = int(sl.get("end_ms") or 0)
    except (TypeError, ValueError):
        s = e = 0
    if e <= s:
        try:
            ln = srt_lookup.get(int(sl.get("src_index") or 0))
        except (TypeError, ValueError):
            ln = None
        if ln is None:
            return None
        s, e = ln.start_ms, ln.end_ms
    return (max(0, s), max(0, e))


def run_stage3_splice(
    repo_root: Path | str,
    task_id: str,
    project_id: str,
    *,
    start_ost_ms: int = 0,
    end_ost_ms: int = 100,
) -> dict:
    """Stage 3 修订六：每组（一条短视频）按其字幕行时间戳逐行截取并拼接。

    - 解析产出的 N 组 = N 条独立短视频（不是合成一条）：每组按**当前
      subtitle_lines 顺序**逐行切割源视频并拼接 → ``stage3/clip_<index>.mp4``
      + ``clip_<index>.srt``（组内字幕重新编号、时间轴重定位到拼接后）
    - end_ost 默认 +100ms 防切词尾（与 Stage 4 默认一致）
    - 状态落 ``pipeline.stage3_splice``：``clips`` 列出每组的产物与时长
    """
    root = Path(repo_root)
    project = svc.load_project(root, task_id, project_id)

    src_id = project.get("base_material_id") or ""
    src = next(
        (m for m in project.get("materials") or []
         if m.get("id") == src_id and m.get("kind") == "video"),
        None,
    )
    if not src:
        raise svc.ShortVideoError("拼接需要先在 Stage 1 选源视频")
    source_video = svc.material_abs_path(root, project, src["id"])

    hp = svc.stage_highlights_path(root, task_id, project_id)
    if not hp.is_file():
        raise svc.ShortVideoError("拼接需要先运行 Stage 3 解析生成短视频列表")
    try:
        doc = json.loads(hp.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise svc.ShortVideoError(f"highlights.json 解析失败: {e}") from e
    hls = sorted(
        (h for h in doc.get("highlights") or []
         if int(h.get("index") or 0) > 0),
        key=lambda h: int(h.get("index") or 0),
    )
    if not hls:
        raise svc.ShortVideoError("highlights.json 没有有效短视频分组")

    state = svc.get_stage_state(project, "stage3_splice")
    state.update({
        "status": "running",
        "started_at": svc.now_iso(),
        "count": len(hls),
        "logs": list(state.get("logs") or []) + [
            f"[{svc.now_iso()}] splice start, groups={len(hls)}",
        ],
    })
    state.pop("stale_reason", None)
    state.pop("error", None)
    svc.set_stage_state(project, "stage3_splice", state)
    svc.save_project(root, project)

    stage_dir = svc.stage3_dir(root, task_id, project_id)
    stage_dir.mkdir(parents=True, exist_ok=True)
    # 清理旧产物（修订五合成一条的 splice.* + 上一轮可能不同组数的 clip_*）
    for old in list(stage_dir.glob("splice.*")) + list(stage_dir.glob("clip_*")):
        try:
            old.unlink()
        except OSError:
            pass

    # 源字幕查表（旧数据/JSON 兜底的行没有自带时间戳时用）
    srt_lookup: dict[int, object] = {}
    srt_path = svc.stage_raw_srt_path(root, task_id, project_id)
    if srt_path.is_file():
        try:
            from slirn_home.short_video_analyze import _parse_srt_lines
            srt_lookup = {
                ln.index: ln
                for ln in _parse_srt_lines(srt_path.read_text(encoding="utf-8"))
            }
        except Exception as parse_err:  # noqa: BLE001
            log.warning("[stage3-splice] raw.srt 解析失败: %s", parse_err)

    # 视频实际时长用于夹紧切割窗（毫秒级时间戳可能略超片尾）
    vid_dur = 0.0
    try:
        import subprocess
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(source_video)],
            capture_output=True, text=True, timeout=30, encoding="utf-8",
        )
        if probe.returncode == 0:
            vid_dur = max(0.0, float((probe.stdout or "").strip()))
    except Exception:  # noqa: BLE001
        pass

    clips_meta: list[dict] = []
    t0 = time.time()
    try:
        for h in hls:
            idx = int(h.get("index") or 0)
            title = str(h.get("title") or f"短视频 {idx}")
            # (窗起, 窗止, 该行字幕文本) —— 按当前 subtitle_lines 顺序
            windows: list[tuple[int, int, str]] = []
            for sl in h.get("subtitle_lines") or []:
                win = _hl_line_window(sl, srt_lookup)
                if win is None:
                    continue
                windows.append((win[0] + start_ost_ms, win[1] + end_ost_ms,
                                str(sl.get("text") or "")))
            if not windows:
                log.warning("[stage3-splice] 组 #%s 没有可切割的字幕行，跳过", idx)
                continue

            # 组内拼接 SRT：重新编号 + 时间轴重定位到拼接后的连续时间轴
            blocks: list[str] = []
            acc_ms = 0
            for w_start, w_end, text in windows:
                span = max(0, w_end - w_start)
                blocks.append(
                    f"{len(blocks) + 1}\n"
                    f"{_fmt_srt_time(acc_ms)} --> {_fmt_srt_time(acc_ms + span)}\n"
                    f"{text}\n"
                )
                acc_ms += span

            seg_list: list[tuple[float, float]] = []
            for w_start, w_end, _text in windows:
                s_sec = max(0.0, w_start / 1000.0)
                e_sec = w_end / 1000.0
                if vid_dur > 0:
                    e_sec = min(vid_dur, e_sec)
                if e_sec <= s_sec:
                    continue
                seg_list.append((s_sec, e_sec))
            if not seg_list:
                raise ValueError(f"组 #{idx} 没有有效的切割时间段")
            out_mp4 = stage_dir / f"clip_{idx}.mp4"
            _render_splice_video(source_video, seg_list, out_mp4)
            if not out_mp4.is_file() or out_mp4.stat().st_size < 1024:
                raise ValueError(f"组 #{idx} 未产出有效 mp4")
            total_sec = sum(max(0.0, e - s) for s, e in seg_list)
            out_srt = stage_dir / f"clip_{idx}.srt"
            out_srt.write_text("\n".join(blocks), encoding="utf-8")
            clips_meta.append({
                "index": idx,
                "title": title,
                "mp4": out_mp4.name,
                "srt": out_srt.name,
                "total_sec": round(total_sec, 1),
            })
    except Exception as e:  # noqa: BLE001
        log.exception("stage3 splice failed")
        project = svc.load_project(root, task_id, project_id)
        state = svc.get_stage_state(project, "stage3_splice")
        state.update({
            "status": "failed",
            "finished_at": svc.now_iso(),
            "error": f"拼接失败: {e}",
            "logs": list(state.get("logs") or []) + [
                f"[{svc.now_iso()}] splice failed: {e}",
            ],
        })
        svc.set_stage_state(project, "stage3_splice", state)
        svc.save_project(root, project)
        return state

    elapsed = int(time.time() - t0)
    if not clips_meta:
        project = svc.load_project(root, task_id, project_id)
        state = svc.get_stage_state(project, "stage3_splice")
        state.update({
            "status": "failed",
            "finished_at": svc.now_iso(),
            "error": "没有任何分组产出有效短视频",
        })
        svc.set_stage_state(project, "stage3_splice", state)
        svc.save_project(root, project)
        return state

    project = svc.load_project(root, task_id, project_id)
    state = svc.get_stage_state(project, "stage3_splice")
    state.update({
        "status": "done",
        "finished_at": svc.now_iso(),
        "elapsed_sec": elapsed,
        "count": len(clips_meta),
        "clips": clips_meta,
        "total_sec": round(sum(c["total_sec"] for c in clips_meta), 1),
        "logs": list(state.get("logs") or []) + [
            f"[{svc.now_iso()}] splice done, clips={len(clips_meta)}, elapsed={elapsed}s",
        ],
    })
    state.pop("error", None)
    svc.set_stage_state(project, "stage3_splice", state)
    svc.save_project(root, project)
    return state


def _cli() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Stage 4: coarse cut")
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--start-ost", type=int, default=0)
    ap.add_argument("--end-ost", type=int, default=100)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    state = run_stage4(
        args.repo_root, args.task_id, args.project_id,
        start_ost_ms=args.start_ost, end_ost_ms=args.end_ost,
    )
    print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0 if state.get("status") == "done" else 1


if __name__ == "__main__":
    raise SystemExit(_cli())