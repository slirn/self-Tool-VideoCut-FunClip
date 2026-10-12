"""REQ-20261013-104：短视频 Stage 6 字幕优化（行级 + 词频 + 批量替换 + 行内切分）。

覆盖：
1. parse_srt 基础解析 + 容错（id 缺号）
2. parse_occurrences 防御式 LLM 输出（剥围栏 / 未知 id 丢弃 / 非法 occurrence 丢弃）
3. aggregate_words 词频 chips（按 after 聚合 + 出现行数 + 频次降序）
4. apply_to_segments accept/edit/reject 三态
5. resplit_line 时间戳按字符数比例内插
6. batch_replace 替换次数
7. 整链路 run_optimize_one（mock _call_llm）+ save_decisions 落盘 + reload 一致
8. 短/长隔离：save_decisions 后长视频 outputs 目录文件数不变
"""

import json
from pathlib import Path

import pytest


# ---------- 1. parse_srt ----------

def test_parse_srt_basic():
    from slirn_home.short_video_opt_review import parse_srt, format_srt
    srt = "1\n00:00:00,000 --> 00:00:02,500\n你好世界\n\n2\n00:00:02,500 --> 00:00:05,000\n这是第二行"
    out = parse_srt(srt)
    assert len(out) == 2
    assert out[0]["id"] == "1"
    assert out[0]["start_ms"] == 0
    assert out[0]["end_ms"] == 2500
    assert out[0]["text"] == "你好世界"
    # format_srt 还原
    assert "00:00:02,500 --> 00:00:05,000" in format_srt(out)


def test_parse_srt_renumber_on_duplicate_ids():
    from slirn_home.short_video_opt_review import parse_srt
    srt = "5\n00:00:00,000 --> 00:00:01,000\nA\n\n5\n00:00:01,000 --> 00:00:02,000\nB"
    out = parse_srt(srt)
    # 缺号 → 补 1..N
    assert out[0]["id"] == "1"
    assert out[1]["id"] == "2"


def test_parse_srt_empty_text_skipped():
    from slirn_home.short_video_opt_review import parse_srt
    srt = "1\n00:00:00,000 --> 00:00:02,500\n\n\n2\n00:00:02,500 --> 00:00:05,000\nX"
    out = parse_srt(srt)
    # 空行不进（regex 要求 \n 文本）
    assert [e["id"] for e in out] == ["1", "2"]


# ---------- 2. parse_occurrences ----------

def test_parse_occurrences_strips_fence_and_drops_unknown():
    from slirn_home.short_video_opt_review import parse_occurrences
    entries = [
        {"id": "1", "text": "我去公园玩了"},
        {"id": "2", "text": "然后回家了"},
    ]
    raw = (
        "```json\n"
        '[{"id":"1","occurrences":[{"before":"我去","after":"我昨天去","reason":"补时间"}]},'
        '{"id":"99","occurrences":[{"before":"x","after":"y"}]},'
        '{"id":"2","occurrences":[{"before":"","after":"xxx"}]}]'
        "```"
    )
    out = parse_occurrences(raw, entries)
    assert "1" in out
    assert out["1"][0]["after"] == "我昨天去"
    assert "99" not in out
    assert "2" not in out  # before 空 → 丢弃


def test_parse_occurrences_handles_legacy_unclear_field():
    from slirn_home.short_video_opt_review import parse_occurrences
    entries = [{"id": "1", "text": "我去公园"}]
    raw = '[{"id":"1","unclear":[{"before":"我去","after":"我昨天去"}]}]'
    out = parse_occurrences(raw, entries)
    assert out["1"][0]["after"] == "我昨天去"


def test_parse_occurrences_garbage_returns_empty():
    from slirn_home.short_video_opt_review import parse_occurrences
    entries = [{"id": "1", "text": "x"}]
    assert parse_occurrences("not json at all", entries) == {}
    assert parse_occurrences("", entries) == {}


# ---------- 3. aggregate_words ----------

def test_aggregate_words_dedup_by_after():
    from slirn_home.short_video_opt_review import aggregate_words
    occs = {
        "1": [{"before": "我去", "after": "我昨天去", "reason": ""}],
        "2": [{"before": "我去", "after": "我昨天去", "reason": ""}],
        "3": [{"before": "然后", "after": "随后", "reason": ""}],
    }
    out = aggregate_words(occs)
    assert out[0]["after"] == "我昨天去"
    assert out[0]["count"] == 2
    assert out[0]["lines"] == ["1", "2"]
    assert out[1]["after"] == "随后"
    assert out[1]["count"] == 1


# ---------- 4. apply_to_segments ----------

def test_apply_to_segments_accept_default():
    from slirn_home.short_video_opt_review import apply_to_segments
    entries = [{"id": "1", "start_ms": 0, "end_ms": 1000, "text": "我去公园"}]
    occs = {"1": [{"before": "我去", "after": "我昨天去"}]}
    out = apply_to_segments(entries, occs, {})
    assert out[0]["text"] == "我昨天去公园"


def test_apply_to_segments_reject_keeps_original():
    from slirn_home.short_video_opt_review import apply_to_segments
    entries = [{"id": "1", "start_ms": 0, "end_ms": 1000, "text": "我去公园"}]
    occs = {"1": [{"before": "我去", "after": "我昨天去"}]}
    decs = {"1": {"accept": False}}
    out = apply_to_segments(entries, occs, decs)
    assert out[0]["text"] == "我去公园"


def test_apply_to_segments_edit_overrides_after():
    from slirn_home.short_video_opt_review import apply_to_segments
    entries = [{"id": "1", "start_ms": 0, "end_ms": 1000, "text": "我去公园"}]
    occs = {"1": [{"before": "我去", "after": "我昨天去"}]}
    decs = {"1": {"accept": True, "edit": "我去公园去"}}
    out = apply_to_segments(entries, occs, decs)
    assert out[0]["text"] == "我去公园去公园"


# ---------- 5. resplit_line ----------

def test_resplit_line_interpolates_by_length():
    from slirn_home.short_video_opt_review import resplit_line
    entries = [{"id": "1", "start_ms": 0, "end_ms": 9000, "text": "我去公园玩了"}]
    new = resplit_line(entries, "1", ["我去", "公园玩了"])
    # 2 子段：id=1 / id=2
    assert len(new) == 2
    assert new[0]["text"] == "我去"
    assert new[1]["text"] == "公园玩了"
    # 时间连续 + 末段贴回 end_ms
    assert new[0]["start_ms"] == 0
    assert new[1]["end_ms"] == 9000
    # 中间点按字符数比例（2/6 = 3000ms）
    assert new[0]["end_ms"] == 3000
    assert new[1]["start_ms"] == 3000


def test_resplit_line_empty_splits_noop():
    from slirn_home.short_video_opt_review import resplit_line
    entries = [{"id": "1", "start_ms": 0, "end_ms": 1000, "text": "abc"}]
    new = resplit_line(entries, "1", [])
    assert new == entries


# ---------- 6. batch_replace ----------

def test_batch_replace_counts_substring():
    from slirn_home.short_video_opt_review import batch_replace
    entries = [
        {"id": "1", "start_ms": 0, "end_ms": 1000, "text": "我去我去"},
        {"id": "2", "start_ms": 0, "end_ms": 1000, "text": "我去公园"},
    ]
    new, n = batch_replace(entries, "我去", "我们")
    assert n == 3
    assert new[0]["text"] == "我们我们"
    assert new[1]["text"] == "我们公园"


# ---------- 7. 整链路 ----------

def test_run_optimize_one_with_mocked_llm(tmp_path, monkeypatch):
    """mock _call_llm 返固定 JSON，验证 occurrences + words 写 project.json。"""
    from slirn_home import short_video_opt_review as mod
    from slirn_home import short_video_service as svc
    import slirn_home.revision_service as rev

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tasks" / "t1" / "short_video" / "p1").mkdir(parents=True)
    # 写 refined srt
    s6 = repo / "tasks" / "t1" / "short_video" / "p1" / "stage5"
    s6.mkdir(parents=True)
    (s6 / "refined_01.srt").write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n我去公园\n\n2\n00:00:02,000 --> 00:00:05,000\n然后回家",
        encoding="utf-8",
    )
    # mock LLM
    def fake_call_llm(system, user, timeout=120):
        return ('[{"id":"1","occurrences":[{"before":"我去","after":"我昨天去"}]},'
                '{"id":"2","occurrences":[{"before":"然后","after":"随后"}]}]')
    monkeypatch.setattr(rev, "_call_llm", fake_call_llm)

    # 建项目
    project = {"task_id": "t1", "id": "p1", "pipeline": {"stage6_refine": {}}}
    svc.save_project(repo, project)

    res = mod.run_optimize_one(repo, "t1", "p1", 1)
    assert res["lines"] == 2
    assert res["occurrences"]["1"][0]["after"] == "我昨天去"
    # words 已聚合（按频次降序；频次相同按 after 字典序）
    after_set = {w["after"] for w in res["words"]}
    assert after_set == {"随后", "我昨天去"}
    # reload 落盘一致
    proj2 = svc.load_project(repo, "t1", "p1")
    opt = proj2["pipeline"]["stage6_refine"]["optimize"]
    assert "1:1" in opt["occurrences"]


def test_save_decisions_appends_to_srt(tmp_path, monkeypatch):
    """save_decisions 把 accept=True 决策应用到 refined srt 落盘。"""
    from slirn_home import short_video_opt_review as mod
    from slirn_home import short_video_service as svc
    import slirn_home.revision_service as rev

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tasks" / "t1" / "short_video" / "p1" / "stage5").mkdir(parents=True)
    srt = repo / "tasks" / "t1" / "short_video" / "p1" / "stage5" / "refined_01.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n我去公园\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(rev, "_call_llm", lambda *a, **k: "")

    project = {"task_id": "t1", "id": "p1", "pipeline": {"stage6_refine": {}}}
    svc.save_project(repo, project)

    # 先 run_optimize_one
    monkeypatch.setattr(rev, "_call_llm",
        lambda *a, **k: '[{"id":"1","occurrences":[{"before":"我去","after":"我昨天去"}]}]')
    mod.run_optimize_one(repo, "t1", "p1", 1)
    # save_decisions
    mod.save_decisions(repo, "t1", "p1", 1, {"1": {"accept": True}})
    new_text = srt.read_text(encoding="utf-8")
    assert "我昨天去" in new_text


# ---------- 8. 短/长隔离 ----------

def test_save_decisions_does_not_touch_long_video_outputs(tmp_path, monkeypatch):
    """save_decisions 不写长视频 tasks/<tid>/outputs 任何文件。"""
    from slirn_home import short_video_opt_review as mod
    from slirn_home import short_video_service as svc
    import slirn_home.revision_service as rev

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tasks" / "t1" / "short_video" / "p1" / "stage5").mkdir(parents=True)
    (repo / "tasks" / "t1" / "short_video" / "p1" / "stage5" / "refined_01.srt").write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nx\n", encoding="utf-8",
    )
    long_outputs = repo / "tasks" / "t1" / "outputs"
    long_outputs.mkdir(parents=True)
    (long_outputs / "rough_compose.mp4").write_bytes(b"long")
    (long_outputs / "execution_history.json").write_text("[]", encoding="utf-8")
    long_files_before = sorted(p.name for p in long_outputs.iterdir())

    project = {"task_id": "t1", "id": "p1", "pipeline": {"stage6_refine": {}}}
    svc.save_project(repo, project)
    monkeypatch.setattr(rev, "_call_llm", lambda *a, **k: "")

    mod.save_decisions(repo, "t1", "p1", 1, {"1": {"accept": True}})

    long_files_after = sorted(p.name for p in long_outputs.iterdir())
    assert long_files_before == long_files_after, \
        f"长视频 outputs 不应被污染: before={long_files_before} after={long_files_after}"
