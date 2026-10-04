"""短视频工作台 Stage 2（提取字幕）+ Stage 5（重 ASR）执行模块。

REQ-20261003-098：

- Stage 2: 把源视频的音频交给 funasr，产物 ``stage2/raw.srt`` + ``stage2/raw.json``.
- Stage 5: 对 Stage 4 粗剪 mp4 重新 ASR，产物 ``stage5/refined_NN_asr.srt``；
  用户在 UI 修改后另存 ``stage5/refined_NN.srt``（Stage 6 优先吃这个）。

funasr 1.3.9 的注册表只认短名（SeacoParaformer / Paraformer / SenseVoiceSmall）；
extract_subtitle.py 内部用的是长路径（iic/speech_seaco_paraformer_large_...），
所以子进程起之前必须先 patch 注册表。patch 在子进程内重做（每个进程独立）；
为简化这里走 ``python -c "<patch + runpy.run_path>"`` 模式，等价于
``tools/_launch_upstream.py`` 在 Gradio 启动时的做法。
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from slirn_home import short_video_service as svc

log = logging.getLogger(__name__)


# ---------- 子进程包装 ----------

# Skill 在两种仓库布局下都存在（路径差异）：
#   1) funclip-main 根 / slirn/  (submodule mount) → skill/video-subtitle-extractor/scripts/
#   2) slirn-standalone 根       → skill/video-subtitle-extractor/scripts/
# 在 live 上下文中 repo_root 是 slirn-standalone；在 smoke 测试里是 funclip-main。
# _resolve_extract_subtitle 按候选顺序找到第一个能解析的脚本路径。
EXTRACT_SUBTITLE_RELPATH_CANDIDATES = (
    Path("slirn") / "skill" / "video-subtitle-extractor" / "scripts" / "extract_subtitle.py",
    Path("skill") / "video-subtitle-extractor" / "scripts" / "extract_subtitle.py",
)


# 在子进程内跑的 boot 脚本：先 patch funasr，再把 funclip 包所在目录的 PARENT
# 加到 sys.path[0]，然后 runpy 执行目标。仅依赖标准库 + 已安装的 funasr/funclip，
# 避免把 slirn_home 的内部 import 串进来。
# REQ-20261004-bugfix：在 live 上下文 repo_root=slirn-standalone，funclip 包位于
# sibling FunClip-main/funclip/；smoke 上下文 repo_root=FunClip-main，funclip 就在
# <repo_root>/funclip。runner 按候选顺序找 funclip 包的上级目录，insert sys.path[0]。
_RUNNER_BODY = r'''
import os, sys, runpy
# patch funasr 1.3.9 model registry（与 _launch_upstream.py 等价）
import funasr  # noqa: F401
from funasr.register import tables
ALIASES = [
    ("iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch", "SeacoParaformer"),
    ("iic/speech_paraformer_asr-en-16k-vocab4199-pytorch", "Paraformer"),
    ("iic/SenseVoiceSmall", "SenseVoiceSmall"),
]
for long_name, short_name in ALIASES:
    cls = tables.model_classes.get(short_name)
    if cls is not None and long_name not in tables.model_classes:
        tables.model_classes[long_name] = cls

# 把 FunClip-main 根 + funclip 包目录 都加到 sys.path，避免 ModuleNotFoundError。
# 关键点：funclip/videoclipper.py 内部既需要 `import funclip.videoclipper`（要
# FunClip-main 根在 sys.path），又需要 `from utils.subtitle_utils import ...`（要
# funclip/ 目录本身在 sys.path，这样 utils 作为 funclip.utils 的兄弟被找到）。
# 两者必须同时插入。
# 候选：repo_root 的 funclip 子目录、repo_root 的 sibling FunClip-main/funclip、
# cwd 的 funclip 子目录、cwd 的 sibling FunClip-main/funclip。
_arg_repo = os.environ.get("SLIRN_SV_REPO_ROOT")
_cwd = os.getcwd()
_funclip_dirs = []  # funclip/ 目录路径
_funclip_parents = []  # funclip 的父目录（FunClip-main 根）
for base in [_arg_repo, _cwd]:
    if not base:
        continue
    base_path = base if os.path.isabs(base) else os.path.abspath(base)
    _funclip_dirs.append(os.path.join(base_path, "funclip"))
    _funclip_dirs.append(os.path.join(os.path.dirname(base_path), "FunClip-main", "funclip"))
for d in _funclip_dirs:
    if os.path.isdir(d):
        # 1) 先把 funclip 父目录（FunClip-main 根）插到 sys.path 末位
        parent = os.path.dirname(d)
        if parent not in sys.path:
            sys.path.append(parent)
        # 2) 再把 funclip 目录插到 sys.path[0]（优先级最高），让 `from utils...`
        # 解析到 funclip.utils
        if d not in sys.path:
            sys.path.insert(0, d)
        break

target = sys.argv[1]
sys.argv = sys.argv[1:]
runpy.run_path(target, run_name="__main__")
'''


def _resolve_extract_subtitle(repo_root: Path) -> Path:
    tried: list[Path] = []
    for rel in EXTRACT_SUBTITLE_RELPATH_CANDIDATES:
        cand = (repo_root / rel).resolve()
        if cand.is_file():
            return cand
        tried.append(cand)
    raise FileNotFoundError(
        "找不到上游 extract_subtitle.py（候选: "
        + " | ".join(str(p) for p in tried)
        + f"；repo_root={repo_root}）"
    )


def _runner_path() -> Path:
    """子进程内的 boot 脚本写到 tmp，避免污染仓库。"""
    p = Path(tempfile.gettempdir()) / "_slirn_sv_runner.py"
    if not p.is_file() or p.read_text(encoding="utf-8", errors="replace") != _RUNNER_BODY:
        p.write_text(_RUNNER_BODY, encoding="utf-8")
    return p


# ---------- 通用 funasr 子进程调用 ----------

@dataclass
class FunasrRunResult:
    srt_path: Path
    raw_json: dict | None
    elapsed_sec: int
    stderr_tail: str
    returncode: int


def _run_funasr_extract(
    *,
    input_video: Path,
    output_srt: Path,
    repo_root: Path,
    model: str = "paraformer",
    lang: str = "zh",
    hotwords: str = "",
    sd_switch: str = "no",
    timeout_sec: int = 1800,
) -> FunasrRunResult:
    """跑 extract_subtitle.py 子进程；产物 = output_srt。

    输出 ``raw.json`` 不由上游直接生成，但若 output_srt 同目录下有 ``state.json``
    （上游 videoclipper 调用约定）则一并拷贝到 raw.json。
    """
    input_video = input_video.resolve()
    output_srt = output_srt.resolve()
    output_srt.parent.mkdir(parents=True, exist_ok=True)
    target = _resolve_extract_subtitle(repo_root)
    runner = _runner_path()

    cmd = [
        sys.executable, str(runner), str(target),
        "--input", str(input_video),
        "--output", str(output_srt),
        "--model", model,
        "--lang", lang,
        "--hotwords", hotwords,
        "--sd-switch", sd_switch,
    ]
    log.info("[funasr] %s", " ".join(cmd))

    t0 = time.time()
    try:
        # REQ-20261004-bugfix：把 repo_root 通过环境变量传给 runner，runner 据此
        # 把 funclip 包所在目录插入 sys.path（slirn-standalone 上下文）。
        proc = subprocess.run(
            cmd, cwd=str(repo_root),
            capture_output=True, text=True,
            timeout=timeout_sec, encoding="utf-8", errors="replace",
            env={**os.environ, "SLIRN_SV_REPO_ROOT": str(repo_root)},
        )
    except subprocess.TimeoutExpired:
        return FunasrRunResult(
            srt_path=output_srt, raw_json=None,
            elapsed_sec=timeout_sec,
            stderr_tail=f"timeout after {timeout_sec}s",
            returncode=-1,
        )
    elapsed = int(time.time() - t0)
    stderr_tail = (proc.stderr or "")[-1500:]
    if proc.returncode != 0 or not output_srt.is_file() or output_srt.stat().st_size < 4:
        return FunasrRunResult(
            srt_path=output_srt, raw_json=None,
            elapsed_sec=elapsed, stderr_tail=stderr_tail,
            returncode=proc.returncode,
        )

    # 试着从同目录找 state.json（上游约定），拷贝为 raw.json
    raw_json: dict | None = None
    state_json = output_srt.parent / "state.json"
    if state_json.is_file():
        try:
            data = json.loads(state_json.read_text(encoding="utf-8", errors="replace"))
            raw_json = data if isinstance(data, dict) else {"_raw": data}
        except Exception as e:  # noqa: BLE001
            log.warning("state.json parse failed: %s", e)
    return FunasrRunResult(
        srt_path=output_srt, raw_json=raw_json,
        elapsed_sec=elapsed, stderr_tail=stderr_tail,
        returncode=proc.returncode,
    )


# ---------- Stage 2 ----------

def run_stage2(
    repo_root: Path | str,
    task_id: str,
    project_id: str,
    *,
    model: str = "paraformer",
    lang: str = "zh",
    hotwords: str = "",
    sd_switch: str = "no",
) -> dict:
    """Stage 2: 对源视频跑 funasr → ``stage2/raw.srt`` + ``stage2/raw.json``。

    源视频 = ``project.base_material_id`` 对应的素材。
    返回最新 pipeline.stage2_extract 状态。
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
        raise svc.ShortVideoError("Stage 2 需要先在 Stage 1 选源视频")

    state = svc.get_stage_state(project, "stage2_extract")
    state["status"] = "running"
    state["started_at"] = svc.now_iso()
    state["model"] = model
    state["lang"] = lang
    state["logs"] = list(state.get("logs") or []) + [
        f"[{svc.now_iso()}] stage2 start, model={model} lang={lang}",
    ]
    svc.set_stage_state(project, "stage2_extract", state)
    svc.save_project(root, project)

    srt_path = svc.stage_raw_srt_path(root, task_id, project_id)
    input_video = svc.material_abs_path(root, project, src["id"])
    result = _run_funasr_extract(
        input_video=input_video, output_srt=srt_path,
        repo_root=root, model=model, lang=lang,
        hotwords=hotwords, sd_switch=sd_switch,
    )

    state = svc.get_stage_state(project, "stage2_extract")
    if result.returncode != 0 or not result.srt_path.is_file() or result.srt_path.stat().st_size < 4:
        state["status"] = "failed"
        state["finished_at"] = svc.now_iso()
        state["error"] = f"funasr 退出码 {result.returncode}"
        state["stderr_tail"] = result.stderr_tail
        state["elapsed_sec"] = result.elapsed_sec
    else:
        raw_json_path = svc.stage_raw_json_path(root, task_id, project_id)
        if result.raw_json is not None:
            raw_json_path.write_text(
                json.dumps(result.raw_json, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        state["status"] = "done"
        state["finished_at"] = svc.now_iso()
        state["srt_path"] = svc._rel(root, srt_path)
        state["raw_json_path"] = svc._rel(root, raw_json_path) if result.raw_json else ""
        state["srt_chars"] = len(srt_path.read_text(encoding="utf-8", errors="replace"))
        state["elapsed_sec"] = result.elapsed_sec
        # REQ-20261004-bugfix：清掉上一次失败的 error/stderr_tail，避免 UI 显示陈旧报错。
        state.pop("error", None)
        state.pop("stderr_tail", None)
        # Stage 2 成功 → Stage 3-6 全部作废（除非已 done）
        svc.reset_downstream_stages(project, "stage2_extract")
    svc.set_stage_state(project, "stage2_extract", state)
    svc.save_project(root, project)
    return state


# ---------- Stage 5 ----------

def run_stage5_one(
    repo_root: Path | str,
    task_id: str,
    project_id: str,
    index: int,
    *,
    model: str = "paraformer",
    lang: str = "zh",
    timeout_sec: int = 1800,
) -> dict:
    """Stage 5: 对 Stage 4 粗剪 mp4 重新 ASR → ``stage5/refined_NN_asr.srt``。

    默认用 paraformer-zh（与 Stage 2 一致）。返回 stage5_refine.highlights[N] 状态。
    """
    root = Path(repo_root)
    project = svc.load_project(root, task_id, project_id)
    coarse_mp4, _ = svc.stage4_paths(root, task_id, project_id, index)
    if not coarse_mp4.is_file():
        raise svc.ShortVideoError(f"粗剪 mp4 不存在: {coarse_mp4}")

    s5_state = svc.get_stage_state(project, "stage5_refine")
    highlights = list(s5_state.get("highlights") or [])
    while len(highlights) < index:
        highlights.append({"index": len(highlights) + 1, "status": "pending"})

    hl = highlights[index - 1]
    hl["status"] = "running_asr"
    hl["asr_started_at"] = svc.now_iso()
    highlights[index - 1] = hl
    s5_state["highlights"] = highlights
    s5_state["logs"] = list(s5_state.get("logs") or []) + [
        f"[{svc.now_iso()}] clip #{index} ASR start, model={model}",
    ]
    svc.set_stage_state(project, "stage5_refine", s5_state)
    svc.save_project(root, project)

    asr_srt = svc.stage5_default_srt_path(root, task_id, project_id, index)
    refined_srt = svc.stage5_refined_srt_path(root, task_id, project_id, index)
    result = _run_funasr_extract(
        input_video=coarse_mp4, output_srt=asr_srt,
        repo_root=root, model=model, lang=lang, timeout_sec=timeout_sec,
    )

    s5_state = svc.get_stage_state(project, "stage5_refine")
    highlights = list(s5_state.get("highlights") or [])
    hl = highlights[index - 1]
    _logs = list(s5_state.get("logs") or [])
    if result.returncode != 0 or not asr_srt.is_file() or asr_srt.stat().st_size < 4:
        hl["status"] = "failed"
        hl["asr_error"] = f"funasr 退出码 {result.returncode}"
        hl["asr_stderr_tail"] = result.stderr_tail
        _logs.append(
            f"[{svc.now_iso()}] clip #{index} ASR failed: funasr 退出码 {result.returncode}"
        )
    else:
        # 没用户改过的 refined_NN.srt → 默认等于 ASR 结果
        if not refined_srt.is_file():
            refined_srt.write_text(asr_srt.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        hl["status"] = "user_editing"
        hl["asr_srt"] = svc._rel(root, asr_srt)
        hl["refined_srt"] = svc._rel(root, refined_srt)
        hl["asr_chars"] = len(asr_srt.read_text(encoding="utf-8", errors="replace"))
        _logs.append(
            f"[{svc.now_iso()}] clip #{index} ASR done, "
            f"chars={hl['asr_chars']} elapsed={result.elapsed_sec}s"
        )
    hl["asr_finished_at"] = svc.now_iso()
    highlights[index - 1] = hl
    s5_state["highlights"] = highlights
    s5_state["logs"] = _logs
    svc.set_stage_state(project, "stage5_refine", s5_state)
    svc.save_project(root, project)
    return hl


def save_stage5_refined(
    repo_root: Path | str,
    task_id: str,
    project_id: str,
    index: int,
    *,
    srt_text: str | None = None,
    srt_file: Path | None = None,
) -> dict:
    """Stage 5: 把用户修正过的字幕存到 ``stage5/refined_NN.srt``。

    Args:
        srt_text: 直接给完整 SRT 文本（UI 编辑后整段提交）
        srt_file: 或给一个绝对路径文件（用于上传/粘贴文件）
    """
    root = Path(repo_root)
    project = svc.load_project(root, task_id, project_id)
    refined_srt = svc.stage5_refined_srt_path(root, task_id, project_id, index)

    if srt_text is not None:
        text = srt_text
    elif srt_file is not None:
        text = Path(srt_file).read_text(encoding="utf-8", errors="replace")
    else:
        raise svc.ShortVideoError("需要 srt_text 或 srt_file 之一")

    # 简单规范化：行尾统一
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"
    refined_srt.parent.mkdir(parents=True, exist_ok=True)
    refined_srt.write_text(text, encoding="utf-8")

    s5_state = svc.get_stage_state(project, "stage5_refine")
    highlights = list(s5_state.get("highlights") or [])
    while len(highlights) < index:
        highlights.append({"index": len(highlights) + 1, "status": "pending"})
    hl = highlights[index - 1]
    hl["status"] = "confirmed"
    hl["refined_srt"] = svc._rel(root, refined_srt)
    hl["confirmed_at"] = svc.now_iso()
    hl["chars"] = len(text)
    highlights[index - 1] = hl
    s5_state["highlights"] = highlights
    s5_state["logs"] = list(s5_state.get("logs") or []) + [
        f"[{svc.now_iso()}] clip #{index} refined saved, chars={len(text)}",
    ]
    svc.set_stage_state(project, "stage5_refine", s5_state)
    svc.save_project(root, project)
    return hl


# ---------- CLI ----------

def _cli_stage2() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Stage 2: extract subtitle")
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--model", default="paraformer")
    ap.add_argument("--lang", default="zh")
    ap.add_argument("--hotwords", default="")
    ap.add_argument("--sd-switch", choices=["yes", "no"], default="no")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    state = run_stage2(
        args.repo_root, args.task_id, args.project_id,
        model=args.model, lang=args.lang,
        hotwords=args.hotwords, sd_switch=args.sd_switch,
    )
    print(json.dumps({k: v for k, v in state.items() if k != "logs"}, ensure_ascii=False, indent=2))
    return 0 if state.get("status") == "done" else 1


def _cli_stage5() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Stage 5: re-ASR coarse mp4")
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--index", type=int, required=True)
    ap.add_argument("--model", default="paraformer")
    ap.add_argument("--lang", default="zh")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    hl = run_stage5_one(
        args.repo_root, args.task_id, args.project_id, args.index,
        model=args.model, lang=args.lang,
    )
    print(json.dumps(hl, ensure_ascii=False, indent=2))
    return 0 if hl.get("status") in ("user_editing", "confirmed") else 1


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p2 = sub.add_parser("stage2")
    p2.add_argument("--repo-root", required=True)
    p2.add_argument("--task-id", required=True)
    p2.add_argument("--project-id", required=True)
    p2.add_argument("--model", default="paraformer")
    p2.add_argument("--lang", default="zh")
    p2.add_argument("--hotwords", default="")
    p2.add_argument("--sd-switch", choices=["yes", "no"], default="no")
    p5 = sub.add_parser("stage5")
    p5.add_argument("--repo-root", required=True)
    p5.add_argument("--task-id", required=True)
    p5.add_argument("--project-id", required=True)
    p5.add_argument("--index", type=int, required=True)
    p5.add_argument("--model", default="paraformer")
    p5.add_argument("--lang", default="zh")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.cmd == "stage2":
        state = run_stage2(
            args.repo_root, args.task_id, args.project_id,
            model=args.model, lang=args.lang,
            hotwords=args.hotwords, sd_switch=args.sd_switch,
        )
        print(json.dumps({k: v for k, v in state.items() if k != "logs"}, ensure_ascii=False, indent=2))
        raise SystemExit(0 if state.get("status") == "done" else 1)
    else:
        hl = run_stage5_one(
            args.repo_root, args.task_id, args.project_id, args.index,
            model=args.model, lang=args.lang,
        )
        print(json.dumps(hl, ensure_ascii=False, indent=2))
        raise SystemExit(0 if hl.get("status") in ("user_editing", "confirmed") else 1)