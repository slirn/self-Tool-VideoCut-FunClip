"""Stage 3.5（字幕一致性核验）：Stage 3 之后、Stage 4 之前插入的核验阶段。

REQ-20261004-verify：在 Stage 3 AI 拆条完成、Stage 4 粗剪之前，对每个 highlight
做一次「音频 ↔ 字幕」一致性核验，避免用错误字幕 / 错位时间戳去切视频。

**只生成分析报告**，不做任何自动修改；用户根据报告自行决定：

- ✅ 一致：subtitle_lines 与音频 ASR 文本相符 → 可放心进入 Stage 4
- ⚠️ 字幕文本可能不准：原文本与 ASR 部分相符（相似度 0.4-0.7）
- ❌ 字幕错：subtitle_lines 内容与 ASR 不符（相似度 < 0.4）→ 建议改成 ASR 文本
- ❌ 切片可能错位：subtitle_lines 中有某行找不到对应的 ASR 文本
  → 建议调整 [start_ms, end_ms] 或删除该字幕行
- 🆕 字幕缺失：ASR 文本中有对应时间窗口但 subtitle_lines 没收录
  → 建议加一行字幕

算法（每个 highlight）：
1. ffmpeg 从源视频 [start_ms, end_ms] 抽 audio slice（wav，16kHz mono，~2s）
2. funasr 跑 ASR → (start_ms, end_ms, text) 列表（带时间戳）
3. 把 subtitle_lines 拼成单字符串 → 与 ASR 文本做按行对齐 + 字符级相似度
4. 分类输出

产物：
- ``stage3_verify/report.json`` — 完整核验报告（disk 上）
- ``pipeline.stage3_verify`` — 项目状态机（status / per-highlight mismatch 概要）
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from slirn_home import short_video_extract as sv_extract
from slirn_home import short_video_service as svc

log = logging.getLogger(__name__)


# 字符级相似度阈值（用 difflib.SequenceMatcher.ratio()）
THRESHOLD_MATCH = 0.7    # ≥ 0.7 → ✅ 一致
THRESHOLD_PARTIAL = 0.4  # 0.4-0.7 → ⚠️ 部分相符
                            # < 0.4 → ❌ 不相符

# 时间对齐容差：ASR 行的中心点落在 highlight 时间 ±容差内才算「对应时间窗口」
TIME_TOL_MS = 1500


# ---------- 数据结构 ----------

@dataclass
class MismatchLine:
    """单条字幕行与 ASR 文本的对比结果。"""
    src_index: int
    subtitle_text: str
    asr_text: str = ""
    asr_start_ms: int = 0
    asr_end_ms: int = 0
    similarity: float = 0.0
    status: str = "missing"  # "ok" | "partial" | "mismatch" | "missing"
    suggestion: str = "无操作"


@dataclass
class HighlightVerifyResult:
    """单个 highlight 的核验结果。"""
    index: int
    start_ms: int
    end_ms: int
    title: str
    status: str = "ok"  # "ok" | "warning" | "failed" | "skipped"
    subtitle_count: int = 0
    asr_count: int = 0
    matched_count: int = 0
    mismatched_count: int = 0
    missing_in_audio: int = 0
    extra_in_audio: int = 0
    error: str = ""
    lines: list[MismatchLine] = None  # type: ignore[assignment]
    extra_asr_lines: list[dict] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.lines is None:
            self.lines = []
        if self.extra_asr_lines is None:
            self.extra_asr_lines = []


# ---------- 核心：ffmpeg 抽音频 ----------

def _extract_clip_audio(
    source_video: Path,
    start_ms: int,
    end_ms: int,
    out_wav: Path,
) -> bool:
    """ffmpeg 从源视频 [start_ms, end_ms] 抽音频到 out_wav (16k mono wav)。

    返回 True 成功。失败时静默返回 False（caller 用 fall-through 处理）。
    """
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    start_sec = max(0, start_ms / 1000.0)
    duration_sec = max(0.05, (end_ms - start_ms) / 1000.0)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{start_sec:.3f}",
        "-i", str(source_video),
        "-t", f"{duration_sec:.3f}",
        "-vn", "-ac", "1", "-ar", "16000",
        "-f", "wav",
        str(out_wav),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=120, encoding="utf-8", errors="replace",
        )
        return proc.returncode == 0 and out_wav.is_file() and out_wav.stat().st_size > 100
    except subprocess.TimeoutExpired:
        log.warning("ffmpeg audio extract timeout for %s [%d, %d]", source_video, start_ms, end_ms)
        return False
    except Exception as e:  # noqa: BLE001
        log.warning("ffmpeg audio extract failed: %s", e)
        return False


def _wrap_wav_as_silent_mp4(wav_path: Path, mp4_path: Path) -> bool:
    """把 wav 包成 mp4（带黑色静帧视频轨）。

    上游 extract_subtitle.py 用 moviepy.editor.VideoFileClip 读输入，wav 没有
    video stream 会抛 KeyError 'video_fps'。给 wav 加一轨 320x240 黑色静帧视频
    （rate=10 够用）让 moviepy 能正常解码。

    返回 True 成功。
    """
    mp4_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=size=320x240:rate=10:color=black",
        "-i", str(wav_path),
        "-shortest",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        str(mp4_path),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=120, encoding="utf-8", errors="replace",
        )
        return proc.returncode == 0 and mp4_path.is_file() and mp4_path.stat().st_size > 100
    except Exception as e:  # noqa: BLE001
        log.warning("wav→mp4 wrap failed: %s", e)
        return False


def _wav_as_srt_input(wav_path: Path, srt_out: Path, repo_root: Path) -> bool:
    """对 wav 跑 funasr，输出 SRT。复用 _run_funasr_extract 的子进程封装。"""
    res = sv_extract._run_funasr_extract(
        input_video=wav_path,
        output_srt=srt_out,
        repo_root=repo_root,
        model="paraformer",
        lang="zh",
        timeout_sec=600,
    )
    return res.returncode == 0 and srt_out.is_file() and srt_out.stat().st_size > 4


def _parse_re_asr_srt(srt_text: str, offset_ms: int) -> list[dict]:
    """解析 ASR 产出的 SRT，返回每行的 (start_ms, end_ms, text)。

    ASR 输出的时间戳是相对 wav 的（从 0 开始），加 offset_ms 转回源视频时间轴。
    """
    out: list[dict] = []
    blocks = re.split(r'\n\s*\n', srt_text.strip())
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 3:
            continue
        try:
            idx = int(lines[0].strip())
        except ValueError:
            continue
        tm = re.match(
            r'(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)',
            lines[1].strip(),
        )
        if not tm:
            continue
        sh = int(tm.group(1)) * 3600000 + int(tm.group(2)) * 60000 + int(tm.group(3)) * 1000 + int(tm.group(4))
        eh = int(tm.group(5)) * 3600000 + int(tm.group(6)) * 60000 + int(tm.group(7)) * 1000 + int(tm.group(8))
        text = ' '.join(lines[2:]).strip()
        if not text:
            continue
        out.append({
            "idx": idx,
            "start_ms": sh + offset_ms,
            "end_ms": eh + offset_ms,
            "text": text,
        })
    return out


# ---------- 文本相似度 + 对齐 ----------

def _char_similarity(a: str, b: str) -> float:
    """字符级相似度 0..1。用 difflib.SequenceMatcher（已含在 stdlib）。

    先做轻量 normalize（去空白差异），避免空格/标点干扰 ASR 比对。
    """
    from difflib import SequenceMatcher
    na = re.sub(r'\s+', '', a or '')
    nb = re.sub(r'\s+', '', b or '')
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def _align_subtitle_to_asr(
    sub_text: str,
    sub_src_index: int,
    asr_lines: list[dict],
    expected_start_ms: int,
    expected_end_ms: int,
) -> MismatchLine:
    """把一条 subtitle_line 与 ASR 行对齐，返回 MismatchLine。

    对齐策略：取时间窗内（expected ± TIME_TOL_MS）的 ASR 行中相似度最高者。
    """
    ml = MismatchLine(src_index=sub_src_index, subtitle_text=sub_text)
    best_sim = -1.0
    best: dict | None = None
    for a in asr_lines:
        # 时间对齐：ASR 行中点应在 highlight 时间 ±容差内
        mid = (a["start_ms"] + a["end_ms"]) // 2
        if mid < expected_start_ms - TIME_TOL_MS or mid > expected_end_ms + TIME_TOL_MS:
            continue
        sim = _char_similarity(sub_text, a["text"])
        if sim > best_sim:
            best_sim = sim
            best = a
    if best is None:
        # 没有任何 ASR 行在时间窗口内 → 字幕缺失
        ml.status = "missing"
        ml.suggestion = (
            "字幕找不到对应音频；可能 [start_ms, end_ms] 区间不包含这段音频，"
            "或 funasr 漏识别。建议：(a) 调宽 [start_ms, end_ms]，或 (b) 删除该字幕行"
        )
        return ml
    ml.asr_text = best["text"]
    ml.asr_start_ms = best["start_ms"]
    ml.asr_end_ms = best["end_ms"]
    ml.similarity = round(best_sim, 3)
    if best_sim >= THRESHOLD_MATCH:
        ml.status = "ok"
        ml.suggestion = "无操作"
    elif best_sim >= THRESHOLD_PARTIAL:
        ml.status = "partial"
        ml.suggestion = (
            f"字幕文本与音频部分相符（相似度 {best_sim:.2f}）；可考虑改成 ASR 文本：{best['text']}"
        )
    else:
        ml.status = "mismatch"
        ml.suggestion = (
            f"字幕文本与音频不相符（相似度 {best_sim:.2f}）；建议替换为 ASR 文本：{best['text']}"
        )
    return ml


# ---------- 单个 highlight 核验 ----------

def _verify_one_highlight(
    source_video: Path,
    highlight: dict,
    asr_lines: list[dict],
    repo_root: Path,
    tmpdir: Path,
) -> HighlightVerifyResult:
    """对单个 highlight 跑 ffmpeg 抽音频 + funasr + 对齐。

    返回 HighlightVerifyResult。
    """
    idx = int(highlight.get("index") or 0)
    start_ms = int(highlight.get("start_ms") or 0)
    end_ms = int(highlight.get("end_ms") or 0)
    title = str(highlight.get("title") or f"片段 #{idx}")
    sub_lines = highlight.get("subtitle_lines") or []

    res = HighlightVerifyResult(
        index=idx, start_ms=start_ms, end_ms=end_ms, title=title,
        subtitle_count=len(sub_lines), asr_count=len(asr_lines),
    )

    if not sub_lines:
        res.status = "skipped"
        res.error = "highlight 无 subtitle_lines，跳过核验"
        return res

    # 1. ffmpeg 抽 wav
    wav_path = tmpdir / f"clip_{idx}.wav"
    if not _extract_clip_audio(source_video, start_ms, end_ms, wav_path):
        res.status = "failed"
        res.error = "ffmpeg 抽音频失败"
        return res

    # 2. wav → mp4（带静帧视频轨，让上游 moviepy 能解码）
    mp4_path = tmpdir / f"clip_{idx}.mp4"
    if not _wrap_wav_as_silent_mp4(wav_path, mp4_path):
        res.status = "failed"
        res.error = "wav → mp4 包装失败"
        return res

    # 3. funasr（吃 mp4 喂给上游 extract_subtitle.py）
    srt_path = tmpdir / f"clip_{idx}.srt"
    if not _wav_as_srt_input(mp4_path, srt_path, repo_root):
        res.status = "failed"
        res.error = "funasr ASR 失败（详见 server log）"
        return res

    # 3. 解析 ASR SRT（注意时间偏移）
    re_asr = _parse_re_asr_srt(srt_path.read_text(encoding="utf-8", errors="replace"), offset_ms=start_ms)
    res.asr_count = len(re_asr)

    # 4. 对齐每条 subtitle_line
    matched_asr_indices: set[int] = set()
    for sl in sub_lines:
        if not isinstance(sl, dict):
            continue
        try:
            si = int(sl.get("src_index") or 0)
        except (TypeError, ValueError):
            continue
        text = str(sl.get("text") or "").strip()
        if not text:
            continue
        ml = _align_subtitle_to_asr(text, si, re_asr, start_ms, end_ms)
        res.lines.append(ml)
        if ml.status == "ok":
            res.matched_count += 1
        else:
            res.mismatched_count += 1
        # 记录被匹配到的 ASR idx 用于「额外字幕」统计
        if ml.status in ("ok", "partial") and ml.asr_text:
            for a in re_asr:
                if a["text"] == ml.asr_text:
                    matched_asr_indices.add(a["idx"])

    # 5. 找 ASR 中「未匹配」的字幕（音频有但字幕没收录）
    for a in re_asr:
        if a["idx"] not in matched_asr_indices:
            res.extra_asr_lines.append({
                "start_ms": a["start_ms"],
                "end_ms": a["end_ms"],
                "text": a["text"],
            })
            res.extra_in_audio += 1

    # 6. 汇总状态
    missing_count = sum(1 for l in res.lines if l.status == "missing")
    res.missing_in_audio = missing_count
    if res.mismatched_count == 0 and res.missing_in_audio == 0 and res.extra_in_audio == 0:
        res.status = "ok"
    elif res.mismatched_count + res.missing_in_audio + res.extra_in_audio <= 1:
        res.status = "warning"
    else:
        res.status = "failed"

    return res


# ---------- 主入口 ----------

def run_stage3_verify(
    repo_root: Path | str,
    task_id: str,
    project_id: str,
    *,
    model: str = "paraformer",
) -> dict:
    """Stage 3.5：对 Stage 3 产生的每个 highlight 跑音频↔字幕一致性核验。

    返回最新的 ``pipeline.stage3_verify`` 状态。
    """
    root = Path(repo_root)
    project = svc.load_project(root, task_id, project_id)

    # 读源视频（必须先 Stage 1 选源）
    src_id = project.get("base_material_id") or ""
    src = next(
        (m for m in project.get("materials") or []
         if m.get("id") == src_id and m.get("kind") == "video"),
        None,
    )
    if not src:
        raise svc.ShortVideoError("Stage 3.5 需要先在 Stage 1 选源视频")
    source_video = svc.material_abs_path(root, project, src["id"])

    # 读 highlights
    highlights_path = svc.stage_highlights_path(root, task_id, project_id)
    if not highlights_path.is_file():
        raise svc.ShortVideoError("Stage 3.5 需要先跑 Stage 3 生成 highlights.json")
    hl_doc = json.loads(highlights_path.read_text(encoding="utf-8"))
    highlights = hl_doc.get("highlights") or []
    if not highlights:
        raise svc.ShortVideoError("highlights.json 没有有效片段")

    state = svc.get_stage_state(project, "stage3_verify")
    state["status"] = "running"
    state["started_at"] = svc.now_iso()
    state["count"] = len(highlights)
    state["logs"] = list(state.get("logs") or []) + [
        f"[{svc.now_iso()}] stage3_verify start, {len(highlights)} clips",
    ]
    svc.set_stage_state(project, "stage3_verify", state)
    svc.save_project(root, project)

    # 创建核验输出目录
    verify_dir = svc.stage3_verify_dir(root, task_id, project_id)
    verify_dir.mkdir(parents=True, exist_ok=True)
    verify_state_dir = verify_dir / "per_clip"
    verify_state_dir.mkdir(parents=True, exist_ok=True)

    results: list[HighlightVerifyResult] = []
    any_failed = False
    with tempfile.TemporaryDirectory(prefix="slirn_verify_") as tmp:
        tmpdir = Path(tmp)
        for hl in highlights:
            idx = int(hl.get("index") or (len(results) + 1))
            # 该 highlight 的子目录（用于单独 SRT 缓存）
            clip_dir = verify_state_dir / f"clip_{idx:02d}"
            clip_dir.mkdir(parents=True, exist_ok=True)
            r = _verify_one_highlight(source_video, hl, [], root, tmpdir)
            # 把 wav/mp4/srt 留到 clip_dir 便于排查
            for ext in ("wav", "mp4", "srt"):
                p = tmpdir / f"clip_{idx}.{ext}"
                if p.is_file():
                    try:
                        p.replace(clip_dir / p.name)
                    except Exception:  # noqa: BLE001
                        pass
            results.append(r)
            any_failed = any_failed or r.status == "failed"

            # 增量保存（断电/重启可恢复）
            _save_report(verify_dir, results)
            _logs = list(state.get("logs") or [])
            _logs.append(
                f"[{svc.now_iso()}] clip #{idx} {r.status}"
                f" sub={r.subtitle_count} asr={r.asr_count}"
                f" matched={r.matched_count} mismatch={r.mismatched_count}"
                f" missing={r.missing_in_audio} extra={res.extra_in_audio}"
                if False else  # noqa: E501 — 避免下面一行过长
                f"[{svc.now_iso()}] clip #{idx} {r.status}"
                f" sub={r.subtitle_count} asr={r.asr_count}"
                f" matched={r.matched_count} mismatch={r.mismatched_count}"
                f" missing={r.missing_in_audio} extra={r.extra_in_audio}"
                + (f" err={r.error}" if r.error else "")
            )
            state["logs"] = _logs
            svc.set_stage_state(project, "stage3_verify", state)
            svc.save_project(root, project)

    final_status = "done" if not any_failed else "failed"
    state["status"] = final_status
    state["finished_at"] = svc.now_iso()
    summary = {
        "ok": sum(1 for r in results if r.status == "ok"),
        "warning": sum(1 for r in results if r.status == "warning"),
        "failed": sum(1 for r in results if r.status == "failed"),
        "skipped": sum(1 for r in results if r.status == "skipped"),
    }
    state["summary"] = summary
    state["highlights"] = [
        {
            "index": r.index,
            "status": r.status,
            "subtitle_count": r.subtitle_count,
            "asr_count": r.asr_count,
            "matched_count": r.matched_count,
            "mismatched_count": r.mismatched_count,
            "missing_in_audio": r.missing_in_audio,
            "extra_in_audio": r.extra_in_audio,
            "error": r.error,
        }
        for r in results
    ]
    state["logs"] = list(state.get("logs") or []) + [
        f"[{svc.now_iso()}] stage3_verify {final_status}, summary={summary}",
    ]
    svc.set_stage_state(project, "stage3_verify", state)
    svc.save_project(root, project)
    return state


def _save_report(verify_dir: Path, results: list[HighlightVerifyResult]) -> None:
    """把当前累积的 results 写到 ``report.json``（每 clip 一个核验一份）。"""
    payload = {
        "generated_at": svc.now_iso(),
        "highlights": [
            {
                "index": r.index,
                "start_ms": r.start_ms,
                "end_ms": r.end_ms,
                "title": r.title,
                "status": r.status,
                "subtitle_count": r.subtitle_count,
                "asr_count": r.asr_count,
                "matched_count": r.matched_count,
                "mismatched_count": r.mismatched_count,
                "missing_in_audio": r.missing_in_audio,
                "extra_in_audio": r.extra_in_audio,
                "error": r.error,
                "lines": [
                    {
                        "src_index": l.src_index,
                        "subtitle_text": l.subtitle_text,
                        "asr_text": l.asr_text,
                        "asr_start_ms": l.asr_start_ms,
                        "asr_end_ms": l.asr_end_ms,
                        "similarity": l.similarity,
                        "status": l.status,
                        "suggestion": l.suggestion,
                    }
                    for l in r.lines
                ],
                "extra_asr_lines": r.extra_asr_lines,
            }
            for r in results
        ],
    }
    out = verify_dir / "report.json"
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------- CLI 入口（便于手工调试） ----------

def _cli() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Stage 3.5 字幕一致性核验")
    p.add_argument("--repo-root", default=".")
    p.add_argument("--task-id", required=True)
    p.add_argument("--project-id", required=True)
    args = p.parse_args()
    state = run_stage3_verify(args.repo_root, args.task_id, args.project_id)
    print(json.dumps({"status": state.get("status"), "summary": state.get("summary")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
