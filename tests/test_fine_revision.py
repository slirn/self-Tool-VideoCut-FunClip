"""精剪修订（热词替换）服务测试 — REQ-20260916-017。

纯函数口径：解析防御（before 摘抄/热词归一/丢弃）、替换应用（重叠剔除）、
统计（撤销排除）、决策落盘。不触网（LLM 由上层注入，parse 层单测覆盖）。
"""
from pathlib import Path

import pytest

from slirn_home import fine_service as fs

LINES = [
    {"id": "10", "start_ms": 0, "end_ms": 2000, "text": "今天讲一下神精网络"},
    {"id": "10.2", "start_ms": 3000, "end_ms": 5000, "text": "爱在很多行业都有应用"},
    {"id": "11", "start_ms": 6000, "end_ms": 8000, "text": "神经网络这个词很形象"},
]
HOTWORDS = ["AI", "神经网络", "funclip"]


# =============== _normalize_rep ===============

def test_normalize_ok():
    r = fs._normalize_rep({"before": "神精网络", "after": "神经网络", "hotword": "神经网络"},
                          LINES[0]["text"], HOTWORDS)
    assert r == {"before": "神精网络", "after": "神经网络", "hotword": "神经网络"}


def test_normalize_hotword_backfill():
    """hotword 编造/缺失 → 从 after 包含的热词回填。"""
    r = fs._normalize_rep({"before": "爱", "after": "AI", "hotword": "人工智能"},
                          LINES[1]["text"], HOTWORDS)
    assert r and r["hotword"] == "AI"


def test_normalize_rejects():
    text = LINES[0]["text"]
    # before 不在原文（模型幻觉摘抄）
    assert fs._normalize_rep({"before": "深度学习", "after": "神经网络", "hotword": "神经网络"}, text, HOTWORDS) is None
    # after 与 before 相同（无意义替换）
    assert fs._normalize_rep({"before": "神精网络", "after": "神精网络", "hotword": "神经网络"}, text, HOTWORDS) is None
    # after 不含任何热词（自由改写）
    assert fs._normalize_rep({"before": "神精网络", "after": "深度学习模型", "hotword": "神经网络"}, text, HOTWORDS) is None
    # 空 before / 非 dict
    assert fs._normalize_rep({"before": "", "after": "AI", "hotword": "AI"}, text, HOTWORDS) is None
    assert fs._normalize_rep(None, text, HOTWORDS) is None


# =============== apply_replacements ===============

def test_apply_basic_with_pos():
    new_text, applied = fs.apply_replacements("今天讲一下神精网络",
                                              [{"before": "神精网络", "after": "神经网络", "hotword": "神经网络"}])
    assert new_text == "今天讲一下神经网络"
    assert applied[0]["pos"] == 5  # 「今天讲一下」5 字之后


def test_apply_overlapping_dropped():
    """同一位置的重复替换只应用一个（避免叠加替换出脏文本）。"""
    new_text, applied = fs.apply_replacements(
        "神精网络",
        [{"before": "神精网络", "after": "神经网络", "hotword": "神经网络"},
         {"before": "神精", "after": "神经", "hotword": "神经网络"}],
    )
    assert new_text == "神经网络"
    assert len(applied) == 1


def test_apply_multiple_disjoint():
    new_text, applied = fs.apply_replacements(
        "神经网洛和神精网络",
        [{"before": "神经网洛", "after": "神经网络", "hotword": "神经网络"},
         {"before": "神精网络", "after": "神经网络", "hotword": "神经网络"}],
    )
    assert new_text == "神经网络和神经网络"
    assert len(applied) == 2 and applied[0]["pos"] == 0 and applied[1]["pos"] == 5


# =============== parse_replacements ===============

def test_parse_fenced_and_filtering():
    raw = '''```json
    前后废话 [{"id": "10", "replacements": [
        {"before": "神精网络", "after": "神经网络", "hotword": "神经网络"}]},
      {"id": "999", "replacements": [{"before": "x", "after": "y", "hotword": "AI"}]},
      {"id": "10.2", "replacements": [{"before": "不在文里", "after": "AI", "hotword": "AI"},
        {"before": "爱", "after": "AI", "hotword": "AI"}]}]
    ```'''
    out = fs.parse_replacements(raw, LINES, HOTWORDS)
    assert set(out) == {"10", "10.2"}  # 未知 id 丢弃、before 不在文丢弃
    assert out["10.2"][0]["after"] == "AI" and out["10.2"][0]["hotword"] == "AI"


def test_parse_empty_array():
    assert fs.parse_replacements("[]", LINES, HOTWORDS) == {}
    assert fs.parse_replacements('说明文字 [] 收尾', LINES, HOTWORDS) == {}


def test_parse_bad_json_raises():
    with pytest.raises(ValueError):
        fs.parse_replacements("完全不是 JSON", LINES, HOTWORDS)


# =============== prompt ===============

def test_user_prompt_contains_hotwords_and_lines():
    p = fs.build_user_prompt("测试任务", HOTWORDS, LINES)
    assert "神经网络" in p and "AI" in p and "funclip" in p
    assert '"id": "10.2"' in p and "神精网络" in p


# =============== 统计与落盘 ===============

def _fake_fine(tmp_path: Path) -> Path:
    fine = {
        "version": 1, "model": "m", "provider": "p", "protocol": "openai",
        "created_at": "2026-09-16T10:00:00", "saved_at": None,
        "hotwords": HOTWORDS, "cutlist_saved_at": "", "lines_count": 3,
        "stats": {"replaced_lines": 2, "replacements": 3},
        "entries": [
            {"id": "10", "text": "今天讲一下神精网络", "new_text": "今天讲一下神经网络",
             "replacements": [
                 {"before": "神精网络", "after": "神经网络", "hotword": "神经网络", "pos": 6}],
             "reverted": False},
            {"id": "10.2", "text": "爱在很多行业都有应用", "new_text": "AI在很多行业都有应用",
             "replacements": [
                 {"before": "爱", "after": "AI", "hotword": "AI", "pos": 0},
                 {"before": "很多", "after": "AI很多", "hotword": "AI", "pos": 1}],
             "reverted": False},
        ],
    }
    p = tmp_path / fs.FINE_JSON
    p.write_text(__import__("json").dumps(fine, ensure_ascii=False), encoding="utf-8")
    return p


def test_word_stats_and_effective(tmp_path):
    _fake_fine(tmp_path)
    fine = fs.load_fine(tmp_path)
    assert fs.word_stats(fine) == [("AI", 2), ("神经网络", 1)]
    est = fs.effective_stats(fine)
    assert est == {"replaced_lines": 2, "replacements": 3, "reverted": 0}
    # 撤销 10.2 → AI 两处不再计入
    fine["entries"][1]["reverted"] = True
    assert fs.word_stats(fine) == [("神经网络", 1)]
    assert fs.effective_stats(fine)["replacements"] == 1


def test_save_decisions_roundtrip(tmp_path):
    _fake_fine(tmp_path)
    fine, changed = fs.save_decisions(tmp_path, ["10.2"])
    assert changed == 1 and fine["entries"][1]["reverted"] is True
    assert fine["saved_at"]  # 阶段完成标记
    # 再保存：撤销 10.2 + 恢复 10（全量口径）
    fine, changed = fs.save_decisions(tmp_path, ["10.2"])
    assert changed == 0  # 幂等：无变化
    fine, changed = fs.save_decisions(tmp_path, [])
    assert changed == 1 and fine["entries"][1]["reverted"] is False


def test_save_decisions_missing_file(tmp_path):
    with pytest.raises(RuntimeError):
        fs.save_decisions(tmp_path, [])
