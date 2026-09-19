"""测试切分修剪清单服务 — REQ-20260916-008。

单元层（纯函数，无网络/无 ASR）：token 对齐、清单生成（保留/更正/删除/切分
子段父编号+子编号/降级）、落盘往返、面板三态渲染、阶段状态推进。
完整浏览器链路走 E2E（work/REQ-20260916-008-cutlist/）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
SLIRN_STANDALONE = FUNCLIP_ROOT.parent / "slirn-standalone"

if str(FUNCLIP_ROOT) not in sys.path:
    sys.path.insert(0, str(FUNCLIP_ROOT))
if SLIRN_STANDALONE.exists() and str(SLIRN_STANDALONE) not in sys.path:
    sys.path.insert(0, str(SLIRN_STANDALONE))


def _make_mgr(tmp_path: Path):
    from tasklib import TaskManager

    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"fake-video")
    return TaskManager(tmp_path), video


def _write_subtitle(mgr, tid: str, segs: list[dict]) -> Path:
    outputs = mgr.tasks_dir / tid / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "subtitle.json").write_text(
        json.dumps({"version": 1, "segments": segs}, ensure_ascii=False), encoding="utf-8"
    )
    return outputs


# ---------- 纯函数 ----------

def test_tokenize_and_join():
    from slirn_home.cutlist_service import join_tokens, tokenize

    assert tokenize("嗯嗯那个我们今天讲一下神经网络") == list("嗯嗯那个我们今天讲一下神经网络")
    assert tokenize("hello world 你好 GPT-4") == ["hello", "world", "你", "好", "GPT-4"]
    assert join_tokens(["hello", "世", "界", "GPT-4"]) == "hello世界 GPT-4"
    assert join_tokens(["好", "的", "。"]) == "好的"  # 去尾部标点（Text2SRT 同款）


def test_align_tokens_and_blocks():
    """贪心子序列对齐：剔除语气词/重复后剩余 token 的连续块。"""
    from slirn_home.cutlist_service import align_tokens, contiguous_blocks

    orig = list("嗯嗯那个我们今天讲一下神经网络")
    keep = list("我们今天讲一下神经网络")
    marks = align_tokens(orig, keep)
    assert contiguous_blocks(marks) == [(4, 14)], "剔除行首「嗯嗯那个」→ 单块"

    # 重复语句只保留一次 → 两块（重复段中间被剔除）
    rep = list("所以我们所以我们所以我们看到")
    marks2 = align_tokens(rep, list("所以我们看到"))
    assert contiguous_blocks(marks2) == [(0, 3), (12, 13)], "10.1/10.3 式两块"

    # 对不上（用户改写）→ 空匹配
    assert align_tokens(list("abc"), ["x", "y"]) == []


def test_partition_tokens_full_alternation():
    """REQ-20260916-011 — 完整交替划分：keep 块 + delete 洞（含首尾）不重不漏。"""
    from slirn_home.cutlist_service import partition_tokens

    # 命中中间一块 → 首洞 + keep + 尾洞
    assert partition_tokens(15, list(range(3, 7))) == [
        ("delete", 0, 2), ("keep", 3, 6), ("delete", 7, 14)]

    # 两块 → 首无洞（0 起）、中洞、尾洞
    assert partition_tokens(14, [0, 1, 2, 3, 12, 13]) == [
        ("keep", 0, 3), ("delete", 4, 11), ("keep", 12, 13)]

    # 全命中 → 无洞单块
    assert partition_tokens(5, [0, 1, 2, 3, 4]) == [("keep", 0, 4)]

    # 全未命中 → 单 delete 洞（对齐层会先 fallback，此为纯函数语义）
    assert partition_tokens(4, []) == [("delete", 0, 3)]


def test_ms2srt_format():
    from slirn_home.cutlist_service import ms2srt

    assert ms2srt(0) == "00:00:00,000"
    assert ms2srt(7980) == "00:00:07,980"
    assert ms2srt(3661500) == "01:01:01,500"


# ---------- build_cutlist ----------

def _seg(i, start_ms, end_ms, text, tokens=None, token_ts=None):
    from slirn_home.cutlist_service import ms2srt

    seg = {"i": i, "start_ms": start_ms, "end_ms": end_ms,
           "start": ms2srt(start_ms), "end": ms2srt(end_ms), "text": text}
    if tokens is not None:
        seg["tokens"] = tokens
        seg["token_ts"] = token_ts
    return seg


def test_build_cutlist_full(tmp_path: Path):
    """全类别组合：keep/fix 带入编号不变、delete 剔除、split 对齐切子段（父编号.子序号）。"""
    from slirn_home.cutlist_service import build_cutlist

    # 段1：嗯嗯那个(4 token) + 我们今天讲一下神经网络(11 token)，token_ts 逐字 200ms
    t1 = [[400 * k, 400 * k + 360] for k in range(15)]
    segs = [
        _seg(1, 0, 6000, "嗯嗯那个我们今天讲一下神经网络",
             tokens=list("嗯嗯那个我们今天讲一下神经网络"), token_ts=t1),
        _seg(2, 6000, 9000, "今天讲第一课"),
        _seg(3, 9000, 12000, "那个那个就是说我们开始吧",
             tokens=list("那个那个就是说我们开始吧"),
             token_ts=[[300 * k, 300 * k + 280] for k in range(12)]),
        _seg(4, 12000, 15000, "神精网络入门"),
        _seg(5, 15000, 18000, "与主题无关的废话"),
    ]
    rev = {"entries": [
        # 采纳模型 split 建议 → 对齐切子段：token[4..14] = 我们今天讲一下神经网络
        {"i": 1, "category": "split", "keep_text": "我们今天讲一下神经网络",
         "decision": "accept", "user_note": ""},
        # 手动改判 keep（模型原建议 split）→ 整段带入、原编号不变
        {"i": 2, "category": "split", "keep_text": "第一课",
         "decision": "keep", "user_note": ""},
        # 采纳 delete → 剔除
        {"i": 3, "category": "delete", "keep_text": None,
         "decision": "accept", "user_note": ""},
        # 采纳 fix → 文本=更正后（keep_text），时间段整段
        {"i": 4, "category": "fix", "keep_text": "神经网络入门",
         "decision": "accept", "user_note": ""},
        # 手动 delete（模型建议 keep）→ 剔除
        {"i": 5, "category": "keep", "keep_text": None,
         "decision": "delete", "user_note": ""},
    ]}
    cut = build_cutlist({"segments": segs}, rev)
    items = cut["items"]
    # 完整划分（REQ-20260916-011）：段1 产 1.1 delete 洞（嗯嗯那个）+ 1.2 keep
    assert [it["id"] for it in items] == ["1.1", "1.2", "2", "4"], "继承编号不重排（3/5 已剔除）"
    s = cut["stats"]
    assert (s["kept"], s["fixed"], s["split_parents"]) == (1, 1, 1)
    assert (s["split_subs"], s["split_subs_delete"], s["dropped"]) == (2, 1, 2)
    assert s["brought"] == 4

    # split 子段：完整划分 — 1.1 = 首洞「嗯嗯那个」（外扩到父段起点）
    d1, k1 = items[0], items[1]
    assert d1["kind"] == "split" and d1["mark"] == "delete" and d1["text"] == "嗯嗯那个"
    assert d1["start_ms"] == 0 and d1["end_ms"] == 400 * 4, "首洞外扩：父段起点 → 下一 keep 块首 token 起点"
    # 1.2 = keep 块：时间来自字级时间戳（token4 起 1600ms，token14 止 6000ms）
    assert k1["sub"] == 2 and k1["mark"] == "keep" and k1["source_i"] == 1
    assert k1["start_ms"] == 400 * 4 and k1["end_ms"] == 400 * 14 + 360
    assert k1["start"] == "00:00:01,600"
    assert k1["text"] == "我们今天讲一下神经网络"
    assert k1["orig_text"] == "嗯嗯那个我们今天讲一下神经网络"
    assert k1["fallback"] is False
    assert k1["start_ms"] == d1["end_ms"], "相邻段共享边界（父段被完全二分）"

    # keep：原文本原时间段
    k = items[2]
    assert k["kind"] == "keep" and k["text"] == "今天讲第一课" and k["start_ms"] == 6000

    # fix：更正后文本
    f = items[3]
    assert f["kind"] == "fix" and f["text"] == "神经网络入门" and f["orig_text"] == "神精网络入门"


def test_build_cutlist_fix_user_note_priority():
    """REQ-20260917-026 — fix 生效文本三级：输入框微调值（user_note）→ 模型更正（keep_text）→ 原文兜底。"""
    from slirn_home.cutlist_service import build_cutlist

    segs = [
        _seg(1, 0, 3000, "神精网络入门"),
        _seg(2, 3000, 6000, "今天讲第一课"),
        _seg(3, 6000, 9000, "我们开始吧"),
    ]
    rev = {"entries": [
        # 手动改判 fix + 输入框微调过 → 微调值生效
        {"i": 1, "category": "fix", "keep_text": "神经网络入门",
         "decision": "fix", "user_note": "神经网络入门（第一讲）"},
        # 采纳 fix + 未填 → 模型更正文本生效
        {"i": 2, "category": "fix", "keep_text": "今天讲·第一课",
         "decision": "accept", "user_note": ""},
        # 改判 fix + 建议文本缺失 → 原文兜底
        {"i": 3, "category": "keep", "keep_text": None,
         "decision": "fix", "user_note": ""},
    ]}
    cut = build_cutlist({"segments": segs}, rev)
    texts = {it["id"]: it["text"] for it in cut["items"]}
    assert texts["1"] == "神经网络入门（第一讲）", "输入框微调值优先（下一阶段自动取用）"
    assert texts["2"] == "今天讲·第一课", "未微调 → 模型更正 keep_text"
    assert texts["3"] == "我们开始吧", "都缺 → 原文兜底"
    assert cut["stats"]["fixed"] == 3
    # orig_text 始终保留原文，供面板对照
    assert [it["orig_text"] for it in cut["items"]][0] == "神精网络入门"


def test_build_cutlist_split_multi_blocks_and_user_note(tmp_path: Path):
    """重复语句 → 完整三段（keep/delete 洞/keep）；手动「切分修剪后内容」优先于模型建议。"""
    from slirn_home.cutlist_service import build_cutlist

    tokens = list("所以我们所以我们所以我们看到")  # 14 token
    segs = [_seg(10, 0, 4200, "所以我们所以我们所以我们看到",
                 tokens=tokens, token_ts=[[300 * k, 300 * k + 280] for k in range(14)])]
    rev = {"entries": [
        {"i": 10, "category": "review", "keep_text": None,  # 模型建议复核
         "decision": "split", "user_note": "所以我们看到"},  # 手动改判切分+内容
    ]}
    cut = build_cutlist({"segments": segs}, rev)
    items = cut["items"]
    # 完整划分：keep(0-3) / delete 洞(4-11) / keep(12-13)
    assert [it["id"] for it in items] == ["10.1", "10.2", "10.3"], "时间序编号不分标记"
    assert [(it["mark"], it["text"]) for it in items] == [
        ("keep", "所以我们"), ("delete", "所以我们所以我们"), ("keep", "看到")]
    assert items[0]["start_ms"] == 0 and items[0]["end_ms"] == 300 * 3 + 280
    # delete 洞外扩：前一段终点 → 下一 keep 块首 token 起点（静默随洞删净）
    assert items[1]["start_ms"] == 300 * 3 + 280 and items[1]["end_ms"] == 300 * 12
    assert items[2]["start_ms"] == 300 * 12
    assert all(it["kind"] == "split" for it in items)
    s = cut["stats"]
    assert s["split_parents"] == 1 and s["split_subs"] == 3 and s["split_subs_delete"] == 1
    # 保留时长 = 执行口径（只计 keep 子段：10.1 [0,1180] + 10.3 [3600,4180]）
    assert s["keep_duration_ms"] == (300 * 3 + 280) + (300 * 13 + 280 - 300 * 12)


def test_build_cutlist_fallback_without_tokens(tmp_path: Path):
    """旧格式字幕（无字级时间戳）/目标文字对不上 → 整段单子段降级（fallback 标记）。"""
    from slirn_home.cutlist_service import build_cutlist

    segs = [_seg(7, 5000, 8000, "嗯那个我们开始吧")]  # 无 tokens/token_ts
    rev = {"entries": [
        {"i": 7, "category": "split", "keep_text": "我们开始吧",
         "decision": "accept", "user_note": ""},
    ]}
    cut = build_cutlist({"segments": segs}, rev)
    (it,) = cut["items"]
    assert it["id"] == "7.1" and it["fallback"] is True
    assert it["start_ms"] == 5000 and it["end_ms"] == 8000  # 整段时间
    assert it["text"] == "我们开始吧"  # 文本=切分后文字
    assert it["mark"] == "keep", "fallback 段建议保留（可手工翻转为整段删）"
    assert cut["stats"]["fallback"] == 1

    # 有 tokens 但目标文字完全对不上（用户改写）→ 同样降级整段
    segs2 = [_seg(8, 0, 1000, "好的没问题", tokens=list("好的没问题"),
                  token_ts=[[250 * k, 250 * k + 200] for k in range(4)])]
    rev2 = {"entries": [
        {"i": 8, "category": "split", "keep_text": "完全可以呀",
         "decision": "accept", "user_note": ""},
    ]}
    (it2,) = build_cutlist({"segments": segs2}, rev2)["items"]
    assert it2["id"] == "8.1" and it2["fallback"] is True and it2["text"] == "完全可以呀"


def test_manual_marks_flip_and_zero_len_hole():
    """REQ-20260916-011 — 子段级手工翻转（mark_manual）+ 零长洞跳过。"""
    from slirn_home.cutlist_service import build_cutlist

    # 3 token：甲(0-300) 乙(300-300 零长) 丙(300-600)，target "甲丙" → 洞(1,1) 零长跳过
    segs = [_seg(3, 0, 600, "甲乙丙", tokens=list("甲乙丙"),
                 token_ts=[[0, 300], [300, 300], [300, 600]])]
    rev = {"entries": [{"i": 3, "category": "split", "keep_text": "甲丙",
                        "decision": "accept", "user_note": ""}]}
    cut = build_cutlist({"segments": segs}, rev)
    assert [(it["id"], it["mark"]) for it in cut["items"]] == [("3.1", "keep"), ("3.2", "keep")]
    assert cut["items"][0]["text"] == "甲" and cut["items"][1]["text"] == "丙"

    # 手工翻转：10.2 洞 → keep（保留重复段）
    tokens = list("所以我们所以我们所以我们看到")
    segs2 = [_seg(10, 0, 4200, "所以我们所以我们所以我们看到",
                  tokens=tokens, token_ts=[[300 * k, 300 * k + 280] for k in range(14)])]
    rev2 = {"entries": [{"i": 10, "category": "split", "keep_text": "所以我们看到",
                         "decision": "accept", "user_note": ""}]}
    cut2 = build_cutlist({"segments": segs2}, rev2,
                         manual_marks={"10.2": "keep", "10.9": "delete"})  # 10.9 不存在 → 忽略
    m2 = next(it for it in cut2["items"] if it["id"] == "10.2")
    assert m2["mark"] == "keep" and m2["mark_manual"] is True, "建议 delete 被手工翻转为 keep"
    assert cut2["stats"]["mark_flipped"] == 1
    assert cut2.get("manual_marks") == {"10.2": "keep", "10.9": "delete"}, "手工决策随清单落盘"
    # 翻转后三段全 keep：0→4180 连续覆盖（尾 token 止于 4180，父段边界 4200）
    assert cut2["stats"]["keep_duration_ms"] == 300 * 13 + 280


def test_actions_reclass_and_effective_units():
    """REQ-20260916-011 — 字幕级改判：keep 原切分整段保留 / split 原保留划子段 /
    delete 整条剔除（条目仍产出供渲染，执行口径剔除）。"""
    from slirn_home.cutlist_service import build_cutlist, effective_keep_units

    segs = [
        _seg(1, 0, 4200, "所以我们所以我们所以我们看到",
             tokens=list("所以我们所以我们所以我们看到"),
             token_ts=[[300 * k, 300 * k + 280] for k in range(14)]),
        _seg(2, 4200, 4800, "今天讲第一课",
             tokens=list("今天讲第一课"),
             token_ts=[[4200 + 100 * k, 4200 + 100 * k + 90] for k in range(6)]),
        _seg(3, 5000, 6000, "神经网络的入门"),
    ]
    rev = {"entries": [
        {"i": 1, "category": "split", "keep_text": "所以我们看到", "decision": "accept", "user_note": ""},
        {"i": 2, "category": "keep", "keep_text": None, "decision": "accept", "user_note": "第一课"},
        {"i": 3, "category": "keep", "keep_text": None, "decision": "accept", "user_note": ""},
    ]}

    # 三种改判一次到位：1=keep（原 split 整段保留）、2=split（原 keep 划子段）、3=delete（剔除）
    cut = build_cutlist({"segments": segs}, rev,
                        actions={"1": "keep", "2": "split", "3": "delete"})
    it1 = next(it for it in cut["items"] if it["source_i"] == 1)
    assert it1["kind"] == "keep" and it1["id"] == "1" and it1["sub"] is None
    assert it1["start_ms"] == 0 and it1["end_ms"] == 4200 and it1["text"].startswith("所以我们")

    # action=split：原 keep 段2 → 按 user_note「第一课」划子段（3 字命中尾部）
    it2s = [it for it in cut["items"] if it["source_i"] == 2]
    assert [it["id"] for it in it2s] == ["2.1", "2.2"], "「今」被剔除 → delete 洞 + keep 尾块"
    assert it2s[0]["mark"] == "delete" and it2s[1]["mark"] == "keep"
    assert it2s[1]["text"] == "第一课"

    # action=delete：段3 条目仍产出（渲染置灰）但执行口径剔除
    it3 = next(it for it in cut["items"] if it["source_i"] == 3)
    assert it3["kind"] == "keep"
    units = effective_keep_units(cut)
    assert [u["source_i"] for u in units if u["source_i"] == 3] == [], "action=delete 整条剔除"
    assert any(u["source_i"] == 1 for u in units), "改判 keep 的整段保留"
    assert cut["actions"] == {"1": "keep", "2": "split", "3": "delete"}
    assert cut["stats"]["action_changed"] == 3

    # 无改判时段1 为完整三段划分；时长 = keep 块（洞剔除）
    cut0 = build_cutlist({"segments": segs}, rev)
    ids0 = [it["id"] for it in cut0["items"]]
    assert ids0 == ["1.1", "1.2", "1.3", "2", "3"]
    assert sum(1 for u in effective_keep_units(cut0) if u["source_i"] == 1) == 2, "洞剔除后剩两 keep 子段"


def test_resplit_segment():
    """REQ-20260916-011 M3 — 单段重切：回写 user_note/decision、清本组手工决策、
    保留其它组；命中 0 / 无字级时间戳 / 空内容 → 宁可不切不可错切。"""
    from slirn_home.cutlist_service import resplit_segment

    tokens = list("所以我们所以我们所以我们看到")  # 14 token
    segs = [
        _seg(1, 0, 4200, "所以我们所以我们所以我们看到",
             tokens=tokens, token_ts=[[300 * k, 300 * k + 280] for k in range(14)]),
        _seg(2, 4200, 4800, "今天讲第一课"),  # 无字级时间戳
    ]
    rev = {"entries": [
        {"i": 1, "category": "split", "keep_text": "所以我们看到",
         "decision": "accept", "user_note": ""},
        {"i": 2, "category": "keep", "keep_text": None, "decision": "accept", "user_note": ""},
    ]}
    saved = {"manual_marks": {"1.2": "keep"}, "actions": {"2": "delete"}}

    cut, rev2, err = resplit_segment({"segments": segs}, rev, 1, "所以我们所以我们看到", saved)
    assert err == ""
    subs = [it for it in cut["items"] if it["source_i"] == 1]
    assert [it["id"] for it in subs] == ["1.1", "1.2", "1.3"], "新 target 两 keep 块 + 中洞"
    assert [it["mark"] for it in subs] == ["keep", "delete", "keep"]
    assert subs[0]["text"] == "所以我们所以我们" and subs[2]["text"] == "看到"
    # 回写修订（单一事实源）
    e1 = next(e for e in rev2["entries"] if e["i"] == 1)
    assert e1["decision"] == "split" and e1["user_note"] == "所以我们所以我们看到"
    # 本组手工清空（1.2 不再手工 keep → 建议 delete 生效）；其它组改判保留
    assert "1.2" not in (cut.get("manual_marks") or {})
    assert cut.get("actions") == {"2": "delete"}
    assert "manual_marks" not in cut or not any(k.startswith("1.") for k in cut["manual_marks"])

    # 命中 0 / 无字级时间戳 / 空内容 / 未知条目 → 不改不落盘
    rev_before = json.loads(json.dumps(rev))
    for si, tt, want in [(1, "完全无关的内容", "对不上"),
                         (2, "第一课", "字级时间戳"),
                         (1, "  ", "不能为空"),
                         (99, "任意", "未找到")]:
        c, r, err = resplit_segment({"segments": segs}, rev, si, tt, saved)
        assert (c, r) == (None, None) and want in err, f"{si}/{tt}: {err}"
    assert rev == rev_before, "失败路径不改动修订"


def test_build_cutlist_accept_review_falls_back_keep():
    """防御：采纳 review 建议（无明确处理）→ 按整段保留带入，不丢内容。"""
    from slirn_home.cutlist_service import build_cutlist

    segs = [_seg(1, 0, 900, "拿不准的内容")]
    rev = {"entries": [{"i": 1, "category": "review", "keep_text": None,
                        "decision": "accept", "user_note": ""}]}
    (it,) = build_cutlist({"segments": segs}, rev)["items"]
    assert it["kind"] == "keep" and it["id"] == "1" and it["text"] == "拿不准的内容"


def test_save_and_load_roundtrip(tmp_path: Path):
    from slirn_home.cutlist_service import build_cutlist, load_cutlist, save_cutlist

    segs = [_seg(1, 0, 500, "正常一句")]
    rev = {"entries": [{"i": 1, "category": "keep", "keep_text": None,
                        "decision": "accept", "user_note": ""}]}
    cut = build_cutlist({"segments": segs}, rev)
    p = save_cutlist(tmp_path, cut)
    assert p.name == "cutlist.json"
    loaded = load_cutlist(tmp_path)
    assert loaded["items"] == cut["items"]
    assert loaded["saved_at"], "落盘时补 saved_at"

    assert load_cutlist(tmp_path / "nope") is None
    (tmp_path / "cutlist.json").write_text("{broken", encoding="utf-8")
    assert load_cutlist(tmp_path) is None


# ---------- 面板渲染 + 阶段状态 ----------

def _write_revision(outputs: Path, entries: list[dict]) -> None:
    (outputs / "revision.json").write_text(
        json.dumps({"version": 1, "created_at": "2026-09-16T10:00:00", "saved_at": "2026-09-16T10:05:00",
                    "segments_count": len(entries), "entries": entries},
                   ensure_ascii=False),
        encoding="utf-8",
    )


def test_render_cutlist_zone_states(tmp_path: Path):
    from slirn_home.app import _render_cutlist_zone

    m, video = _make_mgr(tmp_path)
    t = m.create(name="切分任务", original_video=video)
    tid = t.task_id

    # 状态 1a：无修订数据 → 引导去字幕修订
    h1 = _render_cutlist_zone(tid, m.get(tid), m)
    assert "请先完成上一阶段「字幕修订」" in h1
    assert 'data-action="wb-stage" data-pane="subtitle_review"' in h1

    # 状态 1b：有建议未决策 → 提示剩余数
    outputs = _write_subtitle(m, tid, [_seg(1, 0, 500, "一句")])
    _write_revision(outputs, [
        {"i": 1, "category": "keep", "keep_text": None, "decision": "accept", "user_note": ""},
        {"i": 2, "category": "delete", "keep_text": None, "decision": "pending", "user_note": ""},
    ])
    h1b = _render_cutlist_zone(tid, m.get(tid), m)
    assert "还有 1/2 条未决策" in h1b

    # 状态 2：全部决策 → 预览清单（服务端现算不落盘）
    _write_revision(outputs, [
        {"i": 1, "category": "keep", "keep_text": None, "decision": "accept", "user_note": ""},
        {"i": 2, "category": "split", "keep_text": "我们开始吧",
         "decision": "accept", "user_note": ""},
        {"i": 3, "category": "fix", "keep_text": "神经网络", "decision": "accept", "user_note": ""},
        {"i": 4, "category": "delete", "keep_text": None, "decision": "accept", "user_note": ""},
    ])
    outputs_join = _write_subtitle(m, tid, [
        _seg(1, 0, 500, "正常一句"),
        _seg(2, 500, 1500, "那个我们开始吧"),
        _seg(3, 1500, 2200, "神精网络"),
        _seg(4, 2200, 3000, "废话"),
    ])
    assert outputs_join == outputs
    h2 = _render_cutlist_zone(tid, m.get(tid), m)
    assert "✂️ 处理剪辑 · 第 3 步：切分修剪" in h2
    assert "带入 <b>3</b> 段" in h2 and "剔除 1 条" in h2
    assert "编号继承修订阶段" in h2
    # 分组渲染（REQ-20260916-011）：切分组 = 组头（原段→切分后）+ 子段行（data-mark）
    assert 'id="slirn-cut-list"' in h2
    assert 'class="slirn-cut-ghead"' in h2
    assert "原段：「那个我们开始吧」" in h2 and "切分后：「我们开始吧」" in h2
    assert 'data-id="2.1"' in h2, "split 子段编号 父.子"
    assert 'data-mark="keep"' in h2 and 'class="slirn-cut-mark"' in h2
    assert ">✅ 保留</span>" in h2, "标记徽章（可点翻转）"
    assert 'data-mark-init="keep"' in h2, "初始标记快照（翻转检测用）"
    assert 'data-cut-act="play-keep"' in h2, "组头试听（keep 连续跳播）"
    assert 'data-cut-act="resplit"' in h2 and "✂️ 重新切分" in h2, "切分组重切入口（M3）"
    assert 'data-target="我们开始吧"' in h2, "组级切分后内容（重切预填/保存校验用）"
    # 整段组单行（whole）：类别徽章在行内 + 组级决策下拉默认「维持原状」（REQ-20260917-027）
    assert 'slirn-cut-row whole' in h2
    assert 'data-kind="fix">内容更正</span>' in h2 and "神经网络" in h2
    assert 'class="slirn-cut-abadge slirn-cut-actsel" data-actsel' in h2
    assert '<option value="" selected>维持原状</option>' in h2
    assert h2.count("<option value=") >= 4 and '<option value="delete">❌ 改判删除</option>' in h2
    assert "1 条切分段缺少字级时间戳" in h2
    # 播放器 + 按钮动作（保存决策 / 生成清单 / 播放）
    assert 'id="slirn-cut-player"' in h2
    assert 'data-action="save-cut-decisions"' in h2 and "保存切分决策" in h2
    assert 'data-action="build-cutlist"' in h2 and "生成切分清单并完成本阶段" in h2
    assert 'data-action="play-cut-video"' in h2
    assert not (outputs / "cutlist.json").exists(), "预览不落盘"

    # 已落盘 → 统计行标注「清单已生成」+ 主按钮状态化为「重新执行」
    from slirn_home import cutlist_service
    cutlist_service.save_cutlist(outputs, cutlist_service.build_cutlist(
        {"segments": json.loads((outputs / "subtitle.json").read_text(encoding="utf-8"))["segments"]},
        json.loads((outputs / "revision.json").read_text(encoding="utf-8")),
    ))
    h3 = _render_cutlist_zone(tid, m.get(tid), m)
    assert "清单已生成" in h3
    assert 'data-action="rebuild-cutlist"' in h3 and "重新执行切分修剪" in h3
    assert 'data-action="build-cutlist"' not in h3, "已生成过 → 不再显示「生成」按钮"
    # 修订比清单新 → 过期黄条（构造 saved_at 早于修订 saved_at=10:05）
    data = json.loads((outputs / "cutlist.json").read_text(encoding="utf-8"))
    data["saved_at"] = "2026-09-16T09:00:00"
    (outputs / "cutlist.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    h4 = _render_cutlist_zone(tid, m.get(tid), m)
    assert "已保存的切分清单可能过期" in h4

    # 已保存手工决策（翻转 + 字幕级改判）→ 渲染恢复（data-act / ✏️ 手工标识）
    cut2 = cutlist_service.build_cutlist(
        {"segments": json.loads((outputs / "subtitle.json").read_text(encoding="utf-8"))["segments"]},
        json.loads((outputs / "revision.json").read_text(encoding="utf-8")),
        manual_marks={"2.1": "delete"}, actions={"1": "delete"})
    cutlist_service.save_cutlist(outputs, cut2)
    h5 = _render_cutlist_zone(tid, m.get(tid), m)
    assert 'data-act="delete"' in h5 and '<option value="delete" selected>❌ 改判删除</option>' in h5
    assert "❌ 删除 ✏️" in h5, "手工翻转恢复（✏️ 手工标识）"

    # 改判切分的整段组（keep 无切分内容 → 防御维持原状）→ 整段行 + ✂️ 入口 + 原文预填
    cut3 = cutlist_service.build_cutlist(
        {"segments": json.loads((outputs / "subtitle.json").read_text(encoding="utf-8"))["segments"]},
        json.loads((outputs / "revision.json").read_text(encoding="utf-8")),
        actions={"1": "split"})
    cutlist_service.save_cutlist(outputs, cut3)
    h6 = _render_cutlist_zone(tid, m.get(tid), m)
    assert 'data-act="split"' in h6 and '<option value="split" selected>✂️ 改判切分</option>' in h6
    g1 = h6[h6.find('data-source-i="1"'):h6.find('data-source-i="2"')]
    assert 'slirn-cut-row whole' in g1 and 'data-cut-act="resplit"' in g1
    assert 'data-orig-text="正常一句"' in g1, "整段组原文（改判切分编辑区预填）"


def test_wb_stage_states_with_cutlist(tmp_path: Path):
    """cutlist.json 落盘 → 切分修剪 done（磁盘判定，状态未推进也认）。"""
    from slirn_home.app import _wb_stage_states

    m, video = _make_mgr(tmp_path)
    t = m.create(name="t", original_video=video)
    tid = t.task_id
    outputs = m.tasks_dir / tid / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "subtitle.json").write_text(
        json.dumps({"version": 1, "segments": [_seg(1, 0, 500, "一句")]}), encoding="utf-8")
    (outputs / "revision.json").write_text(
        json.dumps({"version": 1, "entries": [{"i": 1, "decision": "accept"}]}), encoding="utf-8")
    assert _wb_stage_states(m.get(tid))[3] == "current"
    (outputs / "cutlist.json").write_text(
        json.dumps({"version": 1, "items": [{"id": "1"}]}), encoding="utf-8")
    assert _wb_stage_states(m.get(tid)) == ["done", "done", "done", "done", "current"] + ["pending"] * 2
