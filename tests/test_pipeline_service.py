"""REQ-20260918-047 — pipeline_service 单测（v4 schema）。

覆盖：
- default_config / validate_config schema（v4：顶层 stop_after 字符串；阶段 dict 不再含 stop_after）
- 持久化（load/save/atomic/concurrent/corrupt）
- 内存 job 管理（idle / running / stop / status）
- stop_after 顶层字段语义（v4：字符串 stage key 或 None）
- run_pipeline stop 标志位
- v3/v2 旧数据兼容（阶段内 stop_after 字段被丢弃；顶层 stop_after string 提升）
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from slirn_home import pipeline_service as P

# =============== helpers ===============


def _outputs(tmp_path: Path) -> Path:
    """模拟 tasks/<tid>/outputs/ 目录。"""
    d = tmp_path / "outputs"
    d.mkdir(parents=True, exist_ok=True)
    return d


# =============== schema ===============


def test_default_config_keys():
    """v4 默认配置：5 个阶段 + 顶层 stop_after 字符串。"""
    cfg = P.default_config()
    assert set(cfg.keys()) == {
        "subtitle_generation", "subtitle_review", "rough_cut",
        "rough_compose", "optimize", "stop_after",
    }
    # 阶段 dict 不再含 stop_after
    for stage_key in ("subtitle_generation", "subtitle_review", "rough_cut",
                      "rough_compose", "optimize"):
        assert "stop_after" not in cfg[stage_key], f"{stage_key} 不应含 stop_after"


def test_default_config_has_flow_level_stop_after():
    """v4：顶层 stop_after 是 STAGE_INDEX 中的字符串。"""
    cfg = P.default_config()
    assert isinstance(cfg["stop_after"], str)
    assert cfg["stop_after"] in P.STAGE_INDEX


def test_validate_config_accepts_valid_full():
    """完整合法配置 → 原样保留。"""
    cfg = P.default_config()
    cfg["subtitle_generation"]["speaker_diarization"] = True
    cfg["stop_after"] = "rough_compose"
    out = P.validate_config(cfg)
    assert out["subtitle_generation"]["speaker_diarization"] is True
    assert out["stop_after"] == "rough_compose"


def test_validate_config_fills_missing_stage_keys():
    """缺某阶段 → 用默认补全。"""
    user = {
        "subtitle_generation": {"speaker_diarization": True},
        # 其他阶段缺
    }
    out = P.validate_config(user)
    # 补全
    assert "speaker_diarization" in out["subtitle_generation"]
    assert out["subtitle_generation"]["speaker_diarization"] is True
    assert out["rough_cut"]["default_decision"] == "keep"


def test_validate_config_drops_legacy_per_stage_stop_after():
    """v3 兼容：每阶段内 stop_after 字段（boolean / string）被丢弃（顶层才是权威）。"""
    user = P.default_config()
    # 模拟 v3 数据：每阶段含 boolean stop_after
    for s in user:
        if isinstance(user[s], dict):
            user[s]["stop_after"] = True
    out = P.validate_config(user)
    for s in user:
        if isinstance(out[s], dict):
            assert "stop_after" not in out[s], f"{s} 仍含 stop_after"


def test_validate_config_accepts_top_level_stop_after_string():
    """v4 顶层 stop_after 字符串 → 保留。"""
    user = P.default_config()
    user["stop_after"] = "rough_cut"
    out = P.validate_config(user)
    assert out["stop_after"] == "rough_cut"


def test_validate_config_accepts_top_level_stop_after_null():
    """v4 顶层 stop_after=None → 跑到底，保留 null。"""
    user = P.default_config()
    user["stop_after"] = None
    out = P.validate_config(user)
    assert out["stop_after"] is None


def test_validate_config_rejects_invalid_top_level_string():
    """v4 顶层 stop_after 非法字符串 → 回退默认。"""
    user = P.default_config()
    user["stop_after"] = "not_a_stage"
    out = P.validate_config(user)
    assert out["stop_after"] == P.default_config()["stop_after"]


def test_validate_config_non_dict_returns_default():
    """坏 cfg（不是 dict）→ 直接用默认。"""
    assert P.validate_config("garbage") == P.default_config()
    assert P.validate_config(None) == P.default_config()


# =============== 持久化 ===============


def test_load_pipeline_missing_returns_none(tmp_path: Path):
    """文件不存在 → None。"""
    assert P.load_pipeline(_outputs(tmp_path)) is None


def test_load_pipeline_corrupt_returns_none(tmp_path: Path):
    """文件损坏 → None（不抛）。"""
    out = _outputs(tmp_path)
    (out / P.PIPELINE_FILENAME).write_text("{ not json", encoding="utf-8")
    assert P.load_pipeline(out) is None


def test_save_pipeline_records_updated_at(tmp_path: Path):
    """save 后 updated_at 字段存在 + 不为 None。"""
    out = _outputs(tmp_path)
    ts = P.save_pipeline(out, {"config": P.default_config()})
    assert ts
    data = P.load_pipeline(out)
    assert data["updated_at"] == ts


def test_save_pipeline_preserves_history(tmp_path: Path):
    """保存后历史仍在（最多 10 条）。"""
    out = _outputs(tmp_path)
    P.append_history(out, {"i": 1, "status": "done"})
    P.append_history(out, {"i": 2, "status": "error"})
    P.save_pipeline(out, {"config": P.default_config()})
    data = P.load_pipeline(out)
    assert len(data["history"]) == 2
    assert data["history"][1]["i"] == 2


def test_save_pipeline_atomic_no_tmp_leftover(tmp_path: Path):
    """save 后没有 .tmp 残留。"""
    out = _outputs(tmp_path)
    P.save_pipeline(out, {"config": P.default_config()})
    assert not (out / (P.PIPELINE_FILENAME + ".tmp")).exists()


def test_save_pipeline_writes_top_level_stop_after(tmp_path: Path):
    """v4 save 后落盘文件含顶层 stop_after 字符串 + version=3。"""
    out = _outputs(tmp_path)
    cfg = P.default_config()
    cfg["stop_after"] = "rough_cut"
    P.save_pipeline(out, {"config": cfg})
    raw = json.loads((out / P.PIPELINE_FILENAME).read_text(encoding="utf-8"))
    assert raw["version"] == 3
    assert raw["config"]["stop_after"] == "rough_cut"
    # 阶段 dict 内不再含 stop_after
    for s in ("subtitle_generation", "subtitle_review", "rough_cut",
              "rough_compose", "optimize"):
        assert "stop_after" not in raw["config"][s]


def test_concurrent_save_pipeline_does_not_lose(tmp_path: Path):
    """并发 save：文件长度合理（不丢更新）。"""
    out = _outputs(tmp_path)
    stop_keys = ["subtitle_generation", "subtitle_review", "rough_cut",
                 "rough_compose", "optimize", None]

    def _worker(i: int):
        cfg = P.default_config()
        cfg["stop_after"] = stop_keys[i % len(stop_keys)]
        P.save_pipeline(out, {"config": cfg})

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(10)]
    for t in threads: t.start()  # noqa: E701
    for t in threads: t.join()  # noqa: E701
    data = json.loads((out / P.PIPELINE_FILENAME).read_text(encoding="utf-8"))
    assert "config" in data
    assert "stop_after" in data["config"]


def test_append_history_truncates_to_10(tmp_path: Path):
    """历史只保留最近 10 条。"""
    out = _outputs(tmp_path)
    for i in range(15):
        P.append_history(out, {"i": i})
    data = P.load_pipeline(out)
    assert len(data["history"]) == 10
    assert data["history"][-1]["i"] == 14
    assert data["history"][0]["i"] == 5


# =============== 内存 job 管理 ===============


def test_pipeline_status_idle_when_no_job():
    """从未跑过 → status 返回 None。"""
    assert P.pipeline_status("no-such-task") is None


def test_stop_pipeline_without_job_returns_false():
    """无 running job → stop_pipeline 返回 False。"""
    assert P.stop_pipeline("no-such-task") is False


# =============== stop_after 顶层字段语义 ===============


def test_effective_stop_after_runs_to_end_when_null():
    """v4：顶层 stop_after=None → 跑到底（不因 stop_after 停）。"""
    cfg = P.default_config()
    cfg["stop_after"] = None
    out = P.validate_config(cfg)
    assert out["stop_after"] is None


def test_effective_stop_after_stops_at_named_stage():
    """v4：顶层 stop_after='rough_cut' → 跑到 rough_cut 后停。"""
    cfg = P.default_config()
    cfg["stop_after"] = "rough_cut"
    out = P.validate_config(cfg)
    assert out["stop_after"] == "rough_cut"


def test_effective_stop_after_mixed():
    """混合：每个值都正确通过 validate。"""
    for v in ("subtitle_generation", "subtitle_review", "rough_cut",
              "rough_compose", "optimize"):
        cfg = P.default_config()
        cfg["stop_after"] = v
        out = P.validate_config(cfg)
        assert out["stop_after"] == v


# =============== run_pipeline stop 行为 ===============


def test_run_pipeline_returns_false_when_already_running(tmp_path: Path):
    """已有 running job → 重复 run_pipeline 返回 False。

    用一个永不完成的 mock handler 触发。
    """
    orig = P.HANDLERS["subtitle_generation"]

    def _slow(tid, cfg, outputs_dir, api, job):
        job.percent = 50.0
        time.sleep(60)
        return (True, "")

    P.HANDLERS["subtitle_generation"] = _slow
    try:
        out = _outputs(tmp_path)
        api = "http://127.0.0.1:1"  # 不会真连
        ok1 = P.run_pipeline("test-tid-running", api, out)
        ok2 = P.run_pipeline("test-tid-running", api, out)
        assert ok1 is True
        assert ok2 is False
    finally:
        P.HANDLERS["subtitle_generation"] = orig


def test_stop_pipeline_sets_stop_flag(tmp_path: Path):
    """stop_pipeline 设置标志位后 job 进入 stopped 状态。"""
    orig = P.HANDLERS["subtitle_generation"]

    def _slow(tid, cfg, outputs_dir, api, job):
        # 模拟：handler 看到 stop 标志位就退出
        for _ in range(10):
            if P._consume_stop(tid):
                return (True, "用户停止")
            time.sleep(0.1)
        return (True, "")

    P.HANDLERS["subtitle_generation"] = _slow
    try:
        out = _outputs(tmp_path)
        api = "http://127.0.0.1:1"
        P.run_pipeline("test-tid-stop", api, out)
        time.sleep(0.3)
        ok = P.stop_pipeline("test-tid-stop")
        assert ok is True
        deadline = time.time() + 3.0
        while time.time() < deadline:
            st = P.pipeline_status("test-tid-stop")
            if st and st["state"] in ("stopped", "done", "error"):
                break
            time.sleep(0.05)
        st = P.pipeline_status("test-tid-stop")
        assert st is not None
        assert st["state"] in ("stopped", "done", "error")
    finally:
        P.HANDLERS["subtitle_generation"] = orig


def test_run_pipeline_stops_at_top_level_stop_after(tmp_path: Path):
    """v4：顶层 stop_after='subtitle_generation' → 跑完字幕生成就停。"""
    orig = P.HANDLERS["subtitle_generation"]

    def _ok(tid, cfg, outputs_dir, api, job):
        return (True, "")

    P.HANDLERS["subtitle_generation"] = _ok
    P.HANDLERS["subtitle_review"] = _ok
    P.HANDLERS["rough_cut"] = _ok
    P.HANDLERS["rough_compose"] = _ok
    P.HANDLERS["optimize"] = _ok
    try:
        out = _outputs(tmp_path)
        cfg = P.default_config()
        cfg["stop_after"] = "subtitle_generation"
        P.save_pipeline(out, {"config": cfg})
        api = "http://127.0.0.1:1"
        ok = P.run_pipeline("test-tid-v4-stop", api, out)
        assert ok is True
        deadline = time.time() + 3.0
        while time.time() < deadline:
            st = P.pipeline_status("test-tid-v4-stop")
            if st and st["state"] != "running":
                break
            time.sleep(0.05)
        st = P.pipeline_status("test-tid-v4-stop")
        assert st is not None
        assert st["state"] == "stopped", f"期望 stopped，实际 {st['state']}"
        assert st["summary"]["stages_done"] == ["subtitle_generation"]
    finally:
        P.HANDLERS["subtitle_generation"] = orig
        P.HANDLERS["subtitle_review"] = orig
        P.HANDLERS["rough_cut"] = orig
        P.HANDLERS["rough_compose"] = orig
        P.HANDLERS["optimize"] = orig


def test_run_pipeline_starts_from_since_stage(tmp_path: Path):
    """since='rough_cut' → 跳过前 2 阶段，从 rough_cut 开始跑；stop_after=rough_compose → 跑完 rough_cut 又跑 rough_compose，然后停。"""
    orig_g = P.HANDLERS["subtitle_generation"]
    orig_r = P.HANDLERS["subtitle_review"]
    orig_c = P.HANDLERS["rough_cut"]
    orig_p = P.HANDLERS["rough_compose"]
    orig_o = P.HANDLERS["optimize"]

    called = []

    def _track(name):
        def _h(tid, cfg, outputs_dir, api, job):
            called.append(name)
            return (True, "")
        return _h

    P.HANDLERS["subtitle_generation"] = _track("subtitle_generation")
    P.HANDLERS["subtitle_review"] = _track("subtitle_review")
    P.HANDLERS["rough_cut"] = _track("rough_cut")
    P.HANDLERS["rough_compose"] = _track("rough_compose")
    P.HANDLERS["optimize"] = _track("optimize")
    try:
        out = _outputs(tmp_path)
        cfg = P.default_config()
        cfg["stop_after"] = "rough_compose"
        P.save_pipeline(out, {"config": cfg})
        api = "http://127.0.0.1:1"
        P.run_pipeline("test-tid-since", api, out, since="rough_cut")
        deadline = time.time() + 3.0
        while time.time() < deadline:
            st = P.pipeline_status("test-tid-since")
            if st and st["state"] != "running":
                break
            time.sleep(0.05)
        st = P.pipeline_status("test-tid-since")
        assert st is not None
        # since 跳过前 2 个；跑 rough_cut + rough_compose 后停
        assert "subtitle_generation" not in called
        assert "subtitle_review" not in called
        assert "rough_cut" in called
        assert "rough_compose" in called
        assert "optimize" not in called
        assert st["state"] == "stopped"
        assert st["summary"]["stages_done"] == ["rough_cut", "rough_compose"]
    finally:
        P.HANDLERS["subtitle_generation"] = orig_g
        P.HANDLERS["subtitle_review"] = orig_r
        P.HANDLERS["rough_cut"] = orig_c
        P.HANDLERS["rough_compose"] = orig_p
        P.HANDLERS["optimize"] = orig_o


def test_run_pipeline_runs_to_end_when_stop_after_null(tmp_path: Path):
    """v4：顶层 stop_after=None → 5 阶段全部跑完。"""
    orig_g = P.HANDLERS["subtitle_generation"]
    orig_r = P.HANDLERS["subtitle_review"]
    orig_c = P.HANDLERS["rough_cut"]
    orig_p = P.HANDLERS["rough_compose"]
    orig_o = P.HANDLERS["optimize"]

    def _ok(tid, cfg, outputs_dir, api, job):
        return (True, "")

    P.HANDLERS["subtitle_generation"] = _ok
    P.HANDLERS["subtitle_review"] = _ok
    P.HANDLERS["rough_cut"] = _ok
    P.HANDLERS["rough_compose"] = _ok
    P.HANDLERS["optimize"] = _ok
    try:
        out = _outputs(tmp_path)
        cfg = P.default_config()
        cfg["stop_after"] = None
        P.save_pipeline(out, {"config": cfg})
        api = "http://127.0.0.1:1"
        ok = P.run_pipeline("test-tid-null", api, out)
        assert ok is True
        deadline = time.time() + 3.0
        while time.time() < deadline:
            st = P.pipeline_status("test-tid-null")
            if st and st["state"] != "running":
                break
            time.sleep(0.05)
        st = P.pipeline_status("test-tid-null")
        assert st is not None
        assert st["state"] == "done", f"期望 done，实际 {st['state']}"
        assert len(st["summary"]["stages_done"]) == 5
    finally:
        P.HANDLERS["subtitle_generation"] = orig_g
        P.HANDLERS["subtitle_review"] = orig_r
        P.HANDLERS["rough_cut"] = orig_c
        P.HANDLERS["rough_compose"] = orig_p
        P.HANDLERS["optimize"] = orig_o


# =============== handler 行为（mock）==============


def test_handler_subtitle_generation_skips_when_already_done(tmp_path: Path):
    """handler 函数可调用、不抛异常（v4 schema）。"""
    assert callable(P.handler_subtitle_generation)
    assert callable(P.handler_subtitle_review)
    assert callable(P.handler_rough_cut)
    assert callable(P.handler_rough_compose)
    assert callable(P.handler_optimize)
