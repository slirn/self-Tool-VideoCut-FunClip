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


def _write_coarse_srt(
    out_srt: Path,
    highlight: dict,
) -> None:
    """把 highlight.subtitle_lines 写成 SRT 文本（按 src_index 引用 raw.srt 行）。"""
    lines = highlight.get("subtitle_lines") or []
    if not lines:
        out_srt.write_text("", encoding="utf-8")
        return
    out_lines: list[str] = []
    for i, sl in enumerate(lines, start=1):
        text = str(sl.get("text") or "").strip()
        if not text:
            continue
        out_lines.append(str(i))
        # 用 src_index 标记占位时间戳 —— Stage 5 重新 ASR 后会用真实时间
        out_lines.append(f"00:00:00,000 --> 00:00:00,000")
        out_lines.append(text)
        out_lines.append("")
    out_srt.write_text("\n".join(out_lines), encoding="utf-8")


def _coarse_one(
    source_video: Path,
    highlight: dict,
    out_mp4: Path,
    out_srt: Path,
    *,
    start_ost_ms: int = 0,
    end_ost_ms: int = 100,
) -> CoarseClipResult:
    index = max(1, int(highlight.get("index") or 0))
    start_ms = max(0, int(highlight.get("start_ms") or 0)) + start_ost_ms
    end_ms = max(start_ms, int(highlight.get("end_ms") or 0)) + end_ost_ms
    start_sec = start_ms / 1000.0
    end_sec = end_ms / 1000.0

    out_mp4.parent.mkdir(parents=True, exist_ok=True)

    # 先写 srt（即使后面 mp4 失败也落地，方便排查）
    _write_coarse_srt(out_srt, highlight)

    t0 = time.time()
    try:
        import moviepy.editor as mpy
        video = mpy.VideoFileClip(str(source_video))
        try:
            if end_sec > video.duration:
                end_sec = video.duration
            if start_sec >= end_sec:
                raise ValueError(f"start_sec={start_sec} >= end_sec={end_sec}")
            sub = video.subclip(start_sec, end_sec)
            try:
                sub.write_videofile(
                    str(out_mp4),
                    audio_codec="aac",
                    codec="libx264",
                    preset="medium",
                    verbose=False,
                    logger=None,
                )
            finally:
                sub.close()
        finally:
            video.close()
    except Exception as e:  # noqa: BLE001
        return CoarseClipResult(
            index=index, coarse_mp4=out_mp4, coarse_srt=out_srt,
            status="failed", duration_sec=0.0,
            elapsed_sec=int(time.time() - t0),
            error=f"moviepy subclip 失败: {e}",
        )

    elapsed = int(time.time() - t0)
    if not out_mp4.is_file() or out_mp4.stat().st_size < 1024:
        return CoarseClipResult(
            index=index, coarse_mp4=out_mp4, coarse_srt=out_srt,
            status="failed", duration_sec=0.0,
            elapsed_sec=elapsed,
            error="write_videofile 未产出有效 mp4",
        )

    # 读时长做记录
    duration_sec = end_sec - start_sec
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
    """Stage 4: 读 highlights.json + 源视频 → ``stage4/coarse_NN.{mp4,srt}``。

    返回最新 pipeline.stage4_coarse 状态。
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

    state = svc.get_stage_state(project, "stage4_coarse")
    state["status"] = "running"
    state["started_at"] = svc.now_iso()
    state["count"] = len(raw_highlights)
    state["logs"] = list(state.get("logs") or []) + [
        f"[{svc.now_iso()}] stage4 start, {len(raw_highlights)} clips",
    ]
    svc.set_stage_state(project, "stage4_coarse", state)
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

    state = svc.get_stage_state(project, "stage4_coarse")
    state["status"] = "done" if not any_failed else "failed"
    state["finished_at"] = svc.now_iso()
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
    svc.set_stage_state(project, "stage4_coarse", state)
    svc.save_project(root, project)

    # Stage 4 改 → 下游 stage 5/6 全部作废
    if not any_failed:
        svc.reset_downstream_stages(project, "stage4_coarse")
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