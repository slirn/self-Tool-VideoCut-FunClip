"""粗剪合成服务 — 复用上游 VideoClipper 合成方法拼接保留区间（REQ-20260916-018）。

REQ-20260916-016 的自研 ffmpeg concat 重编码路线被弃用，本文件改为复刻
上游主项目首页合成页的调用过程（funclip/launch.py「AI 智能裁剪」按钮 →
funclip/videoclipper.py VideoClipper.video_clip 的 timestamp_list 分支）：

    clipper = VideoClipper(None)   # clip 阶段不加载 ASR 模型（上游 CLI 同款）
    state = {'sentences': 保留行, 'video': VideoFileClip(src),
             'clip_video_file': dst, 'video_filename': src, ...}
    out, message, clip_srt = clipper.video_clip(
        '', 0, 0, state, add_sub=False, timestamp_list=保留区间毫秒)

对接说明：
- timestamp_list 单位毫秒（上游 extract_timestamps 同口径）— 切分清单即毫秒，直传
- sentences = 整行单 token（{'text', 'timestamp': [[行首ms, 行尾ms]]}）；
  行与区间边界同源（区间=行区间的并集），generate_srt_clip 整行进/整行出
- 上游输出固定带 _no{GLOBAL_COUNT} 后缀 → 按返回值 rename 成标准产物名
- clip_srt（处理之后的字幕，时间轴随片平移）落 rough_compose.srt 副产物
- 进度：合成期间临时包一层 VideoClip.write_videofile 注入 proglog logger
  （视频帧 t/total）；全程持 _COMPOSE_LOCK（GLOBAL_COUNT 与 patch 均非并发安全）
- 实测（44min 源 / 997 区间）：编码约 80 fps，全程约 11-12 分钟
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import proglog  # moviepy 1.0.3 自带依赖（帧进度协议）

log = logging.getLogger(__name__)

ROUGH_COMPOSE_NAME = "rough_compose.mp4"

# 跨任务串行：VideoClipper.GLOBAL_COUNT（类属性自增）与 write_videofile 补丁
# 都不是并发安全的，两任务同时合成会互踩输出文件名
_COMPOSE_LOCK = threading.Lock()

_UPSTREAM_CLS = None  # VideoClipper 类缓存（sys.path 注入 + import 只做一次）


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


def merge_intervals_ms(intervals_ms: list[tuple[int, int]]) -> list[list[int]]:
    """毫秒整数版本的 merge_intervals — 传给上游合成时无浮点精度损失。"""
    merged: list[list[int]] = []
    for s, e in sorted(intervals_ms):
        if merged and s - merged[-1][1] < 1:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged


def build_sentences(lines: list[dict]) -> list[dict]:
    """保留行 → 上游 sentences 格式（整行单 token，timestamp 毫秒）。

    generate_srt_clip 会自行 str2list(text)；行边界与区间边界同源，
    token 级裁剪分支（句子被区间从中切断）不会触发。
    """
    return [
        {"text": str(ln.get("text", "")),
         "timestamp": [[int(ln["start_ms"]), int(ln["end_ms"])]]}
        for ln in lines
    ]


def _load_upstream_clipper():
    """加载上游 VideoClipper（funclip/ 内部是平级 import，需注入 sys.path）。"""
    global _UPSTREAM_CLS
    if _UPSTREAM_CLS is not None:
        return _UPSTREAM_CLS
    funclip_dir = Path(__file__).resolve().parent.parent / "funclip"
    if not funclip_dir.is_dir():
        raise RuntimeError(f"上游 funclip/ 目录不存在: {funclip_dir}")
    if str(funclip_dir) not in sys.path:
        sys.path.insert(0, str(funclip_dir))
    from videoclipper import VideoClipper  # noqa: E402 — 上游平级 import 风格

    _UPSTREAM_CLS = VideoClipper
    return VideoClipper


class _FrameProgressLogger(proglog.ProgressBarLogger):
    """moviepy 帧进度 → on_frame(pct 0-100)；不打印进度条（后台服务避免刷屏）。

    proglog 协议：'bar__total'/'bar__index' 经 __call__ 转入 bars 结构后回调
    bars_callback(bar, attr, value, old_value)（moviepy 的视频帧 bar 名是 t、
    音频 chunk bar 名是 chunk — 两者都算编码进度）。
    """

    def __init__(self, on_frame: Callable[[float], None]):
        super().__init__(min_time_interval=0.4)
        self._on_frame = on_frame

    def bars_callback(self, bar, attr, value, old_value) -> None:  # noqa: ARG002
        if attr != "index" or not self._on_frame:
            return
        total = (self.bars.get(bar) or {}).get("total")
        if isinstance(total, (int, float)) and total > 0:
            self._on_frame(min(100.0, float(value) / float(total) * 100.0))


def compose_rough_cut(
    src: Path,
    intervals_ms: list[tuple[int, int]],
    dst: Path,
    lines: list[dict],
    on_progress=None,
) -> dict:
    """用上游 VideoClipper.video_clip 把保留区间按序拼接合成 dst。

    Args:
        src: 源视频（任务工作视频 — 截取段或原视频，时间轴与切分清单一致）
        intervals_ms: [(start_ms, end_ms), …] 整数毫秒，升序（内部**合并相邻**——
            无缝拼接的行会被并成一段；上游 generate_srt_clip 在 CASE2 路径遇到
            浮点精度差导致空 token 时崩溃，整数毫秒直传可避免）
        dst: 输出路径（上游先写带 _no{N} 后缀的同名文件，成功后 rename）
        lines: 保留行 [{"id","start_ms","end_ms","text"}…] — 最新确认文本
            （含热词替换未撤销行的 new_text，由调用端合并）
        on_progress: 可选回调 (pct: 0-100)（视频编码帧进度）

    Returns:
        {"duration": 秒, "size_mb": MB, "segments": 合并后区间数, "raw_segments": 原数, "elapsed": 秒}
    """
    if not src.exists():
        raise RuntimeError(f"源视频不存在: {src}")
    if not intervals_ms:
        raise RuntimeError("没有保留区间 — 全部内容都被删除时无需合成")
    if not lines:
        raise RuntimeError("缺少保留行（字幕）— 上游合成方法按行生成随片字幕")

    VideoClipper = _load_upstream_clipper()
    import moviepy.editor as mpy
    from moviepy.video.VideoClip import VideoClip

    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    # 合并相邻区间：相邻 <1ms 视为连续（毫秒整数比较）
    merged_ms = merge_intervals_ms(intervals_ms)
    # upstream timestamp_list 单位 = 毫秒（funclip/videoclipper.py: start = ts/16000 秒）
    # 给 end 加 1ms 容差规避 upstream int(end*1000) 浮点精度丢 1ms 触发的
    # CASE2 空 _ts 崩溃（funclip/utils/subtitle_utils.py:91-110 路径）
    ts_list = [[s, e + 1] for s, e in merged_ms]
    raw_n = len(intervals_ms)

    total_keep_ms = sum(e - s for s, e in merged_ms)
    last_tick = 0.0

    def _on_frame(pct: float) -> None:
        nonlocal last_tick
        if not on_progress:
            return
        now = time.time()
        if now - last_tick >= 0.5:  # 节流 ≤2Hz
            last_tick = now
            on_progress(5.0 + pct * 0.94)  # 预热 5% + 编码映射到 5-99

    t0 = time.time()
    if on_progress:
        on_progress(2.0)
    # ---- 临时给 VideoClip.write_videofile 注入进度 logger（锁内安全） ----
    _orig_wvf = VideoClip.write_videofile
    frame_logger = _FrameProgressLogger(_on_frame)

    def _wvf(self, filename, *a, **kw):
        kw.setdefault("logger", frame_logger)
        return _orig_wvf(self, filename, *a, **kw)

    video = None
    with _COMPOSE_LOCK:
        try:
            VideoClip.write_videofile = _wvf  # type: ignore[method-assign]
            if on_progress:
                on_progress(5.0)
            clipper = VideoClipper(None)  # clip 不需要 ASR 模型
            clipper.lang = "zh"
            video = mpy.VideoFileClip(str(src))
            state = {
                # timestamp_list 直传分支不读这两项，但函数头无条件解包 → 占位
                "recog_res_raw": "",
                "timestamp": [],
                "sentences": build_sentences(lines),
                "video": video,
                "clip_video_file": str(dst),
                "video_filename": str(src),
            }
            out_file, _message, clip_srt = clipper.video_clip(
                "", 0, 0, state, add_sub=False, timestamp_list=ts_list,
            )
        finally:
            VideoClip.write_videofile = _orig_wvf  # type: ignore[method-assign]
            if video is not None:  # 服务常驻 — 必须释放源视频句柄
                try:
                    video.close()
                except Exception:  # noqa: BLE001
                    pass
            # 清掉上游写的临时音轨残片（正常路径已由 moviepy 删，兜底）
            for p in dst.parent.glob(dst.stem + "_tempaudio_no*.mp4"):
                try:
                    p.unlink()
                except OSError:
                    pass

    out_path = Path(out_file)
    if not out_path.exists():
        raise RuntimeError(f"上游合成未产出文件: {out_file}")
    os.replace(out_path, dst)
    if clip_srt:  # 随片字幕副产物（正式场景即 rough_compose.srt）
        dst.with_suffix(".srt").write_text(clip_srt, encoding="utf-8")
    dur = _probe_duration(dst)
    return {
        "duration": dur,
        "size_mb": round(dst.stat().st_size / 1024 / 1024, 1),
        "segments": len(merged_ms),
        "raw_segments": raw_n,
        "keep_sec": round(total_keep_ms / 1000.0, 1),
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


def start_compose(task_id: str, video_path: Path, intervals_ms: list[tuple[int, int]],
                  dst: Path, lines: list[dict]) -> bool:
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

        def _tick(pct: float) -> None:
            job["progress"] = round(pct, 1)

        try:
            result = compose_rough_cut(video_path, intervals_ms, dst, lines, on_progress=_tick)
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
