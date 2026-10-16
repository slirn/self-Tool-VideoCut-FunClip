"""REQ-20261016-128 — Stage 3 提示词历史：删除单条 + UI 加删除按钮。"""

from __future__ import annotations

import sys
import pathlib

import pytest

# REQ-20261012-099：根目录加入 import 路径
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from slirn_home import short_video_service as svc  # noqa: E402
from slirn_home import short_video_ui as svui  # noqa: E402


# ---------- 服务层：delete_prompt_history ----------

def test_delete_prompt_history_removes_entry(tmp_path):
    """删非默认项 → history 少 1，default 不变。"""
    svc.save_default_prompt(tmp_path, "默认 A")
    svc.save_prompt_history(tmp_path, "额外 B")
    svc.save_prompt_history(tmp_path, "额外 C")

    r = svc.delete_prompt_history(tmp_path, "额外 B")
    assert r["removed_default"] is False
    assert "额外 B" not in [h["prompt"] for h in r["history"]]

    st = svc.load_prompt_settings(tmp_path)
    assert st["default_prompt"]["prompt"] == "默认 A"
    assert [h["prompt"] for h in st["history"]] == ["额外 C", "默认 A"]


def test_delete_prompt_history_clears_default_when_match(tmp_path):
    """删默认项 → history 少 1 + default_prompt = None（removed_default=True）。"""
    svc.save_default_prompt(tmp_path, "默认 X")
    svc.save_prompt_history(tmp_path, "额外 Y")

    r = svc.delete_prompt_history(tmp_path, "默认 X")
    assert r["removed_default"] is True
    assert r["history"] == [] or all(
        h["prompt"] != "默认 X" for h in r["history"]
    )

    st = svc.load_prompt_settings(tmp_path)
    assert st["default_prompt"] is None
    # 「额外 Y」仍在（不是默认，没被删）
    assert any(h["prompt"] == "额外 Y" for h in st["history"])


def test_delete_prompt_history_noop_when_not_in_history(tmp_path):
    """删不存在的 prompt → 列表不变，removed_default=False。"""
    svc.save_prompt_history(tmp_path, "已有的")
    r = svc.delete_prompt_history(tmp_path, "不存在的")
    assert r["removed_default"] is False
    assert [h["prompt"] for h in r["history"]] == ["已有的"]


def test_delete_prompt_history_empty_prompt_raises(tmp_path):
    """空 prompt → ShortVideoError。"""
    with pytest.raises(svc.ShortVideoError):
        svc.delete_prompt_history(tmp_path, "  ")


def test_save_default_prompt_inserts_into_full_history(tmp_path):
    """列表已满 10 条 + 新默认不在其中 → 强制覆盖最旧（REQ-128 修复点）。"""
    # 填 10 条非默认历史
    for i in range(10):
        svc.save_prompt_history(tmp_path, f"提示词 {i}")
    assert len(svc.load_prompt_settings(tmp_path)["history"]) == 10

    # 设为不在列表中的新默认 → 应入列（置顶）并覆盖最旧
    entry = svc.save_default_prompt(tmp_path, "全新默认 Z")
    assert entry["prompt"] == "全新默认 Z"
    st = svc.load_prompt_settings(tmp_path)
    assert st["default_prompt"]["prompt"] == "全新默认 Z"
    assert st["history"][0]["prompt"] == "全新默认 Z"
    assert len(st["history"]) == 10
    # 最旧的「提示词 0」应被覆盖
    assert not any(h["prompt"] == "提示词 0" for h in st["history"])


# ---------- UI 层：render_prompt_history_list 加删除按钮 + 括号注释 ----------

def test_render_prompt_history_list_includes_delete_button():
    """每条历史都有 🗑 删除按钮 + data-action='sv-stage3-delete-prompt'。"""
    settings = {
        "default_prompt": {"prompt": "默认 P", "updated_at": "2026-10-16T10:00:00"},
        "history": [
            {"prompt": "额外 H1", "updated_at": "2026-10-16T11:00:00"},
            {"prompt": "默认 P", "updated_at": "2026-10-16T10:00:00"},
        ],
    }
    html = svui.render_prompt_history_list(settings)
    # 两条历史各有一个删除按钮
    assert html.count('data-action="sv-stage3-delete-prompt"') == 2
    # 按钮含 data-prompt + data-stage
    assert 'data-prompt="额外 H1"' in html
    assert 'data-prompt="默认 P"' in html
    assert 'data-stage="stage3"' in html
    # 删除按钮文字含 🗑
    assert "🗑 删除" in html


def test_render_prompt_history_list_star_matches_default():
    """⭐ 默认标记仍按文本相等标出（已有契约，REQ-128 保持不变）。"""
    settings = {
        "default_prompt": {"prompt": "默认 P", "updated_at": "2026-10-16T10:00:00"},
        "history": [
            {"prompt": "额外 H1", "updated_at": "2026-10-16T11:00:00"},
            {"prompt": "默认 P", "updated_at": "2026-10-16T10:00:00"},
        ],
    }
    html = svui.render_prompt_history_list(settings)
    # ⭐ 标记只出现在「默认 P」那条
    # 找到「默认 P」条目的子串
    p_start = html.find('data-prompt="默认 P"')
    assert p_start > -1
    # 向上找最近的 .slirn-stage3-hist-item 起始
    item_start = html.rfind('slirn-stage3-hist-item', 0, p_start)
    item_end_marker = html.find('</div>', p_start)
    # 该 item 内应含 ⭐ 默认 标记（h1 条目不含）
    # 用更稳的判断：⭐ 默认 出现 1 次，且位于 p_start 之前（属于该 item）
    star_pos = html.find("⭐ 默认")
    assert star_pos > -1
    # ⭐ 位置应与「默认 P」item 配对
    # 简单做法：count ⭐ 默认 == 1（因为只有默认 P 标了）
    assert html.count("⭐ 默认") == 1


def test_render_prompt_history_list_empty_state():
    """无历史 → 显示「尚无保存的提示词」占位（不应报错）。"""
    html = svui.render_prompt_history_list({"default_prompt": None, "history": []})
    assert "尚无保存的提示词" in html
    # 空状态无删除按钮
    assert "data-action=\"sv-stage3-delete-prompt\"" not in html


def test_stage3_analyze_prompt_use_system_button_has_parenthetical():
    """「引用系统提示词」按钮文字含「（可带入片段数和时长限制）」。"""
    # 找 _stage3_analyze 模板的 HTML
    import inspect
    src = inspect.getsource(svui._stage3_analyze)
    assert "可带入片段数和时长限制" in src
    assert "slirn-sv-muted" in src  # 灰显
