"""字幕生成服务 — REQ-20260915-001。

复用上游 funclip.videoclipper.VideoClipper 的识别链路（seaco-paraformer + 热词），
后台线程执行 + 内存 job 表 + 阶段级进度。结果双写 tasks/<id>/outputs/：
subtitle.srt（utf-8-sig）+ subtitle.json（结构化段列表）。

设计见 docs/design/DESIGN-20260915-001-subtitle-stage.md。
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from slirn_home.paths import find_repo_root as _find_repo_root

log = logging.getLogger(__name__)

# funclip/videoclipper.py 顶层用 `from utils.xxx import`（相对包名导入），
# 需要把仓库根 + funclip/ 目录都挂进 sys.path（与 skill 脚本 extract_subtitle.py 同法）
_funclip_root = _find_repo_root()
_FUNCLIP_PKG = _funclip_root / "funclip"
for _p in (str(_funclip_root), str(_FUNCLIP_PKG)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# =============== 模型（懒加载单例） ===============

_MODEL = None
_MODEL_LOCK = threading.Lock()


def _build_model():
    """seaco-paraformer（热词优化版）+ VAD + 标点 — 与 skill extract_subtitle.py 的
    paraformer 分支完全一致（模型已离线缓存于 ~/.cache/modelscope）。"""
    from funasr import AutoModel

    return AutoModel(
        model="iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
        vad_model="damo/speech_fsmn_vad_zh-cn-16k-common-pytorch",
        punc_model="damo/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
    )


def get_model():
    """首次调用加载模型（约 10-30s），之后复用。线程安全。"""
    global _MODEL
    if _MODEL is None:
        with _MODEL_LOCK:
            if _MODEL is None:
                log.info("[asr] 加载 FunASR 模型…")
                _MODEL = _build_model()
                log.info("[asr] 模型就绪")
    return _MODEL


# =============== 纯函数：sentence_info → 段列表 ===============

def segments_from_sentences(sentence_info: list[dict]) -> list[dict]:
    """funclip state['sentences'] → [{i, start_ms, end_ms, text, start, end}]。

    text 可能是 str（未拆分句）或 token 列表（_split_long_sentence 拆分产物），
    Text2SRT.text() 两者都处理（中文直接拼接、英文词空格连接）。
    无有效 timestamp 的条目跳过（与 generate_srt 行为一致）。
    """
    from utils.subtitle_utils import Text2SRT, time_convert

    segs = []
    for sent in sentence_info or []:
        ts = sent.get("timestamp")
        if not ts:
            continue
        try:
            t2s = Text2SRT(sent.get("text", ""), ts)
        except Exception:  # noqa: BLE001 — 单条畸形不拖垮整批
            log.warning("跳过畸形句子条目: %r", sent)
            continue
        segs.append({
            "i": len(segs) + 1,
            "start_ms": int(t2s.start_sec),
            "end_ms": int(t2s.end_sec),
            "start": time_convert(t2s.start_sec),
            "end": time_convert(t2s.end_sec),
            "text": t2s.text(),
        })
    return segs


# =============== 预检 ===============

def has_audio_track(video_path: Path) -> bool:
    """ffprobe 检查有无音频轨（上游 video_recog 对无音频视频直接 sys.exit）。"""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(video_path)],
            capture_output=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
        return "audio" in (r.stdout or "")
    except Exception as e:  # noqa: BLE001
        log.warning("ffprobe 预检失败（按有音频继续）: %s", e)
        return True


# =============== 识别入口（拆出便于测试 monkeypatch） ===============

def _run_recognition(video_path: str, hotword_str: str) -> tuple[str, dict]:
    """调上游 funclip VideoClipper.video_recog → (srt 文本, state)。"""
    from funclip.videoclipper import VideoClipper

    clipper = VideoClipper(get_model())
    clipper.lang = "zh"
    _res_text, res_srt, state = clipper.video_recog(
        video_filename=video_path,
        sd_switch="no",
        hotwords=hotword_str,
        output_dir=None,
    )
    return res_srt or "", state


# =============== 后台 job 管理 ===============

# task_id → {"state": "running|done|error", "stage": str, "error": str|None,
#            "started_at": float, "finished_at": float|None, "segments_count": int}
_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()

SUBTITLE_JSON = "subtitle.json"
SUBTITLE_SRT = "subtitle.srt"


def job_status(task_id: str) -> dict | None:
    with _JOBS_LOCK:
        j = _JOBS.get(task_id)
        return dict(j) if j else None


def start_job(
    task_id: str,
    video_path: Path,
    hotwords: list[str],
    outputs_dir: Path,
    source: str = "original",
    base_offset_ms: int = 0,
    on_success: Callable[[list[dict]], None] | None = None,
) -> bool:
    """启动字幕生成线程。已在跑 → 返回 False（不重复起）。

    on_success(segments) 在 worker 线程内、结果落盘之后调用（app 层用它迁任务状态）。
    """
    with _JOBS_LOCK:
        existing = _JOBS.get(task_id)
        if existing and existing.get("state") == "running":
            return False
        _JOBS[task_id] = {
            "state": "running", "stage": "加载模型", "error": None,
            "started_at": time.time(), "finished_at": None, "segments_count": 0,
        }

    def _run():
        job = _JOBS[task_id]

        def _stage(name: str) -> None:
            job["stage"] = name
            log.info("[asr][%s] %s", task_id, name)

        try:
            _stage("加载模型")
            hotword_str = " ".join(w.strip() for w in hotwords if w and w.strip())
            _stage("提取音频 → 识别中")
            res_srt, state = _run_recognition(str(video_path), hotword_str)

            _stage("保存结果")
            segments = segments_from_sentences(state.get("sentences") or [])
            outputs_dir.mkdir(parents=True, exist_ok=True)
            srt_path = outputs_dir / SUBTITLE_SRT
            srt_path.write_text(res_srt, encoding="utf-8-sig")
            meta = {
                "version": 1,
                "model": "seaco-paraformer",
                "source": source,
                "video_path": str(video_path).replace("\\", "/"),
                "video_url": f"/slirn/api/video/{task_id}",
                "base_offset_ms": base_offset_ms,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "segments": segments,
            }
            (outputs_dir / SUBTITLE_JSON).write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            job["segments_count"] = len(segments)
            job["state"] = "done"
            job["stage"] = "完成"
            job["finished_at"] = time.time()
            log.info("[asr][%s] 完成：%d 段", task_id, len(segments))
            if on_success:
                try:
                    on_success(segments)
                except Exception as e:  # noqa: BLE001 — 回调失败不影响结果
                    log.warning("[asr][%s] on_success 回调失败: %s", task_id, e)
        except SystemExit as e:  # 上游 video_recog 对无音频视频 sys.exit(1)
            job["state"] = "error"
            job["error"] = f"视频没有音频轨（无法识别）: exit={e.code}"
            job["finished_at"] = time.time()
        except Exception as e:  # noqa: BLE001 — 后台线程必须全兜底
            log.exception("[asr][%s] 生成失败", task_id)
            job["state"] = "error"
            job["error"] = str(e)
            job["finished_at"] = time.time()

    threading.Thread(target=_run, name=f"asr-{task_id}", daemon=True).start()
    return True


# =============== 读取既有结果 ===============

def load_subtitle(outputs_dir: Path) -> dict | None:
    """读取 subtitle.json（无/损坏 → None）。"""
    p = Path(outputs_dir) / SUBTITLE_JSON
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log.warning("subtitle.json 损坏: %s", e)
        return None
