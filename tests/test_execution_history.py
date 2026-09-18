"""REQ-20260918-048 — 执行历史记录（字幕生成 + 粗剪合成）。

单元层：record_start/record_finish/patch_extra/load_history 的落盘往返；
原子写（崩溃半写恢复）；并发追加（单写锁防丢失）；上限截断；
失败记录（异常分支也能落盘）。
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
SLIRN_STANDALONE = FUNCLIP_ROOT.parent / "slirn-standalone"

if str(FUNCLIP_ROOT) not in sys.path:
    sys.path.insert(0, str(FUNCLIP_ROOT))
if SLIRN_STANDALONE.exists() and str(SLIRN_STANDALONE) not in sys.path:
    sys.path.insert(0, str(SLIRN_STANDALONE))


def _outputs(tmp_path: Path) -> Path:
    d = tmp_path / "outputs"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------- 基础流程 ----------

def test_record_start_then_finish_success(tmp_path: Path):
    """启动 → 完成成功：status=success、duration_ms > 0、finished_at 已回填。"""
    from slirn_home.execution_history import (
        HISTORY_FILENAME,
        KIND_SUBTITLE_GENERATION,
        load_history,
        record_finish,
        record_start,
    )

    out = _outputs(tmp_path)
    eid = record_start(out, KIND_SUBTITLE_GENERATION, extra={"sd": True})
    assert eid.startswith("exh-")
    items = load_history(out)
    assert len(items) == 1
    assert items[0]["status"] == "running"
    assert items[0]["finished_at"] is None

    time.sleep(0.05)  # 让 duration_ms 非 0
    record_finish(out, eid, success=True)

    items = load_history(out)
    assert len(items) == 1
    it = items[0]
    assert it["status"] == "success"
    assert it["finished_at"] is not None
    assert it["duration_ms"] >= 50
    assert it["error"] == ""
    # 落盘为有效 JSON
    data = json.loads((out / HISTORY_FILENAME).read_text(encoding="utf-8"))
    assert data[0]["status"] == "success"


def test_record_finish_failed_writes_error(tmp_path: Path):
    """失败分支：error 字段写入、status=failed；error 截断到 500 字符。"""
    from slirn_home.execution_history import (
        KIND_ROUGH_COMPOSE,
        load_history,
        record_finish,
        record_start,
    )

    out = _outputs(tmp_path)
    eid = record_start(out, KIND_ROUGH_COMPOSE, extra={"intervals": 3})

    long_err = "X" * 800  # 超过 500 截断
    record_finish(out, eid, success=False, error=long_err)

    items = load_history(out)
    assert items[0]["status"] == "failed"
    assert items[0]["error"] == "X" * 500, "error 超 500 字符必须截断"


def test_record_finish_with_unknown_id_appends(tmp_path: Path):
    """record_finish 拿不到 start id → 追加一条 finish 兜底（不丢失）。"""
    from slirn_home.execution_history import load_history, record_finish

    out = _outputs(tmp_path)
    record_finish(out, "exh-missing-id", success=True)
    items = load_history(out)
    assert len(items) == 1
    assert items[0]["kind"] == "unknown"
    assert items[0]["status"] == "success"


def test_patch_extra_merges_into_running_record(tmp_path: Path):
    """patch_extra 合并覆写：原 extra 的字段保留、新字段并入。"""
    from slirn_home.execution_history import (
        KIND_SUBTITLE_GENERATION,
        load_history,
        patch_extra,
        record_finish,
        record_start,
    )

    out = _outputs(tmp_path)
    eid = record_start(out, KIND_SUBTITLE_GENERATION, extra={"sd": True, "source": "segment"})

    patch_extra(out, eid, {"segments": 14, "speakers": 2})
    record_finish(out, eid, success=True)

    items = load_history(out)
    assert items[0]["extra"] == {"sd": True, "source": "segment", "segments": 14, "speakers": 2}


# ---------- 落盘 / 鲁棒性 ----------

def test_load_history_missing_file_returns_empty(tmp_path: Path):
    """无 history 文件 → []，不抛异常。"""
    from slirn_home.execution_history import load_history

    out = _outputs(tmp_path)
    assert load_history(out) == []


def test_load_history_corrupt_file_returns_empty(tmp_path: Path):
    """损坏 JSON / 非列表 → []（容错回空态）。"""
    from slirn_home.execution_history import HISTORY_FILENAME, load_history

    out = _outputs(tmp_path)
    (out / HISTORY_FILENAME).write_text("{not json", encoding="utf-8")
    assert load_history(out) == []
    (out / HISTORY_FILENAME).write_text(json.dumps({"oops": "dict"}), encoding="utf-8")
    assert load_history(out) == []


def test_history_writes_are_atomic_no_tmp_leftover(tmp_path: Path):
    """原子写：tmp 写入后 rename，不应残留 .tmp 文件。"""
    from slirn_home.execution_history import (
        HISTORY_FILENAME,
        KIND_SUBTITLE_GENERATION,
        record_start,
    )

    out = _outputs(tmp_path)
    record_start(out, KIND_SUBTITLE_GENERATION)
    leftovers = list(out.glob("*.tmp"))
    assert leftovers == [], f"残留临时文件: {leftovers}"
    assert (out / HISTORY_FILENAME).exists()


def test_concurrent_record_start_does_not_lose(tmp_path: Path):
    """并发 20 线程同时 record_start → 最终列表应有 20 条（无丢失）。"""
    from slirn_home.execution_history import (
        KIND_SUBTITLE_GENERATION,
        load_history,
        record_start,
    )

    out = _outputs(tmp_path)
    threads = [threading.Thread(target=lambda: record_start(out, KIND_SUBTITLE_GENERATION)) for _ in range(20)]
    for t in threads: t.start()  # noqa: E701
    for t in threads: t.join()  # noqa: E701

    items = load_history(out)
    assert len(items) == 20, f"并发丢写: 期望 20 条，实际 {len(items)}"


def test_hard_limit_truncates_oldest(tmp_path: Path):
    """超过 _HARD_LIMIT（5000）→ 截掉最旧的，保留最新 N 条。"""
    from slirn_home.execution_history import (
        _HARD_LIMIT,
        KIND_SUBTITLE_GENERATION,
        load_history,
        record_start,
    )

    out = _outputs(tmp_path)
    # 直接构造超过上限的场景：手动写 5001 条 + 再 record_start 一次 → 截断到 5000
    # 用 record_start 构造 5001 条太慢；直接 patch 文件 + 再 record_start
    items = [{"id": f"fake-{i}", "kind": "fake", "started_at": float(i),
              "started_at_iso": "", "finished_at": None, "finished_at_iso": None,
              "duration_ms": None, "status": "success", "error": "", "extra": {}}
             for i in range(_HARD_LIMIT + 1)]
    from slirn_home.execution_history import HISTORY_FILENAME
    (out / HISTORY_FILENAME).write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")

    record_start(out, KIND_SUBTITLE_GENERATION)  # 触发截断

    items = load_history(out)
    assert len(items) == _HARD_LIMIT
    assert items[0]["id"] == f"fake-{2}", "截断后最旧（fake-0/1）应被剔除"


# ---------- 格式化 ----------

def test_format_duration_human_readable():
    """duration_ms → 人读字符串（< 60s / < 1h / ≥ 1h 三档）。"""
    from slirn_home.execution_history import format_duration

    assert format_duration(None) == "-"
    assert format_duration(0) == "-"
    assert format_duration(35 * 1000) == "35s"
    assert format_duration(83 * 1000) == "1m23s"
    assert format_duration(2 * 3600 * 1000 + 5 * 60 * 1000) == "2h05m"


# ---------- app 层：endpoints + 工作台渲染 ----------

def test_execution_history_endpoint(tmp_path: Path):
    """服务端 API：按 task_id 返回倒序历史。"""
    from fastapi.testclient import TestClient
    from tasklib import TaskManager

    from slirn_home import build_app
    from slirn_home.execution_history import (
        KIND_SUBTITLE_GENERATION,
        record_finish,
        record_start,
    )

    video = tmp_path / "v.mp4"
    video.write_bytes(b"v")
    mgr = TaskManager(tmp_path)
    t = mgr.create(name="hist-test", original_video=video)
    outputs_dir = mgr.tasks_dir / t.task_id / "outputs"

    eid1 = record_start(outputs_dir, KIND_SUBTITLE_GENERATION)
    time.sleep(0.01)
    eid2 = record_start(outputs_dir, KIND_SUBTITLE_GENERATION)
    record_finish(outputs_dir, eid1, success=True)
    record_finish(outputs_dir, eid2, success=False, error="OOM")

    app = build_app(repo_root=mgr.repo_root)
    client = TestClient(app.app)
    r = client.post("/slirn/api/execution_history", json={"task_id": t.task_id})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 2
    # 倒序：最新 eid2 在前
    assert items[0]["status"] == "failed" and items[0]["error"] == "OOM"
    assert items[1]["status"] == "success"


def test_render_workbench_exec_card(tmp_path: Path):
    """工作台顶部渲染执行历史卡片：折叠壳 + 倒序行 + 空态文案。"""
    from tasklib import TaskManager

    from slirn_home.app import _render_workbench
    from slirn_home.execution_history import (
        KIND_ROUGH_COMPOSE,
        KIND_SUBTITLE_GENERATION,
        record_finish,
        record_start,
    )

    video = tmp_path / "v.mp4"
    video.write_bytes(b"v")
    mgr = TaskManager(tmp_path)
    t = mgr.create(name="exec-card", original_video=video)
    outputs_dir = mgr.tasks_dir / t.task_id / "outputs"

    # 两条历史：一条成功、一条失败
    e1 = record_start(outputs_dir, KIND_SUBTITLE_GENERATION, extra={"sd": True})
    record_finish(outputs_dir, e1, success=True)
    e2 = record_start(outputs_dir, KIND_ROUGH_COMPOSE, extra={"intervals": 5})
    record_finish(outputs_dir, e2, success=False, error="no audio")

    html = _render_workbench(t.task_id, mgr)
    # 折叠壳 + 头
    assert 'data-col-key="exec:history"' in html
    assert "📜 执行历史" in html
    # 两条行：成功 / 失败
    assert "✅ 成功" in html
    assert "❌ 失败" in html
    assert "no audio" in html, "失败行的 error 必须可见"
    assert "5 区间" in html, "extra 摘要（5 区间）必须渲染"
    # 共 2 次徽章
    assert "2 次" in html


def test_render_workbench_exec_card_empty_state(tmp_path: Path):
    """无任何执行记录 → 友好空态（不显示空列表）。"""
    from tasklib import TaskManager

    from slirn_home.app import _render_workbench

    video = tmp_path / "v.mp4"
    video.write_bytes(b"v")
    mgr = TaskManager(tmp_path)
    t = mgr.create(name="exec-empty", original_video=video)

    html = _render_workbench(t.task_id, mgr)
    assert "尚无执行记录" in html
    assert "0 次" in html
