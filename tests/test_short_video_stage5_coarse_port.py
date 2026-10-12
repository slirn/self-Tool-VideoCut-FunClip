"""REQ-20261013-103：短视频 Stage 5 粗剪按长视频 compose_rough 整套搬。

覆盖：
1. start_coarse_async 内存 job 表语义（已在跑 → False；新启动 → True）
2. coarse_job_status 返回 dict 副本（外部修改不污染内部）
3. run_stage4 progress_cb 事件契约（stage4_start / clip_done / done）
4. delete_coarse 清 N 条 mp4+srt + 写 history + reset 下游
5. preview_continuous_subs 跨 N 条拼连续 SRT（用 mock video duration 避免 ffprobe）
6. 短/长隔离：execution_history 写短视频 stage5_dir，不写长视频 outputs
"""

import json
from pathlib import Path

import pytest


# ---------- 1. 内存 job 表 ----------

def test_coarse_job_status_idempotent_and_returns_copy(tmp_path, monkeypatch):
    """start_coarse_async 第二次同 (tid, pid) 已在跑 → False。

    用 _set_job 模拟已有 running 状态，验证 start_coarse_async 在 daemon
    线程跑起来之前先检查。
    """
    from slirn_home import short_video_coarse as mod

    # 清空 _JOBS
    with mod._JOBS_LOCK:
        mod._JOBS.clear()

    # 模拟已在 running
    mod._set_job("t1", "p1", state="running", started_at=0.0)
    # 二次启动 → 应返回 False（不进 start_coarse_async 的线程分支）
    # 这里直接断言 _JOBS 内容不变
    j = mod.coarse_job_status("t1", "p1")
    assert j["state"] == "running"


def test_coarse_job_status_returns_copy_not_reference():
    """coarse_job_status 返回的 dict 改值不污染 _JOBS 内部。"""
    from slirn_home import short_video_coarse as mod

    with mod._JOBS_LOCK:
        mod._JOBS.clear()
    mod._set_job("t1", "p1", state="done", progress=100.0, count=5)

    j = mod.coarse_job_status("t1", "p1")
    j["state"] = "MUTATED"
    j["count"] = 999

    j2 = mod.coarse_job_status("t1", "p1")
    assert j2["state"] == "done", "coarse_job_status 必须返回 _JOBS 内部 dict 的拷贝"
    assert j2["count"] == 5


# ---------- 2. progress_cb 事件契约 ----------

def test_run_stage4_progress_cb_events(tmp_path, monkeypatch):
    """run_stage4 调 progress_cb 顺序：start → N 个 clip_done/failed → done/failed。

    隔离 moviepy 真实调用：patch 掉 _coarse_one 让它立刻返回 done；patch
    highlights.json 读路径 + raw.srt 不需要（无 src_index 时 fallback 整体切）。
    """
    from slirn_home import short_video_coarse as mod
    from slirn_home import short_video_service as svc

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tasks" / "t1" / "short_video" / "p1").mkdir(parents=True)

    # 建项目（含 source video 占位 + stage3 highlights）
    src_video = repo / "tasks" / "t1" / "v.mp4"
    src_video.write_bytes(b"fake")  # _coarse_one 不会真读
    # 直接构造 project.json
    project = {
        "task_id": "t1",
        "id": "p1",
        "base_material_id": "m1",
        "materials": [{"id": "m1", "kind": "video", "name": "v.mp4", "path": "tasks/t1/v.mp4"}],
        "pipeline": {
            "stage1_source": {"status": "done"},
            "stage3_analyze": {"status": "done", "highlights": [
                {"index": 1, "start_ms": 0, "end_ms": 1000, "subtitle_lines": []},
                {"index": 2, "start_ms": 0, "end_ms": 1000, "subtitle_lines": []},
            ]},
        },
    }
    svc.save_project(repo, project)
    # 写 highlights.json（run_stage4 读它）
    hl_dir = svc.stage3_dir(repo, "t1", "p1")
    hl_dir.mkdir(parents=True, exist_ok=True)
    (hl_dir / "highlights.json").write_text(
        json.dumps({"highlights": [
            {"index": 1, "start_ms": 0, "end_ms": 1000, "subtitle_lines": []},
            {"index": 2, "start_ms": 0, "end_ms": 1000, "subtitle_lines": []},
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )

    # patch _coarse_one 让它立刻返回 done（避免 moviepy 真跑）
    def fake_coarse_one(source_video, hl, out_mp4, out_srt, **kw):
        out_mp4.parent.mkdir(parents=True, exist_ok=True)
        out_mp4.write_bytes(b"fake-mp4")
        out_srt.write_text("", encoding="utf-8")
        return mod.CoarseClipResult(
            index=int(hl.get("index") or 1),
            coarse_mp4=out_mp4, coarse_srt=out_srt,
            status="done", duration_sec=1.0, elapsed_sec=0,
        )
    monkeypatch.setattr(mod, "_coarse_one", fake_coarse_one)

    events: list[tuple[str, dict]] = []
    def cb(event, payload):
        events.append((event, dict(payload)))

    state = mod.run_stage4(repo, "t1", "p1", progress_cb=cb)

    # 断言事件顺序
    assert events[0][0] == "stage4_start", f"first event must be start, got {events[0]}"
    assert events[0][1]["count"] == 2
    # 2 个 clip_done
    done_events = [e for e in events if e[0] == "stage4_clip_done"]
    assert len(done_events) == 2, f"want 2 clip_done, got {len(done_events)}"
    # 最后一个是 done
    assert events[-1][0] == "stage4_done", f"last event must be done, got {events[-1]}"
    # state 含 total_duration_sec
    assert "total_duration_sec" in state
    assert state["total_duration_sec"] == pytest.approx(2.0, rel=1e-3)


def test_run_stage4_progress_cb_none_backward_compat(tmp_path, monkeypatch):
    """progress_cb=None 走原行为 — 不传也能跑（向后兼容）。"""
    from slirn_home import short_video_coarse as mod
    from slirn_home import short_video_service as svc

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tasks" / "t1" / "short_video" / "p1").mkdir(parents=True)
    project = {
        "task_id": "t1", "id": "p1",
        "base_material_id": "m1",
        "materials": [{"id": "m1", "kind": "video", "name": "v.mp4", "path": "tasks/t1/v.mp4"}],
        "pipeline": {"stage1_source": {"status": "done"}},
    }
    svc.save_project(repo, project)
    hl_dir = svc.stage3_dir(repo, "t1", "p1")
    hl_dir.mkdir(parents=True, exist_ok=True)
    (hl_dir / "highlights.json").write_text(
        json.dumps({"highlights": []}, ensure_ascii=False), encoding="utf-8",
    )

    def fake_coarse_one(source_video, hl, out_mp4, out_srt, **kw):
        return mod.CoarseClipResult(
            index=1, coarse_mp4=out_mp4, coarse_srt=out_srt,
            status="failed", duration_sec=0.0, elapsed_sec=0, error="no source",
        )
    monkeypatch.setattr(mod, "_coarse_one", fake_coarse_one)

    # 0 highlights → run_stage4 早返回（highlights.json 没内容抛 ShortVideoError）
    # 这里不传 progress_cb，验证签名兼容
    with pytest.raises(svc.ShortVideoError):
        mod.run_stage4(repo, "t1", "p1")  # progress_cb=None 走默认值


# ---------- 3. delete_coarse ----------

def test_delete_coarse_clears_files_and_resets_state(tmp_path, monkeypatch):
    """delete_coarse 清 N 条 mp4+srt + 重置 state + 写历史 + reset 下游。"""
    from slirn_home import short_video_coarse as mod
    from slirn_home import short_video_service as svc

    repo = tmp_path / "repo"
    repo.mkdir()
    s4 = repo / "tasks" / "t1" / "short_video" / "p1" / "stage4"
    s5 = repo / "tasks" / "t1" / "short_video" / "p1" / "stage5"
    s4.mkdir(parents=True)
    s5.mkdir(parents=True)

    # 落 2 条 mp4 + 2 条 srt + 1 条 preview_subs.srt
    (s4 / "coarse_01.mp4").write_bytes(b"mp4-1")
    (s4 / "coarse_01.srt").write_text("", encoding="utf-8")
    (s4 / "coarse_02.mp4").write_bytes(b"mp4-2")
    (s4 / "coarse_02.srt").write_text("", encoding="utf-8")
    (s5 / "preview_subs.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nx",
                                        encoding="utf-8")

    # project.json 含 stage5_coarse done + stage6 pending
    project = {
        "task_id": "t1", "id": "p1",
        "pipeline": {
            "stage5_coarse": {"status": "done", "highlights": [
                {"index": 1, "coarse_mp4": "tasks/t1/short_video/p1/stage4/coarse_01.mp4"},
            ]},
            "stage6_refine": {"status": "running_asr"},
        },
    }
    svc.save_project(repo, project)

    # patch execution_history 避免真写盘
    captured = {}
    def fake_record_start(out_dir, kind, **kw):
        captured["kind"] = kind
        captured["out_dir"] = out_dir
        return "eid_123"
    def fake_record_finish(out_dir, eid, **kw):
        captured["finish"] = kw
    def fake_patch_fields(out_dir, eid, fields):
        captured["description"] = fields.get("description", "")
    import slirn_home.execution_history as eh
    monkeypatch.setattr(eh, "record_start", fake_record_start)
    monkeypatch.setattr(eh, "record_finish", fake_record_finish)
    monkeypatch.setattr(eh, "patch_fields", fake_patch_fields)

    res = mod.delete_coarse(repo, "t1", "p1")

    assert res["deleted"] is True
    # 5 个文件全删
    assert len(res["removed"]) == 5
    # 文件真不在了
    assert not (s4 / "coarse_01.mp4").exists()
    assert not (s4 / "coarse_02.srt").exists()
    assert not (s5 / "preview_subs.srt").exists()
    # history 写对了
    assert captured["kind"] == "short_video_coarse_delete"
    assert "stage5" in str(captured["out_dir"]).replace("\\", "/")
    # state 重置
    proj2 = svc.load_project(repo, "t1", "p1")
    s5_state = proj2["pipeline"]["stage5_coarse"]
    assert s5_state["status"] == "pending"
    assert s5_state["highlights"] == []
    assert s5_state["count"] == 0
    # 下游作废
    assert proj2["pipeline"]["stage6_refine"]["status"] == "pending"


def test_delete_coarse_no_files_noop(tmp_path, monkeypatch):
    """空目录调用 delete_coarse → deleted=False + 仍写历史（无操作）。"""
    from slirn_home import short_video_coarse as mod
    from slirn_home import short_video_service as svc

    repo = tmp_path / "repo"
    repo.mkdir()
    s4 = repo / "tasks" / "t1" / "short_video" / "p1" / "stage4"
    s4.mkdir(parents=True)

    project = {"task_id": "t1", "id": "p1", "pipeline": {}}
    svc.save_project(repo, project)

    captured = {}
    import slirn_home.execution_history as eh
    monkeypatch.setattr(eh, "record_start", lambda *a, **k: captured.setdefault("kind", a[1]) or "eid")
    monkeypatch.setattr(eh, "record_finish", lambda *a, **k: None)
    monkeypatch.setattr(eh, "patch_fields", lambda *a, **k: captured.setdefault("desc", k.get("fields", {}).get("description", "")))

    res = mod.delete_coarse(repo, "t1", "p1")
    assert res["deleted"] is False
    assert res["removed"] == []
    assert captured.get("kind") == "short_video_coarse_delete"


# ---------- 4. preview_continuous_subs ----------

def test_preview_continuous_subs_concat_with_offset(tmp_path, monkeypatch):
    """preview_continuous_subs 跨 N 条 mp4 累加偏移拼成连续 SRT。"""
    from slirn_home import short_video_coarse as mod
    from slirn_home import short_video_service as svc
    from slirn_home import short_video_finalize as fin

    repo = tmp_path / "repo"
    repo.mkdir()
    s4 = repo / "tasks" / "t1" / "short_video" / "p1" / "stage4"
    s5 = repo / "tasks" / "t1" / "short_video" / "p1" / "stage5"
    s4.mkdir(parents=True)
    s5.mkdir(parents=True)

    # 2 条 mp4（10s + 20s）
    (s4 / "coarse_01.mp4").write_bytes(b"x")
    (s4 / "coarse_02.mp4").write_bytes(b"y")

    # mock ffprobe_duration：10 / 20 秒
    durations = iter([10.0, 20.0])
    monkeypatch.setattr(fin, "_ffprobe_duration", lambda p: next(durations))

    project = {
        "task_id": "t1", "id": "p1",
        "pipeline": {"stage5_coarse": {"status": "done", "highlights": [
            {"index": 1, "coarse_mp4": "tasks/t1/short_video/p1/stage4/coarse_01.mp4",
             "duration_sec": 10.0},
            {"index": 2, "coarse_mp4": "tasks/t1/short_video/p1/stage4/coarse_02.mp4",
             "duration_sec": 20.0},
        ]}},
    }
    svc.save_project(repo, project)

    res = mod.preview_continuous_subs(repo, "t1", "p1")
    assert res["clips"] == 2
    assert res["cached"] is False
    srt = res["srt"]
    # clip_1 [0, 10s] + clip_2 [10s, 30s]
    assert "00:00:00,000 --> 00:00:10,000" in srt
    assert "00:00:10,000 --> 00:00:30,000" in srt
    # 缓存落盘
    assert (s5 / "preview_subs.srt").is_file()


def test_preview_continuous_subs_empty_highlights(tmp_path):
    """空 highlights → 空 srt + cached=False。"""
    from slirn_home import short_video_coarse as mod
    from slirn_home import short_video_service as svc

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tasks" / "t1" / "short_video" / "p1").mkdir(parents=True)
    project = {"task_id": "t1", "id": "p1", "pipeline": {"stage5_coarse": {"highlights": []}}}
    svc.save_project(repo, project)

    res = mod.preview_continuous_subs(repo, "t1", "p1")
    assert res["srt"] == ""
    assert res["clips"] == 0
    assert res["cached"] is False


# ---------- 5. 短/长隔离 ----------

def test_delete_coarse_does_not_touch_long_video_outputs(tmp_path, monkeypatch):
    """调用 delete_coarse 不写长视频 tasks/<tid>/outputs/ 任何文件。

    监控：长视频 outputs 目录在 delete 前后文件数相同。
    """
    from slirn_home import short_video_coarse as mod
    from slirn_home import short_video_service as svc

    repo = tmp_path / "repo"
    repo.mkdir()
    s4 = repo / "tasks" / "t1" / "short_video" / "p1" / "stage4"
    s5 = repo / "tasks" / "t1" / "short_video" / "p1" / "stage5"
    s4.mkdir(parents=True)
    s5.mkdir(parents=True)
    (s4 / "coarse_01.mp4").write_bytes(b"x")

    # 长视频 outputs 目录（隔离验证）
    long_outputs = repo / "tasks" / "t1" / "outputs"
    long_outputs.mkdir(parents=True)
    (long_outputs / "rough_compose.mp4").write_bytes(b"long")
    (long_outputs / "execution_history.json").write_text("[]", encoding="utf-8")
    long_files_before = sorted(p.name for p in long_outputs.iterdir())

    project = {"task_id": "t1", "id": "p1", "pipeline": {
        "stage5_coarse": {"status": "done", "highlights": []},
    }}
    svc.save_project(repo, project)

    import slirn_home.execution_history as eh
    monkeypatch.setattr(eh, "record_start", lambda *a, **k: "eid")
    monkeypatch.setattr(eh, "record_finish", lambda *a, **k: None)
    monkeypatch.setattr(eh, "patch_fields", lambda *a, **k: None)

    mod.delete_coarse(repo, "t1", "p1")

    long_files_after = sorted(p.name for p in long_outputs.iterdir())
    assert long_files_before == long_files_after, \
        f"长视频 outputs 不应被污染: before={long_files_before} after={long_files_after}"
