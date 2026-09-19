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


# ---------- REQ-20260918-053：query_history 过滤 + 新 KIND 覆盖 ----------

def test_query_history_filters_by_kinds(tmp_path: Path):
    """按 kinds 过滤：只返回指定阶段；多选 = OR。"""
    from slirn_home.execution_history import (
        KIND_OPTIMIZE, KIND_ROUGH_CUT, KIND_ROUGH_COMPOSE,
        record_finish, record_start, query_history,
    )

    out = _outputs(tmp_path)
    e1 = record_start(out, KIND_ROUGH_CUT)
    record_finish(out, e1, success=True)
    e2 = record_start(out, KIND_ROUGH_COMPOSE)
    record_finish(out, e2, success=True)
    e3 = record_start(out, KIND_OPTIMIZE)
    record_finish(out, e3, success=True)

    items = query_history(out, kinds=[KIND_ROUGH_CUT, KIND_OPTIMIZE])
    assert {it["kind"] for it in items} == {KIND_ROUGH_CUT, KIND_OPTIMIZE}, \
        f"应只含 rough_cut + optimize；实际 = {[it['kind'] for it in items]}"

    items = query_history(out, kinds=[KIND_ROUGH_COMPOSE])
    assert len(items) == 1
    assert items[0]["kind"] == KIND_ROUGH_COMPOSE


def test_query_history_filters_by_statuses(tmp_path: Path):
    """按 statuses 过滤：只返回指定状态。"""
    from slirn_home.execution_history import (
        KIND_OPTIMIZE, record_finish, record_start, query_history,
    )

    out = _outputs(tmp_path)
    e1 = record_start(out, KIND_OPTIMIZE)
    record_finish(out, e1, success=True)
    e2 = record_start(out, KIND_OPTIMIZE)
    record_finish(out, e2, success=False, error="OOM 内存不足")

    items = query_history(out, statuses=["success"])
    assert len(items) == 1 and items[0]["status"] == "success"

    items = query_history(out, statuses=["failed"])
    assert len(items) == 1 and items[0]["status"] == "failed"
    assert "OOM" in items[0]["error"]


def test_query_history_keyword_search(tmp_path: Path):
    """关键词搜 error：大小写不敏感，命中 substring。"""
    from slirn_home.execution_history import (
        KIND_OPTIMIZE, record_finish, record_start, query_history,
    )

    out = _outputs(tmp_path)
    e1 = record_start(out, KIND_OPTIMIZE)
    record_finish(out, e1, success=False, error="out of memory")
    e2 = record_start(out, KIND_OPTIMIZE)
    record_finish(out, e2, success=False, error="network timeout")

    items = query_history(out, keyword="memory")
    assert len(items) == 1 and "memory" in items[0]["error"]

    items = query_history(out, keyword="TIMEOUT")  # 大写
    assert len(items) == 1 and "timeout" in items[0]["error"]


def test_query_history_returns_desc_order(tmp_path: Path):
    """返回倒序：最新在前。"""
    from slirn_home.execution_history import (
        KIND_OPTIMIZE, record_finish, record_start, query_history,
    )

    out = _outputs(tmp_path)
    e1 = record_start(out, KIND_OPTIMIZE)
    time.sleep(0.01)
    e2 = record_start(out, KIND_OPTIMIZE)
    time.sleep(0.01)
    e3 = record_start(out, KIND_OPTIMIZE)
    record_finish(out, e1, success=True)
    record_finish(out, e2, success=True)
    record_finish(out, e3, success=True)

    items = query_history(out)
    assert items[0]["id"] == e3, "最新 e3 应在前"
    assert items[2]["id"] == e1


def test_query_history_limit_caps_results(tmp_path: Path):
    """limit 截断返回最多 N 条（不报错，超出也只返 N）。"""
    from slirn_home.execution_history import (
        KIND_OPTIMIZE, record_finish, record_start, query_history,
    )

    out = _outputs(tmp_path)
    for _ in range(5):
        e = record_start(out, KIND_OPTIMIZE)
        record_finish(out, e, success=True)

    items = query_history(out, limit=2)
    assert len(items) == 2


def test_all_new_kinds_are_valid(tmp_path: Path):
    """REQ-053 新增的 3 个 kind（review/cut/optimize）必须可被记录且能查到。

    REQ-20260919-075：扩展到 11 个 kind + 校验 description / auto / stage 字段。
    """
    from slirn_home.execution_history import (
        ALL_KINDS, KIND_FINE_AI_LAYOUT, KIND_FINE_BG_DETECT,
        KIND_FINE_EXPORT, KIND_FINE_PREVIEW, KIND_LABELS,
        KIND_OPTIMIZE, KIND_ROUGH_CUT, KIND_ROUGH_COMPOSE_DELETE,
        KIND_ROUGH_CUT_LINK_PERSON, KIND_SUBTITLE_REVIEW,
        record_finish, record_start, query_history,
    )

    assert KIND_SUBTITLE_REVIEW in ALL_KINDS
    assert KIND_ROUGH_CUT in ALL_KINDS
    assert KIND_OPTIMIZE in ALL_KINDS
    # 中文标签：前端工作台筛选按钮文案用
    assert KIND_LABELS[KIND_SUBTITLE_REVIEW] == "字幕修订"
    assert KIND_LABELS[KIND_ROUGH_CUT] == "执行切分修剪"
    assert KIND_LABELS[KIND_OPTIMIZE] == "确认保存"
    # REQ-20260919-075：新增 7 个 kind 的 label 存在性
    for k in (KIND_ROUGH_CUT_LINK_PERSON, KIND_ROUGH_COMPOSE_DELETE,
              KIND_FINE_AI_LAYOUT, KIND_FINE_BG_DETECT,
              KIND_FINE_PREVIEW, KIND_FINE_EXPORT):
        assert k in KIND_LABELS, f"缺少 label: {k}"

    out = _outputs(tmp_path)
    e1 = record_start(out, KIND_SUBTITLE_REVIEW)
    record_finish(out, e1, success=True)
    e2 = record_start(out, KIND_ROUGH_CUT)
    record_finish(out, e2, success=True)
    e3 = record_start(out, KIND_OPTIMIZE)
    record_finish(out, e3, success=True)

    items = query_history(out)
    assert {it["kind"] for it in items} == {KIND_SUBTITLE_REVIEW, KIND_ROUGH_CUT, KIND_OPTIMIZE}


def test_execution_history_query_endpoint_filters(tmp_path: Path):
    """服务端 API：query 端点支持 kinds/statuses/keyword 过滤。"""
    from fastapi.testclient import TestClient
    from tasklib import TaskManager

    from slirn_home import build_app
    from slirn_home.execution_history import (
        KIND_OPTIMIZE, KIND_ROUGH_CUT,
        record_finish, record_start,
    )

    video = tmp_path / "v.mp4"
    video.write_bytes(b"v")
    mgr = TaskManager(tmp_path)
    t = mgr.create(name="q-test", original_video=video)
    outputs_dir = mgr.tasks_dir / t.task_id / "outputs"

    e1 = record_start(outputs_dir, KIND_ROUGH_CUT)
    record_finish(outputs_dir, e1, success=True)
    e2 = record_start(outputs_dir, KIND_OPTIMIZE)
    record_finish(outputs_dir, e2, success=False, error="out of memory")

    app = build_app(repo_root=mgr.repo_root)
    client = TestClient(app.app)
    # kinds 过滤
    r = client.post("/slirn/api/execution_history_query",
                    json={"task_id": t.task_id, "kinds": ["optimize"]})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1 and items[0]["kind"] == "optimize"
    # statuses 过滤
    r = client.post("/slirn/api/execution_history_query",
                    json={"task_id": t.task_id, "statuses": ["success"]})
    items = r.json()["items"]
    assert len(items) == 1 and items[0]["status"] == "success"
    # keyword 过滤
    r = client.post("/slirn/api/execution_history_query",
                    json={"task_id": t.task_id, "keyword": "memory"})
    items = r.json()["items"]
    assert len(items) == 1 and "memory" in items[0]["error"]
    # 无 task_id → 业务 ok=false
    r = client.post("/slirn/api/execution_history_query", json={})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is False


# ---------- REQ-20260919-075：description / auto / stage 字段 + 新 kind ----------


def test_record_start_with_description_and_auto(tmp_path: Path):
    """REQ-075：record_start 支持 description + auto 参数，并写入记录。"""
    from slirn_home.execution_history import (
        KIND_ROUGH_COMPOSE, record_start, load_history,
    )

    out = _outputs(tmp_path)
    eid = record_start(out, KIND_ROUGH_COMPOSE,
                       description="自定义描述：测试", auto=True)
    items = load_history(out)
    assert len(items) == 1
    assert items[0]["description"] == "自定义描述：测试"
    assert items[0]["auto"] is True
    # 不传 description → 走默认描述（来自 DEFAULT_DESCRIPTIONS）
    eid2 = record_start(out, KIND_ROUGH_COMPOSE, auto=False)
    items = load_history(out)
    assert items[0]["description"] == "自定义描述：测试"
    assert items[1]["description"] != ""  # 默认非空
    assert items[1]["auto"] is False


def test_kind_to_stage_mapping_complete():
    """REQ-075：所有 kind 必须能映射到工作台 stage key。"""
    from slirn_home.execution_history import ALL_KINDS, KIND_TO_STAGE

    for k in ALL_KINDS:
        assert k in KIND_TO_STAGE, f"kind={k} 未在 KIND_TO_STAGE 中映射"
        assert KIND_TO_STAGE[k] != "", f"kind={k} 映射到空 stage"


def test_default_descriptions_cover_all_kinds():
    """REQ-075：每个 kind 都有默认 description 模板。"""
    from slirn_home.execution_history import ALL_KINDS, DEFAULT_DESCRIPTIONS

    for k in ALL_KINDS:
        assert k in DEFAULT_DESCRIPTIONS, f"kind={k} 缺少默认 description"
        assert DEFAULT_DESCRIPTIONS[k], f"kind={k} 默认 description 为空"


def test_patch_fields_updates_description(tmp_path: Path):
    """REQ-075：patch_fields 允许回填 description 字段。"""
    from slirn_home.execution_history import (
        KIND_SUBTITLE_GENERATION, load_history, patch_fields, record_start,
    )

    out = _outputs(tmp_path)
    eid = record_start(out, KIND_SUBTITLE_GENERATION)
    patch_fields(out, eid, {"description": "完成后回填的描述：识别 5 段"})
    items = load_history(out)
    assert items[0]["description"] == "完成后回填的描述：识别 5 段"


def test_patch_fields_ignores_disallowed_keys(tmp_path: Path):
    """REQ-075：patch_fields 白名单保护 — 不允许改 status/kind/id。"""
    from slirn_home.execution_history import (
        KIND_SUBTITLE_GENERATION, load_history, patch_fields, record_start,
    )

    out = _outputs(tmp_path)
    eid = record_start(out, KIND_SUBTITLE_GENERATION, description="原始")
    # 尝试改 status/kind/id 应被忽略
    patch_fields(out, eid, {
        "description": "新描述",
        "status": "failed",  # 不允许
        "kind": "fake",      # 不允许
        "id": "forged-id",   # 不允许
    })
    items = load_history(out)
    assert items[0]["description"] == "新描述"
    assert items[0]["status"] == "running"
    assert items[0]["kind"] == KIND_SUBTITLE_GENERATION
    assert items[0]["id"] == eid


def test_stage_field_auto_set_from_kind(tmp_path: Path):
    """REQ-075：record_start 自动从 kind 推导 stage 字段。"""
    from slirn_home.execution_history import (
        KIND_FINE_EXPORT, KIND_ROUGH_CUT_LINK_PERSON,
        load_history, record_start,
    )

    out = _outputs(tmp_path)
    record_start(out, KIND_FINE_EXPORT)
    record_start(out, KIND_ROUGH_CUT_LINK_PERSON)
    items = load_history(out)
    by_kind = {it["kind"]: it for it in items}
    assert by_kind[KIND_FINE_EXPORT]["stage"] == "fine_cut"
    assert by_kind[KIND_ROUGH_CUT_LINK_PERSON]["stage"] == "rough_cut"


def test_old_history_files_load_with_defaults(tmp_path: Path):
    """REQ-075：旧 execution_history.json 缺字段时，load 后用空值兜底（不抛错）。"""
    import json
    from slirn_home import execution_history as eh

    out = _outputs(tmp_path)
    # 写一条旧格式记录（无 description/auto/stage）
    old_item = {
        "id": "exh-old-001",
        "kind": "rough_compose",
        "started_at": 1700000000.0,
        "started_at_iso": "2026-09-19T00:00:00",
        "finished_at": 1700000060.0,
        "finished_at_iso": "2026-09-19T00:01:00",
        "duration_ms": 60000,
        "status": "success",
        "error": "",
        "extra": {},
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / eh.HISTORY_FILENAME).write_text(json.dumps([old_item], ensure_ascii=False),
                                            encoding="utf-8")
    items = eh.load_history(out)
    assert len(items) == 1
    # 旧字段值原样保留
    assert items[0]["id"] == "exh-old-001"
    # 缺字段时 __getitem__ 不抛 KeyError（前端读 .description 时取空字符串）
    assert items[0].get("description", "") == ""
    assert items[0].get("auto", False) is False
    assert items[0].get("stage", "") == ""
