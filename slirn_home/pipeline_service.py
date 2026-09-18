"""REQ-20260918-047 — 流程配置 + 自动执行。

Per-task 持久化 `outputs/pipeline.json`：
- `config`：5 阶段可选项（subtitle_generation/subtitle_review/rough_cut/rough_compose/optimize）
- `stop_after`：流程层总停点（覆盖每阶段的 stop_after，取更严的）
- `history`：最近 10 次完整运行的 summary

后台调度器（`_PIPELINE_JOBS[tid]`）守护线程顺序跑 5 个 handler，每个
handler 通过 in-process HTTP client（urllib）调现有 `/slirn/api/*` 端点，
复用现有 client polling 与 modal 状态机。

两层 stop_after：每阶段 config 内的 stop_after + 流程层 stop_after，取
阶段索引较小者（更靠前）为准。

任何阶段报错即停（不自动重试）；手动按钮始终可用，与自动跑共用同一组
service 的 _JOBS，并发时按各 service 的「running 拒绝新起」语义处理。

设计见 docs/design/DESIGN-20260918-047-pipeline-auto-run.md。
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

# 落盘文件名（与 execution_history 同模式：单文件 + 原子写 + 单写锁）
PIPELINE_FILENAME = "pipeline.json"

# 阶段顺序：key, 中文名, 工作台面板 key
STAGE_ORDER: list[tuple[str, str, str]] = [
    ("subtitle_generation", "字幕生成", "subtitle"),
    ("subtitle_review", "字幕修订", "subtitle_review"),
    ("rough_cut", "切分修剪", "rough_cut"),
    ("rough_compose", "粗剪合成", "rough_compose"),
    ("optimize", "优化字幕", "fine_review"),
]

# 阶段索引（用于 stop_after 比较）
STAGE_INDEX: dict[str, int] = {k: i for i, k in enumerate(s[0] for s in STAGE_ORDER)}
STAGE_INDEX[None] = 99  # 流程跑完 = 99（最大，永不因 stop_after 停）

# 单写锁（跨任务共享）
_WRITE_LOCK = threading.Lock()

# 模块级 job 表：tid → PipelineJob
_PIPELINE_JOBS: dict[str, "PipelineJob"] = {}
_JOBS_LOCK = threading.Lock()

# stop 请求位：tid → True 表示请求停止
_STOP_FLAGS: dict[str, bool] = {}
_STOP_LOCK = threading.Lock()


# =============== 数据类 ===============


@dataclass
class PipelineJob:
    """内存中的流程任务状态（每次 run_pipeline 启动时新建 / 复用）。"""

    state: str = "idle"  # idle / running / done / error / stopped
    current_stage: str | None = None
    percent: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    history: list[dict] = field(default_factory=list)  # 本次运行的日志（结构见 _append_log）
    summary: dict | None = None  # 完成后写回的 summary {stages_done, total_ms, status}

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "current_stage": self.current_stage,
            "percent": round(self.percent, 2),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "log": list(self.history),
            "summary": self.summary,
        }


# =============== 配置 schema ===============


def default_config() -> dict:
    """v4 默认配置：5 阶段都跑，顶层 stop_after='subtitle_review'（字幕修订后停）。"""
    return {
        "subtitle_generation": {
            "speaker_diarization": False,
        },
        "subtitle_review": {
            "accept_all_suggestions": True,
            "skip_categories": [],
        },
        "rough_cut": {
            "delete_speakers": [],
            "default_decision": "keep",
        },
        "rough_compose": {},
        "optimize": {
            "accept_all_replacements": True,
        },
        # v4 顶层字段：在哪个阶段完成后停（None = 跑到底）
        "stop_after": "subtitle_review",
    }


def validate_config(cfg: dict) -> dict:
    """校验 + 补全配置（缺字段用 default_config 兜底）。

    v4 schema：
    - 阶段 dict 内不再含 stop_after（顶层 stop_after 才是权威源）
    - 顶层 stop_after 是字符串（STAGE_ORDER 的某个 key）或 None
    - 兼容旧数据：v3 阶段内 boolean / v2 阶段内 string 字段被丢弃
    - 兼容 v2 顶层 string：直接当成 v4 顶层 stop_after 接受

    返回合法配置（不抛异常 — 任何坏字段都用默认值替换，便于 UI 编辑容错）。
    """
    base = default_config()
    if not isinstance(cfg, dict):
        return base
    # 顶层 stop_after 校验
    if "stop_after" in cfg:
        v = cfg["stop_after"]
        if v is None:
            base["stop_after"] = None
        elif isinstance(v, str) and v in STAGE_INDEX and STAGE_INDEX[v] != 99:
            base["stop_after"] = v
        # else: 无效值（None 字符串、未知 key、空串）→ 保留默认
    for stage_key in (s[0] for s in STAGE_ORDER):
        user_stage = cfg.get(stage_key)
        if not isinstance(user_stage, dict):
            continue  # 用默认
        merged = dict(base[stage_key])
        for k, v in user_stage.items():
            if k == "stop_after":
                # v3/v2 阶段内 stop_after：丢弃（顶层 stop_after 才是权威）
                continue
            elif k in merged:
                merged[k] = v
        base[stage_key] = merged
    return base


# =============== 持久化 ===============


def _read(outputs_dir: Path) -> dict | None:
    """读取 pipeline.json；不存在或损坏 → None。"""
    p = outputs_dir / PIPELINE_FILENAME
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return data
    except (OSError, json.JSONDecodeError) as e:
        log.warning("[pipeline][%s] 读取失败: %s", outputs_dir, e)
        return None


def _write_atomic(outputs_dir: Path, data: dict) -> None:
    """原子写：先写 .tmp 再 rename。"""
    outputs_dir.mkdir(parents=True, exist_ok=True)
    tmp = outputs_dir / (PIPELINE_FILENAME + ".tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(outputs_dir / PIPELINE_FILENAME)


def load_pipeline(outputs_dir: Path) -> dict | None:
    """读取 + 校验 pipeline.json。"""
    with _WRITE_LOCK:
        data = _read(outputs_dir)
    if data is None:
        return None
    # 校验/补全（merge default）保证调用方拿到完整 schema
    merged = validate_config(data.get("config") or {})
    return {
        "version": int(data.get("version") or 1),
        "config": merged,
        "updated_at": data.get("updated_at"),
        "history": list(data.get("history") or [])[-10:],  # 最近 10 条
    }


def save_pipeline(outputs_dir: Path, cfg: dict) -> str:
    """保存配置 + 记录 updated_at；保留 history 最近 10 条。返回 ISO 时间戳。

    cfg 必须含 config 字段（5 阶段）。
    """
    cfg_in = cfg.get("config") if isinstance(cfg, dict) else None
    merged = validate_config(cfg_in or cfg)
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    # 保留旧 history
    old = _read(outputs_dir) or {}
    new_data = {
        "version": 3,  # v4 schema：顶层 stop_after 字符串；阶段内不再含 stop_after
        "config": merged,
        "updated_at": ts,
        "history": list(old.get("history") or [])[-10:],
    }
    with _WRITE_LOCK:
        _write_atomic(outputs_dir, new_data)
    return ts


def append_history(outputs_dir: Path, summary: dict) -> None:
    """一次完整 run 完成后追加一条 summary（最多保留 10 条）。"""
    try:
        with _WRITE_LOCK:
            data = _read(outputs_dir) or {}
            history = list(data.get("history") or [])
            history.append(summary)
            data["history"] = history[-10:]
            _write_atomic(outputs_dir, data)
    except Exception as e:  # noqa: BLE001 — 历史写失败不影响主流程
        log.warning("[pipeline][%s] append_history 失败: %s", outputs_dir, e)


# =============== 内存 job 管理 ===============


def pipeline_status(tid: str) -> dict | None:
    """读内存 job（无 job → None 表示从未跑过）。"""
    with _JOBS_LOCK:
        j = _PIPELINE_JOBS.get(tid)
        return j.to_dict() if j else None


def _is_running(tid: str) -> bool:
    with _JOBS_LOCK:
        j = _PIPELINE_JOBS.get(tid)
        return bool(j and j.state == "running")


def _set_job(tid: str, job: PipelineJob) -> None:
    with _JOBS_LOCK:
        _PIPELINE_JOBS[tid] = job


def _request_stop(tid: str) -> None:
    with _STOP_LOCK:
        _STOP_FLAGS[tid] = True


def _consume_stop(tid: str) -> bool:
    with _STOP_LOCK:
        return bool(_STOP_FLAGS.pop(tid, False))


def _clear_stop(tid: str) -> None:
    with _STOP_LOCK:
        _STOP_FLAGS.pop(tid, None)


# =============== 调度器 ===============


def _http_post(api: str, path: str, payload: dict, *, timeout: float = 30.0) -> dict:
    """in-process HTTP client：POST {api}{path} → JSON 响应。

    与前端 router.js 的 postJSON 同源思路；用 urllib 零依赖。
    不抛异常：网络/解析错误 → 返回 {ok: False, error: <msg>}。
    """
    url = api.rstrip("/") + path
    body = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            return data if isinstance(data, dict) else {"ok": False, "error": "非 JSON 响应"}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.reason}"}
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"连接失败: {e.reason}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _http_get(api: str, path: str, payload: dict | None = None, *, timeout: float = 30.0) -> dict:
    """GET 用 query 参数传 task_id（与 subtitle_status 端点签名一致）。"""
    from urllib.parse import urlencode

    q = urlencode(payload or {})
    url = api.rstrip("/") + path + ("?" + q if q else "")
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            return data if isinstance(data, dict) else {"ok": False, "error": "非 JSON 响应"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _poll_status(api: str, status_path: str, tid: str, *,
                 interval: float = 2.0, timeout: float = 24 * 3600.0) -> tuple[str, str]:
    """轮询 status 端点直到 done / error / 超时。

    返回 (state, error) — state ∈ {done, error, timeout}。
    """
    t0 = time.time()
    last_err = ""
    while True:
        if _consume_stop(tid + ":poller"):  # 用户中途 stop 也算
            return ("stopped", "用户请求停止")
        try:
            r = _http_get(api, status_path, {"task_id": tid}, timeout=15.0)
            job = (r or {}).get("job") or {}
            state = str(job.get("state") or "")
            if state == "done":
                return ("done", "")
            if state == "error":
                return ("error", str(job.get("error") or "服务报错"))
            last_err = str(job.get("error") or "")
        except Exception as e:  # noqa: BLE001
            last_err = f"轮询异常: {e}"
        if time.time() - t0 > timeout:
            return ("timeout", f"轮询超时（>{int(timeout)}s）: {last_err}")
        time.sleep(interval)


# =============== 5 个 stage handler ===============
# 每个 handler 签名：handler(tid, cfg, outputs_dir, api, log_fn) -> tuple[bool, str]
# bool = success（不含 stop_after），str = 详情。失败时调度器整体停。


def _log(job: PipelineJob, stage: str, msg: str, level: str = "info") -> None:
    """调度器内 log helper：写进内存 job.history（前端轮询可见）。"""
    entry = {
        "stage": stage,
        "msg": msg,
        "level": level,
        "ts": time.time(),
        "ts_iso": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    job.history.append(entry)
    if len(job.history) > 200:  # 防止内存爆炸
        job.history = job.history[-200:]


def _check_prereq(outputs_dir: Path, required_files: list[str]) -> tuple[bool, str]:
    """检查 prereq 产物文件是否存在。"""
    missing = [f for f in required_files if not (outputs_dir / f).exists()]
    if missing:
        return (False, f"缺少依赖产物：{', '.join(missing)}")
    return (True, "")


def handler_subtitle_generation(tid: str, cfg: dict, outputs_dir: Path, api: str,
                                job: PipelineJob) -> tuple[bool, str]:
    """字幕生成阶段 — 调 /gen_subtitle + 等 /subtitle_status。"""
    sd_on = bool(cfg.get("speaker_diarization", False))
    _log(job, "subtitle_generation", f"启动字幕生成（区分说话人={sd_on}）")
    r = _http_post(api, "/gen_subtitle", {"task_id": tid, "sd": sd_on})
    if not r.get("ok"):
        return (False, f"启动字幕生成失败：{r.get('error') or '未知错误'}")
    _log(job, "subtitle_generation", "等待字幕生成完成（后台运行中…）")
    state, err = _poll_status(api, "/subtitle_status", tid)
    if state == "done":
        _log(job, "subtitle_generation", "✅ 字幕生成完成", "ok")
        return (True, "")
    return (False, f"字幕生成{state}：{err}")


def handler_subtitle_review(tid: str, cfg: dict, outputs_dir: Path, api: str,
                            job: PipelineJob) -> tuple[bool, str]:
    """字幕修订阶段 — 启动大模型分析 + 等完成 + (可选) 全接受 save_revision。

    prereq：subtitle.json 存在（由 handler 顺序保证，但兜底再 check）。
    """
    ok, err = _check_prereq(outputs_dir, ["subtitle.json"])
    if not ok:
        _log(job, "subtitle_review", f"跳过：{err}", "warn")
        return (True, "skip")  # 跳过不算失败（设计文档 AC-6）
    # 大模型分析必选严谨性级别 — 没现成选择就用 'medium' 默认（设计文档 §2.7 范围外）
    rigor = "medium"
    accept_all = bool(cfg.get("accept_all_suggestions", True))
    skip_cats = list(cfg.get("skip_categories") or [])
    _log(job, "subtitle_review", f"启动大模型分析（严谨性 {rigor}）")
    r = _http_post(api, "/revise_subtitle", {"task_id": tid, "rigor": rigor, "force": True})
    if not r.get("ok"):
        return (False, f"启动修订失败：{r.get('error')}")
    _log(job, "subtitle_review", "等待大模型分析完成…")
    state, err = _poll_status(api, "/revise_status", tid)
    if state != "done":
        return (False, f"字幕修订{state}：{err}")
    if not accept_all:
        _log(job, "subtitle_review", "⏸ 配置要求人工决策修订 — 不自动 save_revision，等人工去工作台处理", "info")
        # v3：accept_all_suggestions=False 时只跑 revise，不自动 save_revision。
        # 是否停在该阶段由 stage_cfg.stop_after 单独决定（调度器主循环判），
        # 这里不再 _request_stop。
        return (True, "revision 待人工决策")
    # 全接受：构造 save_revision payload 把所有 entry 决策为 accept
    rev_path = outputs_dir / "revision.json"
    if not rev_path.exists():
        return (False, "修订产物 revision.json 不存在")
    try:
        rev = json.loads(rev_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return (False, f"revision.json 解析失败：{e}")
    entries = (rev or {}).get("entries") or []
    # skip_categories 处理：跳过该类别的建议 → 决策为 pending（留给人工）
    decisions = []
    for e in entries:
        cat = str(e.get("category") or "")
        if cat in skip_cats:
            decisions.append({"i": int(e.get("i", 0)), "decision": "pending", "user_note": ""})
        else:
            decisions.append({"i": int(e.get("i", 0)), "decision": "accept", "user_note": ""})
    _log(job, "subtitle_review", f"应用全部接受决策（{len(decisions)} 条）")
    r = _http_post(api, "/save_revision",
                   {"task_id": tid, "decisions": decisions})
    if not r.get("ok"):
        return (False, f"save_revision 失败：{r.get('error')}")
    _log(job, "subtitle_review", "✅ 字幕修订完成（全部接受）", "ok")
    return (True, "")


def handler_rough_cut(tid: str, cfg: dict, outputs_dir: Path, api: str,
                      job: PipelineJob) -> tuple[bool, str]:
    """切分修剪阶段 — 生成切分清单 + (可选) 删说话人。

    prereq：revision.json 已保存（且 all_decided）。由 handler 顺序保证。
    """
    ok, err = _check_prereq(outputs_dir, ["subtitle.json", "revision.json"])
    if not ok:
        _log(job, "rough_cut", f"跳过：{err}", "warn")
        return (True, "skip")
    # 默认决策：keep（与设计文档 §2.1 默认同源）
    default_decision = str(cfg.get("default_decision") or "keep")
    _log(job, "rough_cut", f"生成切分清单（默认决策={default_decision}）")
    r = _http_post(api, "/build_cutlist", {"task_id": tid})
    if not r.get("ok"):
        return (False, f"build_cutlist 失败：{r.get('error')}")
    # 若配置删除说话人 → 调 cut_speaker + 标记 spk 全部行 delete
    del_spks = list(cfg.get("delete_speakers") or [])
    if del_spks:
        _log(job, "rough_cut", f"删除说话人 {del_spks} 的全部记录")
        # link speakers（自动算 rows）
        r = _http_post(api, "/cut_speaker_link", {"task_id": tid})
        if not r.get("ok"):
            _log(job, "rough_cut", f"cut_speaker_link 失败：{r.get('error')}", "warn")
        # 标 actions：每个 spk → delete
        actions = {str(s): "delete" for s in del_spks if s}
        r = _http_post(api, "/save_cut_decisions",
                       {"task_id": tid, "actions": actions})
        if not r.get("ok"):
            return (False, f"save_cut_decisions 失败：{r.get('error')}")
        # save_cut_decisions 默认 actions 覆盖其它 spk → 用 manual_marks 补回：
        # 设计文档没要求补非目标 spk 的 keep，按 actions = del_spks→delete（其它维持 keep）
        # 该端点设计是 actions 仅覆盖所列 spk，未列出的保留 default
    _log(job, "rough_cut", "✅ 切分修剪完成", "ok")
    return (True, "")


def handler_rough_compose(tid: str, cfg: dict, outputs_dir: Path, api: str,
                          job: PipelineJob) -> tuple[bool, str]:
    """粗剪合成阶段 — 调 /compose_rough + 等 /compose_rough_status。"""
    ok, err = _check_prereq(outputs_dir, ["subtitle.json", "revision.json"])
    if not ok:
        _log(job, "rough_compose", f"跳过：{err}", "warn")
        return (True, "skip")
    _log(job, "rough_compose", "启动粗剪合成")
    r = _http_post(api, "/compose_rough", {"task_id": tid})
    if not r.get("ok"):
        return (False, f"启动合成失败：{r.get('error')}")
    _log(job, "rough_compose", "等待合成完成（重编码约 10+ 分钟）")
    state, err = _poll_status(api, "/compose_rough_status", tid, timeout=6 * 3600.0)
    if state == "done":
        _log(job, "rough_compose", "✅ 粗剪合成完成", "ok")
        return (True, "")
    return (False, f"粗剪合成{state}：{err}")


def handler_optimize(tid: str, cfg: dict, outputs_dir: Path, api: str,
                     job: PipelineJob) -> tuple[bool, str]:
    """优化字幕阶段 — /optimize_subtitle + 等完成 + (可选) 全接受。"""
    # prereq：rough_compose.mp4
    ok, err = _check_prereq(outputs_dir, ["rough_compose.mp4"])
    if not ok:
        _log(job, "optimize", f"跳过：{err}", "warn")
        return (True, "skip")
    accept_all = bool(cfg.get("accept_all_replacements", True))
    _log(job, "optimize", "启动优化字幕分析")
    r = _http_post(api, "/optimize_subtitle",
                   {"task_id": tid, "force": True})
    if not r.get("ok"):
        return (False, f"启动优化失败：{r.get('error')}")
    _log(job, "optimize", "等待分析完成…")
    state, err = _poll_status(api, "/optimize_subtitle_status", tid, timeout=6 * 3600.0)
    if state != "done":
        return (False, f"优化字幕{state}：{err}")
    if accept_all:
        _log(job, "optimize", "应用全部替换（accepted all applied=True）")
        r = _http_post(api, "/save_optimize_subtitle",
                       {"task_id": tid, "applied_all": True})
        if not r.get("ok"):
            return (False, f"save_optimize_subtitle 失败：{r.get('error')}")
    _log(job, "optimize", "✅ 优化字幕完成", "ok")
    return (True, "")


HANDLERS: dict[str, Callable] = {
    "subtitle_generation": handler_subtitle_generation,
    "subtitle_review": handler_subtitle_review,
    "rough_cut": handler_rough_cut,
    "rough_compose": handler_rough_compose,
    "optimize": handler_optimize,
}


# =============== 主入口 ===============


def run_pipeline(tid: str, api: str, outputs_dir: Path, *,
                 since: str | None = None) -> bool:
    """启动后台守护线程跑流程；已有 running job → False。

    api = Gradio FastAPI 应用的 base URL（通常是 `http://127.0.0.1:<port>`）。
    """
    if _is_running(tid):
        return False
    _clear_stop(tid)
    job = PipelineJob(state="running", started_at=time.time(), current_stage=None, percent=0.0)
    _set_job(tid, job)

    def _run() -> None:
        try:
            cfg_path_data = load_pipeline(outputs_dir) or {}
            cfg_full = cfg_path_data.get("config") or default_config()
            # v4 顶层 stop_after：哪个阶段完成后停（None = 跑到底）
            flow_stop = cfg_full.get("stop_after")
            # since 解析：None 表示从头跑；否则「从 since 这一阶段开始」跳过更早的阶段
            since_idx = STAGE_INDEX.get(since, -1) if since else -1
            stages_done: list[str] = []
            for stage_key, stage_label, _pane in STAGE_ORDER:
                idx = STAGE_INDEX[stage_key]
                if idx < since_idx:
                    continue
                # 用户中途 stop → 立即退出
                if _consume_stop(tid):
                    _log(job, stage_key, "⏹ 用户请求停止", "warn")
                    job.state = "stopped"
                    break
                job.current_stage = stage_key
                # per-stage percent: 假设 5 阶段均匀 → 100/5=20 每阶段
                job.percent = (idx + 1) / max(len(STAGE_ORDER), 1) * 80  # 留 20% 给收尾
                _log(job, stage_key, f"== 开始阶段：{stage_label} ==")
                stage_cfg = cfg_full.get(stage_key) or {}
                handler = HANDLERS.get(stage_key)
                if handler is None:
                    _log(job, stage_key, f"未知阶段 {stage_key}，跳过", "warn")
                    continue
                try:
                    ok, msg = handler(tid, stage_cfg, outputs_dir, api, job)
                except Exception as e:  # noqa: BLE001
                    log.exception("[pipeline][%s] handler %s 抛异常", tid, stage_key)
                    job.state = "error"
                    job.error = f"{stage_label}：{e}"
                    _log(job, stage_key, f"❌ 异常：{e}", "error")
                    break
                if not ok:
                    job.state = "error"
                    job.error = msg
                    _log(job, stage_key, f"❌ {msg}", "error")
                    break
                if msg == "skip":
                    pass  # 跳过不计入 done
                else:
                    stages_done.append(stage_key)
                # v4 stop_after：顶层字段（字符串=该阶段后停；None=不停）
                if flow_stop and stage_key == flow_stop:
                    _log(job, stage_key,
                         f"⏸ 顶层配置要求「{stage_label}」后停（等人工）", "info")
                    job.state = "stopped"
                    break
            else:
                # 全部阶段正常 → done
                job.state = "done"
                _log(job, "_end", "✅ 全部阶段完成", "ok")
            job.finished_at = time.time()
            job.percent = 100.0 if job.state == "done" else job.percent
            # summary + append history
            summary = {
                "started_at": job.started_at,
                "finished_at": job.finished_at,
                "duration_ms": int((job.finished_at - (job.started_at or job.finished_at)) * 1000),
                "status": job.state,
                "stages_done": stages_done,
                "since": since,
                "error": job.error,
            }
            job.summary = summary
            append_history(outputs_dir, summary)
        except Exception as e:  # noqa: BLE001 — 后台线程兜底
            log.exception("[pipeline][%s] 调度器异常", tid)
            job.state = "error"
            job.error = str(e)
            job.finished_at = time.time()

    threading.Thread(target=_run, name=f"pipeline-{tid}", daemon=True).start()
    return True


def stop_pipeline(tid: str) -> bool:
    """请求停止（设置标志位，下次循环检查时退出）。无 job → False。"""
    if not _is_running(tid):
        return False
    _request_stop(tid)
    return True
