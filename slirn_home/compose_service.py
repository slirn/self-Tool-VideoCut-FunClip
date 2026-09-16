"""粗剪合成服务 — 按切分修剪执行口径把保留区间合成一版粗剪视频（REQ-20260916-016）。

定位：快速预览切分后的整体效果（可选步骤）。边界按字幕级精度重编码合成
（concat demuxer + inpoint/outpoint + libx264），实测边界偏差 ~1ms/段；
曾测 `-c copy` 快速路线：长 GOP（实测中位 14.4s）下拼接处 DTS 非单调、
播放异常，弃用。44 分钟源 / 997 区间实测约 6 分钟 — 后台线程 + 进度轮询。
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

ROUGH_COMPOSE_NAME = "rough_compose.mp4"
_FFMPEG_TIMEOUT = 1800  # 30 分钟（超长视频兜底；44min/997 段实测 ~6min）


def rough_compose_path(outputs_dir: Path) -> Path:
    """粗剪成片固定产物路径 outputs/rough_compose.mp4。"""
    return Path(outputs_dir) / ROUGH_COMPOSE_NAME


def merge_intervals(intervals: list[tuple[float, float]]) -> list[list[float]]:
    """升序合并相邻区间（缝隙 <1ms 视为连续 — 保留单元接缝处不产生无谓切点）。"""
    merged: list[list[float]] = []
    for s, e in sorted(intervals):
        if merged and s - merged[-1][1] < 0.001:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged


def _ffconcat_escape(path: str) -> str:
    """ffconcat file 行单引号转义。"""
    return "'" + path.replace("'", "'\\''") + "'"


def compose_rough_cut(
    src: Path,
    intervals: list[tuple[float, float]],
    dst: Path,
    on_progress=None,
) -> dict:
    """把 src 的保留区间按序拼接合成 dst（重编码，帧级边界）。

    Args:
        src: 源视频（任务工作视频 — 截取段或原视频，时间轴与切分清单一致）
        intervals: [(start_s, end_s), …] 秒，升序
        dst: 输出路径（先写 .part 临时文件，成功后原子改名）
        on_progress: 可选回调 (pct: 0-100, out_sec: float)

    Returns:
        {"duration": 秒, "size_mb": MB, "segments": 区间数, "elapsed": 秒}

    Raises:
        RuntimeError: ffmpeg 失败 / 超时 / 输出缺失
    """
    if not src.exists():
        raise RuntimeError(f"源视频不存在: {src}")
    merged = merge_intervals(intervals)
    if not merged:
        raise RuntimeError("没有保留区间 — 全部内容都被删除时无需合成")
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.stem + ".part.mp4")
    lst = dst.with_name(dst.stem + ".list.ffconcat")
    with open(lst, "w", encoding="utf-8") as f:
        f.write("ffconcat version 1.0\n")
        esc = _ffconcat_escape(src.resolve().as_posix())
        for s, e in merged:
            f.write(f"file {esc}\ninpoint {s:.3f}\noutpoint {e:.3f}\n")
    total = sum(e - s for s, e in merged)
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(lst),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        "-v", "error", "-nostats", "-progress", "pipe:1",
        str(part),
    ]
    t0 = time.time()
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError as e:
        lst.unlink(missing_ok=True)
        raise RuntimeError("ffmpeg 不在 PATH 中，无法合成") from e
    last_tick = 0.0
    for line in proc.stdout or []:
        line = line.strip()
        if not line.startswith("out_time_us="):
            continue
        try:
            out_s = int(line.split("=", 1)[1]) / 1_000_000
        except ValueError:
            continue
        now = time.time()
        if on_progress and now - last_tick >= 0.5:  # 节流：进度回调 ≤2Hz
            last_tick = now
            on_progress(min(99.0, out_s / total * 100) if total > 0 else 0.0, out_s)
    try:
        proc.wait(timeout=_FFMPEG_TIMEOUT - (time.time() - t0))
    except subprocess.TimeoutExpired:
        proc.kill()
        lst.unlink(missing_ok=True)
        part.unlink(missing_ok=True)
        raise RuntimeError("合成超时（超过 30 分钟）")
    err = (proc.stderr.read() if proc.stderr else "") or ""
    lst.unlink(missing_ok=True)
    if proc.returncode != 0 or not part.exists():
        part.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg 合成失败: {err[-400:]}")
    os.replace(part, dst)
    dur = _probe_duration(dst)
    return {
        "duration": dur,
        "size_mb": round(dst.stat().st_size / 1024 / 1024, 1),
        "segments": len(merged),
        "elapsed": round(time.time() - t0, 1),
    }


def _probe_duration(p: Path) -> float | None:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(p)],
            capture_output=True, text=True, timeout=30,
        )
        return float(r.stdout.strip()) if r.returncode == 0 else None
    except (ValueError, subprocess.TimeoutExpired):
        return None


# =============== 后台 job 管理（对齐 asr_service 范式） ===============

# task_id → {"state": "running|done|error", "progress": 0-100, "error": str|None,
#            "started_at": float, "finished_at": float|None, "result": dict|None}
_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()


def job_status(task_id: str) -> dict | None:
    with _JOBS_LOCK:
        j = _JOBS.get(task_id)
        return dict(j) if j else None


def start_compose(task_id: str, video_path: Path, intervals: list[tuple[float, float]], dst: Path) -> bool:
    """启动合成线程。已在跑 → 返回 False（不重复起）。"""
    with _JOBS_LOCK:
        existing = _JOBS.get(task_id)
        if existing and existing.get("state") == "running":
            return False
        _JOBS[task_id] = {
            "state": "running", "progress": 0.0, "error": None,
            "started_at": time.time(), "finished_at": None, "result": None,
        }

    def _run():
        job = _JOBS[task_id]

        def _tick(pct: float, _out: float) -> None:
            job["progress"] = round(pct, 1)

        try:
            result = compose_rough_cut(video_path, intervals, dst, on_progress=_tick)
            job["result"] = result
            job["progress"] = 100.0
            job["state"] = "done"
            job["finished_at"] = time.time()
            log.info("[compose][%s] 完成：%s 段 → %s", task_id, result["segments"], dst.name)
        except Exception as e:  # noqa: BLE001 — 后台线程必须全兜底
            job["state"] = "error"
            job["error"] = str(e)
            job["finished_at"] = time.time()
            log.exception("[compose][%s] 合成失败", task_id)

    threading.Thread(target=_run, name=f"rough-compose-{task_id}", daemon=True).start()
    return True
