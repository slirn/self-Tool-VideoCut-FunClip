"""REQ-20260918-048 — 执行历史记录（生成字幕 / 生成粗剪视频）。

单文件落盘 `tasks/<tid>/outputs/execution_history.json`；列表追加、单写锁防并发。
每次执行都有一条记录：起始/截止时间、时长、状态（running/success/failed）、错误信息。
服务重启后历史仍在（落盘而非内存）。

供 app 层的 `gen_subtitle` / `compose_rough` 启动时 record_start，完成/失败时 record_finish。
工作台面板展示用 load_history() 取倒序最近 N 条。
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# 落盘文件名（与 tasklib 解耦：tasklib 不感知 history 文件，删除任务整体 rm 即可）
HISTORY_FILENAME = "execution_history.json"

# 历史条数上限 — 单文件最大 1MB 量级（按每条约 200 字节估算 → 5000 条），远高于现实预期
_HARD_LIMIT = 5000

# 模块级单写锁（跨任务共享；同一时刻只有一个写者 + 多读）
_WRITE_LOCK = threading.Lock()

# 已知 kind 常量（便于 app 层枚举）
KIND_SUBTITLE_GENERATION = "subtitle_generation"
KIND_ROUGH_COMPOSE = "rough_compose"


def _now_iso(ts: float) -> str:
    """float → ISO8601 字符串（含本地时区偏移）。"""
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _outputs_dir(tid: str) -> Path:
    """历史落盘目录：tasks/<tid>/outputs/。调用方传完整 path 时由 record_* 内部解析。"""
    raise NotImplementedError("使用 history_for_outputs(outputs_dir) 显式传目录")


def _read(outputs_dir: Path) -> list[dict]:
    """读取历史；文件不存在或损坏 → 返回空列表。"""
    path = outputs_dir / HISTORY_FILENAME
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        return data
    except (OSError, json.JSONDecodeError) as e:
        log.warning("[history][%s] 读取失败，当作空: %s", outputs_dir, e)
        return []


def _write_atomic(outputs_dir: Path, items: list[dict]) -> None:
    """原子写：先写 .tmp 再 rename，避免崩溃半写。"""
    outputs_dir.mkdir(parents=True, exist_ok=True)
    tmp = outputs_dir / (HISTORY_FILENAME + ".tmp")
    payload = json.dumps(items, ensure_ascii=False, indent=2)
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(outputs_dir / HISTORY_FILENAME)


def record_start(outputs_dir: Path, kind: str, extra: dict | None = None) -> str:
    """记录一次执行启动（status=running）。返回本次 execution id（start 时生成，
    finish 时按 id 定位同一记录）。

    失败也吞掉（历史记录失败不影响主流程）。
    """
    try:
        ts = time.time()
        item: dict[str, Any] = {
            "id": "exh-" + datetime.fromtimestamp(ts).strftime("%Y%m%d-%H%M%S")
                  + "-" + uuid.uuid4().hex[:6],
            "kind": kind,
            "started_at": ts,
            "started_at_iso": _now_iso(ts),
            "finished_at": None,
            "finished_at_iso": None,
            "duration_ms": None,
            "status": "running",
            "error": "",
            "extra": extra or {},
        }
        with _WRITE_LOCK:
            items = _read(outputs_dir)
            items.append(item)
            # 截断最旧的避免单文件无限增长
            if len(items) > _HARD_LIMIT:
                items = items[-_HARD_LIMIT:]
            _write_atomic(outputs_dir, items)
        return item["id"]
    except Exception as e:  # noqa: BLE001 — 历史写失败不影响主流程
        log.warning("[history][%s] record_start 失败: %s", outputs_dir, e)
        return ""


def record_finish(outputs_dir: Path, exec_id: str, *,
                  success: bool, error: str = "") -> None:
    """记录一次执行完成（成功/失败）。按 id 定位同一记录并回填 finished_at/duration/status。"""
    if not exec_id:
        return  # record_start 失败时返回了空 id；不写
    try:
        ts = time.time()
        with _WRITE_LOCK:
            items = _read(outputs_dir)
            for it in items:
                if it.get("id") == exec_id:
                    it["finished_at"] = ts
                    it["finished_at_iso"] = _now_iso(ts)
                    if it.get("started_at"):
                        it["duration_ms"] = int((ts - float(it["started_at"])) * 1000)
                    it["status"] = "success" if success else "failed"
                    if not success:
                        it["error"] = (error or "")[:500]  # 截断避免异常对象塞满文件
                    break
            else:
                # 找不到对应 start 记录 → 追加一条 finish（兜底，正常不会进）
                log.warning("[history][%s] record_finish 找不到 id=%s 的启动记录", outputs_dir, exec_id)
                items.append({
                    "id": exec_id, "kind": "unknown",
                    "started_at": ts, "started_at_iso": _now_iso(ts),
                    "finished_at": ts, "finished_at_iso": _now_iso(ts),
                    "duration_ms": 0,
                    "status": "success" if success else "failed",
                    "error": (error or "")[:500],
                    "extra": {},
                })
            if len(items) > _HARD_LIMIT:
                items = items[-_HARD_LIMIT:]
            _write_atomic(outputs_dir, items)
    except Exception as e:  # noqa: BLE001
        log.warning("[history][%s] record_finish 失败: %s", outputs_dir, e)


def patch_extra(outputs_dir: Path, exec_id: str, extra: dict) -> None:
    """合并覆写指定记录的 extra（用于完成时补 segments / speakers 等结果摘要）。"""
    if not exec_id or not extra:
        return
    try:
        with _WRITE_LOCK:
            items = _read(outputs_dir)
            for it in items:
                if it.get("id") == exec_id:
                    it["extra"] = {**(it.get("extra") or {}), **extra}
                    break
            _write_atomic(outputs_dir, items)
    except Exception as e:  # noqa: BLE001
        log.warning("[history][%s] patch_extra 失败: %s", outputs_dir, e)


def load_history(outputs_dir: Path) -> list[dict]:
    """读全量历史（不截断，调用方决定展示多少条）。"""
    with _WRITE_LOCK:
        return _read(outputs_dir)


def format_duration(ms: int | None) -> str:
    """duration_ms → 人读字符串（35s / 1m23s / 0s）。"""
    if not ms or ms < 0:
        return "-"
    s = ms // 1000
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"
