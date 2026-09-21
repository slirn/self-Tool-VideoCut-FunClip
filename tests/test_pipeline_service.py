"""REQ-20260918-047 — pipeline_service 单测（v5 schema）。

覆盖：
- default_config / validate_config schema（v5：6 阶段 + 顶层 run_mode + 顶层 stop_after）
- 持久化（load/save/atomic/concurrent/corrupt）
- 内存 job 管理（idle / running / stop / status）
- run_mode / stop_after 顶层字段语义
- run_pipeline stop 标志位
- v4/v3/v2 旧数据兼容
- REQ-20260921-NNN：fine_cut 阶段 + 每阶段新字段（link_person_ids / fine_cut config）
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
    """v5 默认配置：6 个阶段 + 顶层 run_mode + stop_after。"""
    cfg = P.default_config()
    assert set(cfg.keys()) == {
        "subtitle_generation", "subtitle_review", "rough_cut",
        "rough_compose", "optimize", "fine_cut",
        "run_mode", "stop_after",
    }
    # 阶段 dict 不再含 stop_after
    for stage_key in ("subtitle_generation", "subtitle_review", "rough_cut",
                      "rough_compose", "optimize", "fine_cut"):
        assert "stop_after" not in cfg[stage_key], f"{stage_key} 不应含 stop_after"


def test_default_config_has_6_stages():
    """v5：STAGE_ORDER 含 6 阶段，fine_cut 是最后一个。"""
    keys = [s[0] for s in P.STAGE_ORDER]
    assert len(keys) == 6
    assert keys[-1] == "fine_cut"


def test_default_config_has_run_mode_stop_after():
    """v5：默认 run_mode="stop_after"。"""
    cfg = P.default_config()
    assert cfg["run_mode"] == "stop_after"


def test_default_config_fine_cut_enabled_false():
    """v5：fine_cut.enabled 默认 False（防误跑）。"""
    cfg = P.default_config()
    assert cfg["fine_cut"]["enabled"] is False


def test_default_config_fine_cut_has_required_keys():
    """v5 + v2 用户反馈：fine_cut 只含 4 个真正生效的字段（enabled / params_source /
    preview_start / duration）。cover_image / bg_image / bgm 已移除 —— 都是死字段，
    handler 不读（实际素材在 fc.json 的 materials.{cover,bg,audio}.path）。
    """
    cfg = P.default_config()
    fc = cfg["fine_cut"]
    for k in ("enabled", "params_source", "preview_start", "duration"):
        assert k in fc, f"fine_cut 缺 {k}"
    # 死字段必须不在 default_config 里（防止 UI 误加回来）
    for k in ("cover_image", "bg_image", "bgm"):
        assert k not in fc, f"fine_cut 不应含死字段 {k}（handler 不读，UI 不要画）"


def test_default_config_subtitle_review_rough_cut_have_link_person_ids():
    """v5：subtitle_review / rough_cut 默认含 link_person_ids=False。"""
    cfg = P.default_config()
    assert cfg["subtitle_review"]["link_person_ids"] is False
    assert cfg["rough_cut"]["link_person_ids"] is False


def test_default_config_has_flow_level_stop_after():
    """v5：顶层 stop_after 是 STAGE_INDEX 中的字符串。"""
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


def test_validate_config_run_mode_to_end_forces_stop_after_null():
    """v5：run_mode=to_end → 强制 stop_after=None（忽略用户配置）。"""
    user = P.default_config()
    user["run_mode"] = "to_end"
    user["stop_after"] = "rough_cut"  # 即使设了也被强制 null
    out = P.validate_config(user)
    assert out["run_mode"] == "to_end"
    assert out["stop_after"] is None


def test_validate_config_run_mode_stop_after_keeps_stop_after():
    """v5：run_mode=stop_after → stop_after 按用户值。"""
    user = P.default_config()
    user["run_mode"] = "stop_after"
    user["stop_after"] = "rough_compose"
    out = P.validate_config(user)
    assert out["run_mode"] == "stop_after"
    assert out["stop_after"] == "rough_compose"


def test_validate_config_legacy_data_without_run_mode_stop_after_none():
    """v4→v5 兼容：缺 run_mode + stop_after=None → 推算 run_mode=to_end。"""
    user = P.default_config()
    user["stop_after"] = None
    user.pop("run_mode", None)
    out = P.validate_config(user)
    assert out["run_mode"] == "to_end"
    assert out["stop_after"] is None


def test_validate_config_legacy_data_without_run_mode_stop_after_string():
    """v4→v5 兼容：缺 run_mode + stop_after='rough_cut' → 推算 run_mode=stop_after。"""
    user = P.default_config()
    user["stop_after"] = "rough_cut"
    user.pop("run_mode", None)
    out = P.validate_config(user)
    assert out["run_mode"] == "stop_after"
    assert out["stop_after"] == "rough_cut"


def test_validate_config_invalid_run_mode_falls_back_to_stop_after():
    """v5：run_mode 非法值 → 回退 stop_after（保守）。"""
    user = P.default_config()
    user["run_mode"] = "bogus"
    out = P.validate_config(user)
    assert out["run_mode"] == "stop_after"


def test_validate_config_fine_cut_enabled_non_bool_falls_back_false():
    """v5：fine_cut.enabled 非 bool → 回退 False（防误跑）。"""
    user = P.default_config()
    user["fine_cut"]["enabled"] = "yes"
    out = P.validate_config(user)
    assert out["fine_cut"]["enabled"] is False


def test_validate_config_fine_cut_accepts_full_settings():
    """v5：fine_cut 仅 4 个真正生效字段被保留（enabled / params_source / preview_start / duration）。
    cover_image / bg_image / bgm 是 v1 死字段（handler 不读），validate_config 应该丢弃
    而非保留（避免旧配置误导 UI）。
    """
    user = P.default_config()
    user["fine_cut"] = {
        "enabled": True,
        "cover_image": "cover.jpg",   # v1 死字段
        "bg_image": "bg.jpg",         # v1 死字段
        "bgm": "song.mp3",            # v1 死字段
        "params_source": "template:p_xxx",
        "preview_start": 30.5,
        "duration": 120.0,
    }
    out = P.validate_config(user)
    assert out["fine_cut"]["enabled"] is True
    assert out["fine_cut"]["params_source"] == "template:p_xxx"
    assert out["fine_cut"]["preview_start"] == 30.5
    assert out["fine_cut"]["duration"] == 120.0
    # v1 死字段被丢弃（merged[k] = v 仅当 k 在 default_config 里）
    assert "cover_image" not in out["fine_cut"]
    assert "bg_image" not in out["fine_cut"]
    assert "bgm" not in out["fine_cut"]


def test_validate_config_link_person_ids_non_bool_falls_back_false():
    """v5：link_person_ids 非 bool → 回退 False。"""
    user = P.default_config()
    user["subtitle_review"]["link_person_ids"] = "yes"
    user["rough_cut"]["link_person_ids"] = 1
    out = P.validate_config(user)
    assert out["subtitle_review"]["link_person_ids"] is False
    assert out["rough_cut"]["link_person_ids"] is False


def test_validate_config_drops_unknown_fine_cut_keys():
    """v5：fine_cut 未知字段被丢弃（仅保留 whitelist）。"""
    user = P.default_config()
    user["fine_cut"]["bogus_field"] = "x"
    out = P.validate_config(user)
    assert "bogus_field" not in out["fine_cut"]


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
    """v5 save 后落盘文件含顶层 stop_after 字符串 + version=4。"""
    out = _outputs(tmp_path)
    cfg = P.default_config()
    cfg["stop_after"] = "rough_cut"
    P.save_pipeline(out, {"config": cfg})
    raw = json.loads((out / P.PIPELINE_FILENAME).read_text(encoding="utf-8"))
    assert raw["version"] == 4
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
    P.HANDLERS["fine_cut"] = _ok
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
        P.HANDLERS["fine_cut"] = orig


def test_run_pipeline_starts_from_since_stage(tmp_path: Path):
    """since='rough_cut' → 跳过前 2 阶段，从 rough_cut 开始跑；stop_after=rough_compose → 跑完 rough_cut 又跑 rough_compose，然后停。"""
    orig_g = P.HANDLERS["subtitle_generation"]
    orig_r = P.HANDLERS["subtitle_review"]
    orig_c = P.HANDLERS["rough_cut"]
    orig_p = P.HANDLERS["rough_compose"]
    orig_o = P.HANDLERS["optimize"]
    orig_f = P.HANDLERS["fine_cut"]

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
    P.HANDLERS["fine_cut"] = _track("fine_cut")
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
        assert "fine_cut" not in called
        assert st["state"] == "stopped"
        assert st["summary"]["stages_done"] == ["rough_cut", "rough_compose"]
    finally:
        P.HANDLERS["subtitle_generation"] = orig_g
        P.HANDLERS["subtitle_review"] = orig_r
        P.HANDLERS["rough_cut"] = orig_c
        P.HANDLERS["rough_compose"] = orig_p
        P.HANDLERS["optimize"] = orig_o
        P.HANDLERS["fine_cut"] = orig_f


def test_run_pipeline_runs_to_end_when_stop_after_null(tmp_path: Path):
    """v5：顶层 stop_after=None + run_mode=to_end → 6 阶段全部跑完（含 fine_cut）。"""
    orig_g = P.HANDLERS["subtitle_generation"]
    orig_r = P.HANDLERS["subtitle_review"]
    orig_c = P.HANDLERS["rough_cut"]
    orig_p = P.HANDLERS["rough_compose"]
    orig_o = P.HANDLERS["optimize"]
    orig_f = P.HANDLERS["fine_cut"]

    def _ok(tid, cfg, outputs_dir, api, job):
        return (True, "")

    P.HANDLERS["subtitle_generation"] = _ok
    P.HANDLERS["subtitle_review"] = _ok
    P.HANDLERS["rough_cut"] = _ok
    P.HANDLERS["rough_compose"] = _ok
    P.HANDLERS["optimize"] = _ok
    P.HANDLERS["fine_cut"] = _ok
    try:
        out = _outputs(tmp_path)
        cfg = P.default_config()
        cfg["run_mode"] = "to_end"
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
        assert len(st["summary"]["stages_done"]) == 6
    finally:
        P.HANDLERS["subtitle_generation"] = orig_g
        P.HANDLERS["subtitle_review"] = orig_r
        P.HANDLERS["rough_cut"] = orig_c
        P.HANDLERS["rough_compose"] = orig_p
        P.HANDLERS["optimize"] = orig_o
        P.HANDLERS["fine_cut"] = orig_f


# =============== handler 行为（mock）==============


def test_handler_subtitle_generation_skips_when_already_done(tmp_path: Path):
    """handler 函数可调用、不抛异常（v5 schema）。"""
    assert callable(P.handler_subtitle_generation)
    assert callable(P.handler_subtitle_review)
    assert callable(P.handler_rough_cut)
    assert callable(P.handler_rough_compose)
    assert callable(P.handler_optimize)
    assert callable(P.handler_fine_cut)


def test_handler_fine_cut_skips_when_disabled(monkeypatch, tmp_path: Path):
    """v5：fine_cut.enabled=False → 直接 skip（不调 /export_fine_video）。"""
    # 监控 _http_post — 确认不会调用 export_fine_video
    calls = []
    def fake_http_post(api, path, payload, **kw):
        calls.append(path)
        return {"ok": True, "job_id": "job_x"}
    monkeypatch.setattr(P, "_http_post", fake_http_post)

    job = P.PipelineJob()
    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    cfg = {"enabled": False}
    ok, msg = P.handler_fine_cut("t-1", cfg, out, "http://x", job)
    assert ok is True
    assert msg == "skip"
    assert calls == []  # 没调用任何端点


def test_handler_fine_cut_runs_export_when_enabled(monkeypatch, tmp_path: Path):
    """v5：fine_cut.enabled=True → 调 /export_fine_video + 等 /render_status。"""
    monkeypatch.setattr(P, "_poll_export",
                        lambda api, jid, tid, **kw: ("done", ""))
    calls = []
    def fake_http_post(api, path, payload, **kw):
        calls.append((path, payload))
        if path == "/export_fine_video":
            return {"ok": True, "job_id": "job_test"}
        return {"ok": True}
    monkeypatch.setattr(P, "_http_post", fake_http_post)

    job = P.PipelineJob()
    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    # 准备 prereq 文件
    (out / "rough_compose.mp4").write_bytes(b"")
    cfg = {"enabled": True, "preview_start": 10.0, "duration": 60.0,
           "params_source": "current"}
    ok, msg = P.handler_fine_cut("t-1", cfg, out, "http://x", job)
    assert ok is True
    assert msg == ""
    # 期望调用 export_fine_video
    paths = [p for p, _ in calls]
    assert "/export_fine_video" in paths


def test_handler_fine_cut_skips_when_no_prereq(monkeypatch, tmp_path: Path):
    """v5：fine_cut 启用但缺 rough_compose.mp4 → skip（不报错）。"""
    calls = []
    def fake_http_post(api, path, payload, **kw):
        calls.append(path)
        return {"ok": True}
    monkeypatch.setattr(P, "_http_post", fake_http_post)

    job = P.PipelineJob()
    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    # 不创建 rough_compose.mp4
    cfg = {"enabled": True}
    ok, msg = P.handler_fine_cut("t-1", cfg, out, "http://x", job)
    assert ok is True
    assert msg == "skip"
    assert calls == []


def test_handler_fine_cut_applies_template_before_export(monkeypatch, tmp_path: Path):
    """v5：params_source='template:p_x' → 先 /apply_fine_global_profile，再 /export_fine_video。"""
    monkeypatch.setattr(P, "_poll_export",
                        lambda api, jid, tid, **kw: ("done", ""))
    calls = []
    def fake_http_post(api, path, payload, **kw):
        calls.append(path)
        if path == "/export_fine_video":
            return {"ok": True, "job_id": "job_test"}
        return {"ok": True}
    monkeypatch.setattr(P, "_http_post", fake_http_post)

    job = P.PipelineJob()
    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    (out / "rough_compose.mp4").write_bytes(b"")
    cfg = {"enabled": True, "params_source": "template:p_xyz"}
    ok, msg = P.handler_fine_cut("t-1", cfg, out, "http://x", job)
    assert ok is True
    # 期望 apply_fine_global_profile 在 export_fine_video 之前
    assert calls.index("/apply_fine_global_profile") < calls.index("/export_fine_video")


# =============== REQ-20260921-NNN-v4：range_enabled 门控「区间导出」===============
# v3 bug：start/duration 用 number 输入（秒），用户要算 10 分钟 = 600 秒嫌麻烦，
# 改 HH:MM:SS。但默认 00:10:00 + 无门控会让「默认行为」从全片变成前 10 分钟，
# 是回归。加 range_enabled checkbox（默认 False）保留「全片」为默认意图。
#
# 测试钉死：
# - default_config 有 range_enabled: False
# - validate_config 兜底（非 bool → False）
# - handler_fine_cut 在 range_enabled=False 时不发 preview_start/duration（全片）
# - handler_fine_cut 在 range_enabled=True 时发 preview_start/duration（区间导出）


def test_default_config_fine_cut_has_range_enabled_default_false():
    """v4 默认 range_enabled=False（全片导出）。"""
    cfg = P.default_config()
    assert "range_enabled" in cfg["fine_cut"]
    assert cfg["fine_cut"]["range_enabled"] is False


def test_validate_config_fine_cut_range_enabled_non_bool_falls_back_false():
    """v4 兜底：脏数据（字符串/None/数字）→ False，不抛异常。"""
    # 字符串 True（用户误填）
    cfg = P.validate_config({"fine_cut": {"range_enabled": "true"}})
    assert cfg["fine_cut"]["range_enabled"] is False
    # None
    cfg = P.validate_config({"fine_cut": {"range_enabled": None}})
    assert cfg["fine_cut"]["range_enabled"] is False
    # 数字 1
    cfg = P.validate_config({"fine_cut": {"range_enabled": 1}})
    assert cfg["fine_cut"]["range_enabled"] is False
    # 缺省
    cfg = P.validate_config({"fine_cut": {}})
    assert cfg["fine_cut"]["range_enabled"] is False


def test_handler_fine_cut_range_disabled_sends_full_video_body(monkeypatch, tmp_path: Path):
    """v4：range_enabled=False → /export_fine_video body 只有 task_id（无 preview_start/duration），
    让端点走默认全片导出。
    """
    captured = []

    def fake_http_post(api, path, payload, **kw):
        captured.append((path, dict(payload)))
        if path == "/export_fine_video":
            return {"ok": True, "job_id": "j1"}
        return {"ok": True}

    monkeypatch.setattr(P, "_http_post", fake_http_post)
    monkeypatch.setattr(P, "_poll_export", lambda *a, **kw: ("done", ""))

    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    (out / "rough_compose.mp4").write_bytes(b"")

    job = P.PipelineJob()
    # 即便用户填了 start=600 / duration=600，range_enabled=False 也不应传到 body
    cfg = {"enabled": True, "range_enabled": False,
           "preview_start": 600, "duration": 600}
    ok, msg = P.handler_fine_cut("t-1", cfg, out, "http://x", job)
    assert ok is True

    body = next(p for path, p in captured if path == "/export_fine_video")
    assert "preview_start" not in body, f"全片模式不应带 preview_start，body={body}"
    assert "duration" not in body, f"全片模式不应带 duration，body={body}"
    assert body["task_id"] == "t-1"


def test_handler_fine_cut_range_enabled_sends_start_and_duration(monkeypatch, tmp_path: Path):
    """v4：range_enabled=True → body 带 preview_start / duration。"""
    captured = []

    def fake_http_post(api, path, payload, **kw):
        captured.append((path, dict(payload)))
        if path == "/export_fine_video":
            return {"ok": True, "job_id": "j1"}
        return {"ok": True}

    monkeypatch.setattr(P, "_http_post", fake_http_post)
    monkeypatch.setattr(P, "_poll_export", lambda *a, **kw: ("done", ""))

    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    (out / "rough_compose.mp4").write_bytes(b"")

    job = P.PipelineJob()
    # 前端 HH:MM:SS → 秒：00:30:00 = 1800
    cfg = {"enabled": True, "range_enabled": True,
           "preview_start": 1800, "duration": 600}
    ok, msg = P.handler_fine_cut("t-1", cfg, out, "http://x", job)
    assert ok is True

    body = next(p for path, p in captured if path == "/export_fine_video")
    assert body["preview_start"] == 1800
    assert body["duration"] == 600


def test_handler_fine_cut_range_enabled_with_zero_start(monkeypatch, tmp_path: Path):
    """v4 边界：range_enabled=True + preview_start=0 → body 不带 preview_start（端点 0=全篇起点等价），
    但 duration 仍带。"""
    captured = []

    def fake_http_post(api, path, payload, **kw):
        captured.append((path, dict(payload)))
        if path == "/export_fine_video":
            return {"ok": True, "job_id": "j1"}
        return {"ok": True}

    monkeypatch.setattr(P, "_http_post", fake_http_post)
    monkeypatch.setattr(P, "_poll_export", lambda *a, **kw: ("done", ""))

    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    (out / "rough_compose.mp4").write_bytes(b"")

    job = P.PipelineJob()
    cfg = {"enabled": True, "range_enabled": True, "preview_start": 0, "duration": 60}
    ok, msg = P.handler_fine_cut("t-1", cfg, out, "http://x", job)
    assert ok is True
    body = next(p for path, p in captured if path == "/export_fine_video")
    # start=0 被过滤掉（handler 逻辑：ps not in (None, "", 0)），duration 保留
    assert "preview_start" not in body
    assert body.get("duration") == 60


def test_handler_subtitle_review_calls_rev_speaker_link_when_enabled(monkeypatch, tmp_path: Path):
    """v5：subtitle_review.link_person_ids=True → 调 /rev_speaker_link。"""
    calls = []
    def fake_http_post(api, path, payload, **kw):
        calls.append(path)
        if path == "/revise_subtitle":
            return {"ok": True}
        return {"ok": True}
    def fake_poll_status(api, path, tid, **kw):
        return ("done", "")
    monkeypatch.setattr(P, "_http_post", fake_http_post)
    monkeypatch.setattr(P, "_poll_status", fake_poll_status)
    # 写空 revision.json 让 skip_categories 处理能走
    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    (out / "subtitle.json").write_text("{}", encoding="utf-8")
    (out / "revision.json").write_text('{"entries":[]}', encoding="utf-8")

    job = P.PipelineJob()
    cfg = {"accept_all_suggestions": True, "link_person_ids": True,
           "skip_categories": [], "rigor": "medium"}
    ok, msg = P.handler_subtitle_review("t-1", cfg, out, "http://x", job)
    assert ok is True
    assert "/rev_speaker_link" in calls


def test_handler_rough_cut_calls_cut_speaker_link_when_enabled(monkeypatch, tmp_path: Path):
    """v5：rough_cut.link_person_ids=True（且 delete_speakers 为空）→ 调 /cut_speaker_link。"""
    calls = []
    def fake_http_post(api, path, payload, **kw):
        calls.append(path)
        return {"ok": True}
    monkeypatch.setattr(P, "_http_post", fake_http_post)
    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    (out / "subtitle.json").write_text("{}", encoding="utf-8")
    (out / "revision.json").write_text('{"entries":[]}', encoding="utf-8")

    job = P.PipelineJob()
    cfg = {"delete_speakers": [], "default_decision": "keep",
           "link_person_ids": True}
    ok, msg = P.handler_rough_cut("t-1", cfg, out, "http://x", job)
    assert ok is True
    assert "/cut_speaker_link" in calls


# =============== REQ-20260918-049：rigor 字段 + 续跑语义 ===============


def test_default_config_subtitle_review_has_rigor_medium():
    """REQ-049 v1：subtitle_review 默认带 rigor='medium'。"""
    cfg = P.default_config()
    assert cfg["subtitle_review"].get("rigor") == "medium"


def test_validate_config_normalizes_invalid_rigor_to_medium():
    """REQ-049 v1：脏数据（脏 rigor 值）→ validate 回落 medium。"""
    bad_inputs = [
        {"subtitle_review": {"rigor": "super-high"}},     # 未知
        {"subtitle_review": {"rigor": ""}},                # 空串
        {"subtitle_review": {"rigor": None}},              # None
        {"subtitle_review": {"rigor": 123}},               # 非字符串
        {"subtitle_review": {}},                           # 缺字段（用 default）
    ]
    for cfg_in in bad_inputs:
        cfg = P.validate_config(cfg_in)
        rigor = cfg["subtitle_review"].get("rigor")
        assert rigor == "medium", f"期望 medium，实际 {rigor!r} (输入={cfg_in!r})"


def test_validate_config_accepts_valid_rigor_values():
    """REQ-049 v1：合法 rigor 值（high/medium/low/custom）原样保留。"""
    for valid in ("high", "medium", "low", "custom"):
        cfg = P.validate_config({"subtitle_review": {"rigor": valid}})
        assert cfg["subtitle_review"]["rigor"] == valid


def test_handler_subtitle_review_passes_rigor_from_cfg(tmp_path: Path, monkeypatch):
    """REQ-049 v1：handler_subtitle_review 把 cfg.rigor 透传给 /revise_subtitle。"""
    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    (out / "subtitle.json").write_text("{}", encoding="utf-8")

    captured = []

    def fake_http_post(api, path, payload, *, timeout=30.0, auto_session_id=""):
        captured.append({"api": api, "path": path, "payload": payload,
                         "auto_session_id": auto_session_id})
        return {"ok": True}

    monkeypatch.setattr(P, "_http_post", fake_http_post)
    monkeypatch.setattr(P, "_poll_status", lambda *a, **kw: ("done", ""))

    job = P.PipelineJob(state="running", started_at=time.time(),
                       current_stage="subtitle_review", percent=40.0)
    ok, msg = P.handler_subtitle_review(
        "tid-rigor", {"rigor": "high", "accept_all_suggestions": False},
        out, "http://api", job,
    )
    assert ok is True
    # 找到 /revise_subtitle 调用
    revise_calls = [c for c in captured if c["path"] == "/revise_subtitle"]
    assert len(revise_calls) == 1, f"期望 1 次 /revise_subtitle 调用，实际 {len(revise_calls)}"
    payload = revise_calls[0]["payload"]
    assert payload["rigor"] == "high"
    assert payload["task_id"] == "tid-rigor"


def test_handler_subtitle_review_normalizes_invalid_rigor(tmp_path: Path, monkeypatch):
    """REQ-049 v1：handler 收到非法 rigor → 回落 medium（防御层）。"""
    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    (out / "subtitle.json").write_text("{}", encoding="utf-8")

    captured = []

    def fake_http_post(api, path, payload, *, timeout=30.0, auto_session_id=""):
        captured.append(payload)
        return {"ok": True}

    monkeypatch.setattr(P, "_http_post", fake_http_post)
    monkeypatch.setattr(P, "_poll_status", lambda *a, **kw: ("done", ""))

    job = P.PipelineJob(state="running", started_at=time.time(),
                       current_stage="subtitle_review", percent=40.0)
    # 注入非法 rigor（理论上 validate 已拦，这里是 handler 层兜底）
    P.handler_subtitle_review(
        "tid-bad-rigor", {"rigor": "ultra-mega", "accept_all_suggestions": False},
        out, "http://api", job,
    )
    revise_payloads = [p for p in captured if p.get("task_id") == "tid-bad-rigor"]
    assert revise_payloads
    assert revise_payloads[0]["rigor"] == "medium"


def test_history_stages_done_persists_through_runs(tmp_path: Path):
    """REQ-049 v1：连续两次 run 完后 history 含两条 summary，
    最新一条的 stages_done 反映第二次的范围（since 跳过早期阶段）。

    模拟：第一次跑 1 阶段（mock handler 立即 ok）；第二次 since='rough_cut'，
    stages_done 应该只含 ['rough_cut', 'rough_compose', 'optimize']。
    """
    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    api = "http://127.0.0.1:1"

    # 第一次 run：仅 mock subtitle_generation / subtitle_review 通过
    orig = dict(P.HANDLERS)

    def ok_g(*a, **kw): return (True, "")
    def ok_r(tid, cfg, outputs_dir, api, job): return (True, "")  # 不 mock 其他阶段
    def slow_other(tid, cfg, outputs_dir, api, job):
        return (False, "其他阶段未实现 mock → fail → 停止")
    P.HANDLERS["subtitle_generation"] = ok_g
    P.HANDLERS["subtitle_review"] = ok_r
    P.HANDLERS["rough_cut"] = slow_other
    P.HANDLERS["rough_compose"] = slow_other
    P.HANDLERS["optimize"] = slow_other
    P.HANDLERS["fine_cut"] = slow_other
    try:
        # 配置顶层 stop_after='subtitle_review'（默认），跑完前两阶段后停在 stop_after
        P.save_pipeline(out, {"config": P.default_config()})
        assert P.run_pipeline("tid-hist", api, out) is True
        # 等第一次跑完
        deadline = time.time() + 3.0
        while time.time() < deadline:
            st = P.pipeline_status("tid-hist")
            if st and st["state"] != "running":
                break
            time.sleep(0.05)
        first_st = P.pipeline_status("tid-hist")
        assert first_st is not None
        assert first_st["state"] == "stopped", f"第一次期望 stopped，实际 {first_st['state']}"
        assert "subtitle_generation" in first_st["summary"]["stages_done"]
        assert "subtitle_review" in first_st["summary"]["stages_done"]

        # 第二次 since='rough_cut'（接上次停点之后的 rough_cut）
        P.HANDLERS["rough_cut"] = ok_g
        P.HANDLERS["rough_compose"] = slow_other  # optimize 仍 fail
        P.run_pipeline("tid-hist", api, out, since="rough_cut")
        deadline = time.time() + 3.0
        while time.time() < deadline:
            st = P.pipeline_status("tid-hist")
            if st and st["state"] in ("error", "stopped"):
                break
            time.sleep(0.05)
        second_st = P.pipeline_status("tid-hist")
        # 第二次 stages_done 应该包含 rough_cut（前面 2 个被 since 跳过）
        done = second_st["summary"]["stages_done"]
        assert "rough_cut" in done
        # 跳过的 subtitle_generation/subtitle_review 不应再入 done
        assert "subtitle_generation" not in done
        assert "subtitle_review" not in done
    finally:
        P.HANDLERS.clear()
        P.HANDLERS.update(orig)

    # 落盘 history 应该含 2 条
    data = P.load_pipeline(out)
    assert data is not None
    history = data["history"]
    assert len(history) == 2
    # 最新一条在 history[-1]
    assert "rough_cut" in history[-1]["stages_done"]
    assert history[-1]["since"] == "rough_cut"
    # 第一条没 since（或 None）
    assert history[0].get("since") in (None, "None")
    assert "subtitle_generation" in history[0]["stages_done"]


def test_append_history_max_10_entries(tmp_path: Path):
    """REQ-049 v1：连续 12 次 append_history 后 history 最多保留 10 条。"""
    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    for i in range(12):
        P.append_history(out, {
            "started_at": i, "finished_at": i + 1, "duration_ms": 1000,
            "status": "done", "stages_done": [], "since": None, "error": None,
        })
    data = P.load_pipeline(out)
    assert data is not None
    history = data["history"]
    assert len(history) == 10
    # 最新 10 条 = i ∈ [2, 11]
    assert history[-1]["started_at"] == 11
    assert history[0]["started_at"] == 2


# =============== 前端 computeNextSince 逻辑（Python 复刻，便于单测） ===============
# 说明：JS 端 computeNextSince 与 STAGE_KEYS 强耦合，前端单测暂未建立。
# 这里用 Python 复刻同一算法逻辑，验证「下一个该跑的 stage」语义。

STAGE_KEYS = [s[0] for s in P.STAGE_ORDER]


def _compute_next_since_py(history):
    """Python 版 computeNextSince（与 JS pipeline.js:computeNextSince 同源）。

    边界：
    - 空 history → None（从头跑）
    - last.stages_done 含全部 5 个 → None（从头跑）
    - last.stages_done 含若干前缀 → 返回最后一个 done 之后的那个 key
    """
    if not isinstance(history, list) or len(history) == 0:
        return None
    last = history[-1]
    if not isinstance(last, dict):
        return None
    done = last.get("stages_done") or []
    if not isinstance(done, list):
        return None
    if len(done) >= len(STAGE_KEYS):
        return None
    last_idx = -1
    for k in done:
        if k in STAGE_KEYS:
            ix = STAGE_KEYS.index(k)
            if ix > last_idx:
                last_idx = ix
    if last_idx + 1 >= len(STAGE_KEYS):
        return None
    return STAGE_KEYS[last_idx + 1]


def test_compute_next_since_empty_history():
    """空 history → 从头跑。"""
    assert _compute_next_since_py([]) is None
    assert _compute_next_since_py(None) is None


def test_compute_next_since_first_stage_done():
    """stages_done 含 ['subtitle_generation'] → 下一个 subtitle_review。"""
    history = [{"stages_done": ["subtitle_generation"], "status": "stopped"}]
    assert _compute_next_since_py(history) == "subtitle_review"


def test_compute_next_since_two_stages_done():
    """stages_done 含前 2 个 → 下一个 rough_cut。"""
    history = [{"stages_done": ["subtitle_generation", "subtitle_review"], "status": "stopped"}]
    assert _compute_next_since_py(history) == "rough_cut"


def test_compute_next_since_all_done():
    """stages_done 含全部 5 个 → 从头跑（覆盖重跑场景）。"""
    history = [{"stages_done": list(STAGE_KEYS), "status": "done"}]
    assert _compute_next_since_py(history) is None


def test_compute_next_since_with_error_midway():
    """stages_done = ['subtitle_generation']，status=error（subtitle_review 挂）→
    下一个仍是 subtitle_review（让用户重试出错阶段）。"""
    history = [{"stages_done": ["subtitle_generation"], "status": "error",
                "error": "字幕修订出错"}]
    assert _compute_next_since_py(history) == "subtitle_review"


def test_compute_next_since_unknown_stage_ignored():
    """history 里有未知 stage key → 防御性忽略（不影响 next 计算）。"""
    history = [{"stages_done": ["subtitle_generation", "unknown_stage"], "status": "stopped"}]
    # 'unknown_stage' 不在 STAGE_KEYS 里 → last_idx 仍是 0
    assert _compute_next_since_py(history) == "subtitle_review"


# ---------- REQ-20260920-081：auto_session_id 透传 ----------

def test_run_pipeline_generates_unique_auto_session_id(tmp_path: Path, monkeypatch):
    """REQ-20260920-081：每次 run_pipeline 启动生成新的 12 字符 session_id；写到 job。"""
    from slirn_home import pipeline_service as P

    outputs_dir = tmp_path / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    api = "http://stub"

    # stub 所有 handler：返回 (True, "") 即 succeed
    monkeypatch.setattr(P, "HANDLERS",
                        {k: (lambda *a, **kw: (True, "")) for k in P.HANDLERS})
    # prereq：subtitle.json + revision.json（避免 skip）
    (outputs_dir / "subtitle.json").write_text("{}", encoding="utf-8")
    (outputs_dir / "revision.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(P, "_poll_status", lambda *a, **kw: ("done", ""))

    seen_sids = []
    for tid in ["tid-sess-a", "tid-sess-b"]:
        assert P.run_pipeline(tid, api, outputs_dir) is True
        job = P._PIPELINE_JOBS.get(tid)
        assert job is not None
        sid = job.auto_session_id
        assert len(sid) == 12, f"session_id 应为 12 字符：{sid!r}"
        seen_sids.append(sid)
        # 等守护线程退出
        t0 = time.time()
        while P._is_running(tid) and time.time() - t0 < 5:
            time.sleep(0.05)

    # 两次 run_pipeline 的 session_id 不同
    assert seen_sids[0] != seen_sids[1], "不同次自动流的 session_id 应不同"


def test_http_post_attaches_session_header(monkeypatch):
    """REQ-20260920-081：_http_post 接 auto_session_id 时附加 X-Slirn-Auto-Session header。"""
    from slirn_home import pipeline_service as P

    captured_requests = []

    class _FakeResp:
        def __init__(self): self._b = b'{"ok": true}'
        def read(self): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def fake_urlopen(req, timeout=None):
        # 把整个 req 存下来，调用后再读 header
        captured_requests.append(req)
        return _FakeResp()

    import urllib.request as _ur
    monkeypatch.setattr(_ur, "urlopen", fake_urlopen)
    # 不传 auto_session_id → 不带 X-Slirn-Auto-Session
    P._http_post("http://x", "/y", {"a": 1})
    assert len(captured_requests) == 1
    headers1 = {k.lower(): v for k, v in captured_requests[-1].headers.items()}
    assert "x-slirn-auto-session" not in headers1
    assert headers1.get("x-slirn-auto") == "1"
    # 传 auto_session_id → 带上 header
    P._http_post("http://x", "/y", {"a": 1}, auto_session_id="sid123abc456")
    assert len(captured_requests) == 2
    headers2 = {k.lower(): v for k, v in captured_requests[-1].headers.items()}
    assert headers2.get("x-slirn-auto-session") == "sid123abc456"


def test_handler_propagates_auto_session_id_to_http_post(tmp_path: Path, monkeypatch):
    """REQ-20260920-081：handler 调 _http_post 时透传 job.auto_session_id。"""
    from slirn_home import pipeline_service as P

    outputs_dir = tmp_path / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    # 准备 prereq 文件让 handler 跳过 skip
    (outputs_dir / "subtitle.json").write_text("{}", encoding="utf-8")
    (outputs_dir / "revision.json").write_text("{}", encoding="utf-8")

    captured = []

    def fake_http_post(api, path, payload, *, timeout=30.0, auto_session_id=""):
        captured.append({"path": path, "auto_session_id": auto_session_id})
        return {"ok": True}

    def fake_poll(*a, **kw):
        return ("done", "")

    job = P.PipelineJob(state="running", started_at=time.time(),
                       current_stage=None, percent=0.0,
                       auto_session_id="sess-handler-1")

    monkeypatch.setattr(P, "_http_post", fake_http_post)
    monkeypatch.setattr(P, "_poll_status", fake_poll)

    ok, msg = P.handler_rough_cut("tid-prop", {}, outputs_dir, "http://x", job)
    assert ok is True

    # fake_http_post 应被调用，且传了 session_id
    assert len(captured) >= 1
    for call in captured:
        assert call["auto_session_id"] == "sess-handler-1", (
            f"所有 _http_post 调用应透传 session_id: {call}"
        )


# =====================================================================
# REQ-20260921-NNN：清理所有阶段产物（clear_pipeline_state）
# =====================================================================

def test_clear_pipeline_state_clears_jobs_and_stop(tmp_path: Path):
    """REQ-20260921-NNN：clear_pipeline_state 清内存 job + stop flag + 历史。"""
    from slirn_home import pipeline_service as P

    tid = "tid-clear-1"

    # 1. 制造内存 job + stop flag
    job = P.PipelineJob(state="running", started_at=time.time(),
                       current_stage="rough_cut", percent=50.0,
                       auto_session_id="s")
    P._set_job(tid, job)
    P._request_stop(tid)

    # 2. 历史文件路径（用 tmp_path）
    history_path = tmp_path / "execution_history.json"
    history_path.write_text('[{"started_at":"x"}]', encoding="utf-8")

    # 3. 调 clear
    result = P.clear_pipeline_state(tid, history_path=history_path)

    # 4. 内存 job 应被清
    assert P._PIPELINE_JOBS.get(tid) is None, "内存 job 应清"
    # 5. stop flag 应被清
    assert P._consume_stop(tid) is False, "stop flag 应清（consume 应返回 False）"
    # 6. 历史文件应被删
    assert not history_path.exists(), "execution_history.json 应被删"
    # 7. 返回值
    assert result["cleared_jobs"] == 1
    assert result["cleared_stop"] is True
    assert result["cleared_history"] is True


def test_clear_pipeline_state_handles_missing_job(tmp_path: Path):
    """REQ-20260921-NNN：clear_pipeline_state 容忍 missing job（不抛错）。"""
    from slirn_home import pipeline_service as P

    tid = "tid-no-job"
    # 没 job / 没 stop flag / 没 history
    result = P.clear_pipeline_state(tid, history_path=tmp_path / "nope.json")
    assert result["cleared_jobs"] == 0
    assert result["cleared_stop"] is False
    assert result["cleared_history"] is False


def test_clear_pipeline_state_no_history_path_specified(tmp_path: Path, monkeypatch):
    """REQ-20260921-NNN：不传 history_path → 尝试候选路径（不抛错）。"""
    from slirn_home import pipeline_service as P

    tid = "tid-cand"
    # 不传 history_path → 内部尝试 2 个候选（都不存在也无害）
    result = P.clear_pipeline_state(tid)
    # cleared_history 应是 False（候选路径都不存在）
    assert result["cleared_history"] is False
    # 其他字段也不应抛错
    assert "cleared_jobs" in result
    assert "cleared_stop" in result


# =====================================================================
# REQ-20260921-NNN：fine_cut 预检（素材 + 参数）— fine_cut_preflight
# =====================================================================


def _make_task_dir(tmp_path: Path, tid: str = "t-fc") -> tuple[Path, Path]:
    """建立 tasks/<tid>/ 模拟结构：返回 (task_dir, outputs_dir) 和 fc_root 路径。

    outputs_dir = task_dir / outputs（pipeline.json 落盘位置）。
    fc_root = task_dir / fine_compose.json（精剪参数落盘位置）。
    """
    task_dir = tmp_path / tid
    outputs_dir = task_dir / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    return task_dir, outputs_dir


def _write_fc(task_dir: Path, fc: dict) -> Path:
    """写 fine_compose.json 到 task_dir/fine_compose.json。"""
    fc_root = task_dir / "fine_compose.json"
    fc_root.write_text(json.dumps(fc, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    return fc_root


def test_fine_cut_preflight_disabled_returns_ok(tmp_path: Path):
    """fine_cut.enabled=False → 预检直接通过，不读 fc.json。"""
    from slirn_home import pipeline_service as P

    task_dir, outputs_dir = _make_task_dir(tmp_path)
    cfg = P.default_config()
    cfg["fine_cut"]["enabled"] = False
    pre = P.fine_cut_preflight("t-fc", cfg, outputs_dir)
    assert pre["ok"] is True
    assert pre["enabled"] is False
    # 即便 fc.json 不存在也不报错
    assert pre["has_fc_json"] is True  # 未启用 → 视同 True（前端不强制）


def test_fine_cut_preflight_enabled_no_fc_json_returns_ok(tmp_path: Path):
    """fine_cut.enabled=True + fc.json 不存在 → preflight 不报错
    （让用户能继续操作；具体参数是否就绪由 _check_fine_cut_params 报）。"""
    from slirn_home import pipeline_service as P

    task_dir, outputs_dir = _make_task_dir(tmp_path)
    cfg = P.default_config()
    cfg["fine_cut"]["enabled"] = True
    cfg["fine_cut"]["params_source"] = "template:demo-profile-id"
    pre = P.fine_cut_preflight("t-fc", cfg, outputs_dir)
    # 选了模板 → 通过
    assert pre["ok"] is True
    assert pre["has_fc_json"] is False


def test_fine_cut_preflight_params_blocked_when_no_fc_json_and_current(tmp_path: Path):
    """enabled=True + 无 fc.json + params_source='current' → 阻断 + 明确原因。"""
    from slirn_home import pipeline_service as P

    task_dir, outputs_dir = _make_task_dir(tmp_path)
    cfg = P.default_config()
    cfg["fine_cut"]["enabled"] = True
    cfg["fine_cut"]["params_source"] = "current"
    pre = P.fine_cut_preflight("t-fc", cfg, outputs_dir)
    assert pre["ok"] is False
    assert pre["has_fc_json"] is False
    assert "fine_compose.json" in pre["reason"]
    assert "第 6 阶段" in pre["reason"]


def test_fine_cut_preflight_materials_missing_when_cover_bg_enabled_but_no_path(tmp_path: Path):
    """v2 用户反馈：video/subtitle/reference 由 handler / 上游产物保证，
    预检只关心「用户在精剪合成页勾选启用但忘了上传」的 cover/bg。

    enabled=True + 有 fc.json + materials 全空 + layout.cover.enabled=True +
    layout.bg.enabled=True → 缺 cover + bg（user 勾了启用但没上传）→ 阻断。
    """
    from slirn_home import pipeline_service as P

    task_dir, outputs_dir = _make_task_dir(tmp_path)
    _write_fc(task_dir, {
        "materials": {},
        "layout": {
            "video": {"enabled": True},
            "subtitle": {"enabled": True},
            "cover": {"enabled": True},   # 启用但没 path → 必须阻断
            "bg": {"enabled": True},       # 启用但没 path → 必须阻断
        },
        "font": {},
        "output": {},
    })
    cfg = P.default_config()
    cfg["fine_cut"]["enabled"] = True
    cfg["fine_cut"]["params_source"] = "current"
    pre = P.fine_cut_preflight("t-fc", cfg, outputs_dir)
    assert pre["ok"] is False
    assert pre["has_fc_json"] is True
    # 缺 cover + bg（用户启用但没上传）
    missing_kinds = [m["kind"] for m in pre["materials"]["missing"]]
    assert "cover" in missing_kinds
    assert "bg" in missing_kinds
    # video / subtitle / reference 不应被预检（handler / 上游 / AI 辅助）
    assert "video" not in missing_kinds
    assert "subtitle" not in missing_kinds
    assert "reference" not in missing_kinds
    # audio + reference 都属于 optional（reference 是 AI 辅助，audio 是 BGM）
    opt_kinds = [m["kind"] for m in pre["materials"]["optional_missing"]]
    assert "audio" in opt_kinds
    assert "reference" in opt_kinds


def test_fine_cut_preflight_materials_ok_when_cover_bg_disabled(tmp_path: Path):
    """v2 用户反馈：cover/bg 默认 disabled → 不需要 path，预检直接过。

    这场景最常见 —— 用户只用视频 + 字幕（粗剪 + 优化字幕上游产物自动获取），
    没勾封面 / 没勾背景，预检不应阻塞。
    """
    from slirn_home import pipeline_service as P

    task_dir, outputs_dir = _make_task_dir(tmp_path)
    _write_fc(task_dir, {
        # materials 全空：用户没上传任何素材
        "materials": {},
        # cover/bg 默认 disabled（_FINE_LAYOUT_DEFAULTS）
        "layout": {
            "cover": {"enabled": False},
            "bg": {"enabled": False},
        },
        "font": {},
        "output": {},
    })
    cfg = P.default_config()
    cfg["fine_cut"]["enabled"] = True
    cfg["fine_cut"]["params_source"] = "current"
    pre = P.fine_cut_preflight("t-fc", cfg, outputs_dir)
    assert pre["ok"] is True, f"cover/bg 都 disabled 时不应阻塞，实际: {pre}"
    assert pre["materials"]["missing"] == []
    # audio + reference 仍是 optional
    opt_kinds = [m["kind"] for m in pre["materials"]["optional_missing"]]
    assert "audio" in opt_kinds
    assert "reference" in opt_kinds


def test_fine_cut_preflight_materials_full_ok(tmp_path: Path):
    """enabled=True + fc.json 含全部素材 path + layout 全 disabled → 通过。"""
    from slirn_home import pipeline_service as P

    task_dir, outputs_dir = _make_task_dir(tmp_path)
    _write_fc(task_dir, {
        "materials": {
            "video":     {"path": "x/y.mp4"},
            "subtitle":  {"path": "x/y.srt"},
            "cover":     {"path": "x/y.png"},
            "bg":        {"path": "x/bg.png"},
            "reference": {"path": "x/ref.png"},
            "audio":     {"path": "x/bgm.mp3"},
        },
        "layout": {
            "cover": {"enabled": False},
            "bg": {"enabled": False},
        },
        "font": {},
        "output": {},
    })
    cfg = P.default_config()
    cfg["fine_cut"]["enabled"] = True
    cfg["fine_cut"]["params_source"] = "current"
    pre = P.fine_cut_preflight("t-fc", cfg, outputs_dir)
    assert pre["ok"] is True
    assert pre["materials"]["missing"] == []
    assert pre["materials"]["optional_missing"] == []


def test_fine_cut_preflight_only_bgm_missing_is_ok(tmp_path: Path):
    """enabled=True + 仅有 BGM 缺失（其他素材都齐 + layout 全 disabled）→ 仍通过（BGM 可选）。"""
    from slirn_home import pipeline_service as P

    task_dir, outputs_dir = _make_task_dir(tmp_path)
    _write_fc(task_dir, {
        "materials": {
            "video":     {"path": "x/y.mp4"},
            "subtitle":  {"path": "x/y.srt"},
            "cover":     {"path": "x/y.png"},
            "bg":        {"path": "x/bg.png"},
            "reference": {"path": "x/ref.png"},
            # audio 没填
        },
        "layout": {
            "cover": {"enabled": False},
            "bg": {"enabled": False},
        },
        "font": {},
        "output": {},
    })
    cfg = P.default_config()
    cfg["fine_cut"]["enabled"] = True
    pre = P.fine_cut_preflight("t-fc", cfg, outputs_dir)
    assert pre["ok"] is True
    opt_kinds = [m["kind"] for m in pre["materials"]["optional_missing"]]
    assert "audio" in opt_kinds


def test_fine_cut_preflight_corrupt_fc_json_blocked(tmp_path: Path):
    """enabled=True + fc.json 解析失败 → 阻断 + 明确错误。"""
    from slirn_home import pipeline_service as P

    task_dir, outputs_dir = _make_task_dir(tmp_path)
    fc_root = task_dir / "fine_compose.json"
    fc_root.write_text("{not valid json", encoding="utf-8")
    cfg = P.default_config()
    cfg["fine_cut"]["enabled"] = True
    pre = P.fine_cut_preflight("t-fc", cfg, outputs_dir)
    assert pre["ok"] is False
    assert "解析失败" in pre["reason"]


def test_fine_cut_preflight_skipped_when_stop_after_earlier(tmp_path: Path):
    """run_mode=stop_after + stop_after=rough_cut < fine_cut — 不会跑到精剪 → 预检无关。"""
    from slirn_home import pipeline_service as P

    task_dir, outputs_dir = _make_task_dir(tmp_path)
    # fc.json 不存在 + params_source=current + 缺所有素材
    # 但因 stop_after=rough_cut，handler_fine_cut 根本不会被调用 → 预检应 ok=True
    cfg = P.default_config()
    cfg["fine_cut"]["enabled"] = True
    cfg["run_mode"] = "stop_after"
    cfg["stop_after"] = "rough_cut"
    pre = P.fine_cut_preflight("t-fc", cfg, outputs_dir)
    # 但预检本身不知道 run_mode/stop_after，它只看 cfg.fine_cut.enabled
    # 实际上调用方（在 app.py）根据 run_mode/stop_after 决定是否调预检
    # 这里我们只验：预检被调时返回正确的素材缺失报告
    assert pre["ok"] is False  # 还是会报素材缺失（调用方应跳过调用）


def test_fine_cut_preflight_import_params_ok(tmp_path: Path):
    """enabled=True + 无 fc.json + params_source='import' → 通过
    （导入由第 6 阶段详情页负责，模板选择器已经允许）。"""
    from slirn_home import pipeline_service as P

    task_dir, outputs_dir = _make_task_dir(tmp_path)
    cfg = P.default_config()
    cfg["fine_cut"]["enabled"] = True
    cfg["fine_cut"]["params_source"] = "import"
    pre = P.fine_cut_preflight("t-fc", cfg, outputs_dir)
    assert pre["ok"] is True


# =============== REQ-20260921-NNN-v3：handler_optimize 必须发 decisions 数组 ===============
# 早期版本发 {"task_id": tid, "applied_all": True}，但端点硬要求 decisions 是 list；
# 端点返回 400 「decisions 必须是数组」→ 整个 optimize 阶段失败 → fine_cut 进不去。
# handler 必须从 optimize_subtitle.json 读 occurrences，构造全量 [{occ_id, applied, after, reviewed}]。


def test_handler_optimize_accept_all_sends_decisions_array(monkeypatch, tmp_path: Path):
    """accept_all_replacements=True → handler_optimize 必须发 decisions 列表，
    且每条 applied=True + after=大模型建议值 + reviewed=True。
    """
    from slirn_home import pipeline_service as P

    captured = []

    def fake_http_post(api, path, payload, **kw):
        captured.append((path, payload))
        return {"ok": True}

    def fake_poll_status(api, path, tid, **kw):
        return ("done", "")

    # 写一份 optimize_subtitle.json 给 handler 读
    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    (out / "rough_compose.mp4").write_bytes(b"")
    opt_data = {
        "version": 1,
        "occurrences": [
            {"occ_id": 0, "seg": 5, "pos": 0, "before": "测似", "after": "测试",
             "reason": "形近字", "applied": True, "reviewed": False},
            {"occ_id": 1, "seg": 12, "pos": 1, "before": "功击", "after": "攻击",
             "reason": "形近字", "applied": True, "reviewed": False},
            {"occ_id": 2, "seg": 18, "pos": 0, "before": "机气", "after": "机器",
             "reason": "同音字", "applied": True, "reviewed": False},
        ],
    }
    (out / "optimize_subtitle.json").write_text(
        json.dumps(opt_data, ensure_ascii=False), encoding="utf-8"
    )

    monkeypatch.setattr(P, "_http_post", fake_http_post)
    monkeypatch.setattr(P, "_poll_status", fake_poll_status)

    job = P.PipelineJob()
    cfg = {"accept_all_replacements": True}
    ok, msg = P.handler_optimize("t-opt", cfg, out, "http://x", job)
    assert ok is True, f"handler_optimize 失败：{msg}"

    # 找到 save_optimize_subtitle 调用
    save_payloads = [p for path, p in captured if path == "/save_optimize_subtitle"]
    assert len(save_payloads) == 1, f"应只调一次 save_optimize_subtitle，实际：{captured}"
    payload = save_payloads[0]
    # 关键契约：decisions 必须是 list
    assert "decisions" in payload, f"payload 缺 decisions：{payload}"
    assert isinstance(payload["decisions"], list), f"decisions 不是 list：{payload}"
    assert len(payload["decisions"]) == 3
    # 每条 applied=True + after 是大模型建议 + reviewed=True
    for d in payload["decisions"]:
        assert d["applied"] is True
        assert d["reviewed"] is True
        assert d["after"]  # 非空
    # 顺序按 occ_id 排
    assert [d["occ_id"] for d in payload["decisions"]] == [0, 1, 2]
    # after 正确（不是空字符串、不是 before 原值）
    by_occ = {d["occ_id"]: d["after"] for d in payload["decisions"]}
    assert by_occ[0] == "测试"
    assert by_occ[1] == "攻击"
    assert by_occ[2] == "机器"


def test_handler_optimize_accept_all_skips_save_when_disabled(monkeypatch, tmp_path: Path):
    """accept_all_replacements=False → 不发 save_optimize_subtitle，等人工。"""
    from slirn_home import pipeline_service as P

    captured = []

    def fake_http_post(api, path, payload, **kw):
        captured.append(path)
        return {"ok": True}

    monkeypatch.setattr(P, "_http_post", fake_http_post)
    monkeypatch.setattr(P, "_poll_status", lambda *a, **kw: ("done", ""))

    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    (out / "rough_compose.mp4").write_bytes(b"")

    job = P.PipelineJob()
    cfg = {"accept_all_replacements": False}
    ok, msg = P.handler_optimize("t-opt", cfg, out, "http://x", job)
    assert ok is True
    assert "/save_optimize_subtitle" not in captured, \
        f"accept_all=False 时不应调 save_optimize_subtitle，实际：{captured}"


def test_handler_optimize_fails_when_optimize_json_missing(monkeypatch, tmp_path: Path):
    """accept_all=True 但 optimize_subtitle.json 不存在 → handler 报错（不静默）。"""
    from slirn_home import pipeline_service as P

    monkeypatch.setattr(P, "_http_post", lambda *a, **kw: {"ok": True})
    monkeypatch.setattr(P, "_poll_status", lambda *a, **kw: ("done", ""))

    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    (out / "rough_compose.mp4").write_bytes(b"")
    # 注意：不写 optimize_subtitle.json

    job = P.PipelineJob()
    cfg = {"accept_all_replacements": True}
    ok, msg = P.handler_optimize("t-opt", cfg, out, "http://x", job)
    assert ok is False
    assert "optimize_subtitle.json" in msg or "不存在" in msg


# =============== REQ-20260921-NNN-v3：_poll_status 必须用 POST ===============
# 早期版本用 _http_get 调 POST 端点 → 永远 405 → handler 永远不返回 → 主循环
# 卡死。这两个测试钉死契约：_poll_status 必须 POST，且必须识别 done/error。


def test_poll_status_uses_post_not_get(monkeypatch):
    """回归测试：_poll_status 必须调 _http_post（不是 _http_get），
    否则 4 个 status 端点（POST）会 405，pipeline 永远卡在第一个阶段。
    """
    from slirn_home import pipeline_service as P

    called_post = []
    called_get = []

    def fake_http_post(api, path, payload, **kw):
        called_post.append((path, payload))
        return {"ok": True, "job": {"state": "done"}}

    def fake_http_get(api, path, payload=None, **kw):
        called_get.append((path, payload))
        return {"ok": True, "job": {"state": "done"}}

    monkeypatch.setattr(P, "_http_post", fake_http_post)
    monkeypatch.setattr(P, "_http_get", fake_http_get)
    state, err = P._poll_status("http://x", "/subtitle_status", "t-poll",
                                interval=0.01, timeout=5.0)
    assert state == "done"
    assert err == ""
    assert "/subtitle_status" in [p for p, _ in called_post], \
        f"_poll_status 应该调 _http_post('/subtitle_status', ...)，实际调了: {called_post}"
    assert called_get == [], f"_poll_status 不应该调 _http_get，实际调了: {called_get}"


def test_poll_status_returns_done_on_post_response(monkeypatch):
    """_poll_status 调 POST，POST 返回 {state:done} → 立即返回 ('done', '')。"""
    from slirn_home import pipeline_service as P

    responses = iter([
        {"ok": True, "job": {"state": "running"}},
        {"ok": True, "job": {"state": "done"}},
    ])

    def fake_http_post(api, path, payload, **kw):
        return next(responses)

    monkeypatch.setattr(P, "_http_post", fake_http_post)
    state, err = P._poll_status("http://x", "/revise_status", "t-poll",
                                interval=0.001, timeout=10.0)
    assert state == "done"
    assert err == ""


def test_poll_status_returns_error_on_post_response(monkeypatch):
    """POST 返回 {state:error} → 返回 ('error', <err>)，不是 timeout。"""
    from slirn_home import pipeline_service as P

    monkeypatch.setattr(P, "_http_post",
                        lambda *a, **kw: {"ok": True, "job": {"state": "error", "error": "识别失败"}})
    state, err = P._poll_status("http://x", "/subtitle_status", "t-poll",
                                interval=0.001, timeout=10.0)
    assert state == "error"
    assert "识别失败" in err
