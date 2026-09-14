"""测试 hotword_ui.py + REQ-D 在 create_task 中的嵌入 — REQ-D 单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
SLIRN_STANDALONE = FUNCLIP_ROOT.parent / "slirn-standalone"

if str(FUNCLIP_ROOT) not in sys.path:
    sys.path.insert(0, str(FUNCLIP_ROOT))
if SLIRN_STANDALONE.exists() and str(SLIRN_STANDALONE) not in sys.path:
    sys.path.insert(0, str(SLIRN_STANDALONE))


@pytest.fixture
def repo(tmp_path: Path):
    """空仓库 + 已有几个词的公共库。"""
    from tasklib import HotwordLibrary
    lib = HotwordLibrary(tmp_path)
    lib.add("张老师", category="讲师")
    lib.add("达摩院", category="讲师")
    lib.add("FunASR", category="专业术语")
    return tmp_path


# ---------- on_add_word ----------

def test_on_add_word_new(repo):
    from slirn_home.hotword_ui import on_add_word
    grouped, msg = on_add_word("行业黑话", "默认", repo)
    assert "行业黑话" in msg or "已添加" in msg
    assert "行业黑话" in grouped.get("默认", [])


def test_on_add_word_duplicate(repo):
    from slirn_home.hotword_ui import on_add_word
    grouped, msg = on_add_word("张老师", "讲师", repo)
    assert "已存在" in msg


def test_on_add_word_empty(repo):
    from slirn_home.hotword_ui import on_add_word
    grouped, msg = on_add_word("", "", repo)
    assert "❌" in msg


# ---------- on_remove_word ----------

def test_on_remove_word_existing(repo):
    from slirn_home.hotword_ui import on_remove_word
    grouped, msg = on_remove_word("FunASR", repo)
    assert "FunASR" not in grouped.get("专业术语", [])


def test_on_remove_word_nonexistent(repo):
    from slirn_home.hotword_ui import on_remove_word
    grouped, msg = on_remove_word("不存在的词", repo)
    assert "不存在" in msg


# ---------- on_assign_category ----------

def test_on_assign_category(repo):
    from slirn_home.hotword_ui import on_assign_category
    grouped, msg = on_assign_category("FunASR", "讲师", repo)
    assert "FunASR" in grouped.get("讲师", [])
    assert "FunASR" not in grouped.get("专业术语", [])


# ---------- on_search_words ----------

def test_on_search_words_all(repo):
    from slirn_home.hotword_ui import on_search_words
    grid, status = on_search_words("", repo)
    assert status.startswith("匹配")
    assert "3" in status  # 3 个词
    # 网格应是 10 行 × 5 列
    assert len(grid) == 10
    assert all(len(row) == 5 for row in grid)


def test_on_search_words_filter(repo):
    from slirn_home.hotword_ui import on_search_words
    grid, status = on_search_words("fun", repo)
    assert "FunASR" in status or "匹配 1 个" in status
    # 找到的词应该在网格第一格
    flat = [w for row in grid for w in row if w]
    assert "FunASR" in flat
    assert "张老师" not in flat


def test_on_search_words_pagination():
    """>50 个词时分页。"""
    import tempfile

    from tasklib import HotwordLibrary

    from slirn_home.hotword_ui import on_page_change, on_search_words
    with tempfile.TemporaryDirectory() as tmp:
        lib = HotwordLibrary(Path(tmp))
        for i in range(75):
            lib.add(f"词{i:03d}", category="X")
        grid1, status1 = on_search_words("", Path(tmp))
        assert "第 1/2 页" in status1
        # 翻到第 2 页
        grid2, status2 = on_page_change("", 1, Path(tmp))
        assert "第 2/2 页" in status2


# ---------- on_toggle_select ----------

def test_on_toggle_select_toggle(repo):
    from slirn_home.hotword_ui import on_toggle_select
    # 第 0 行第 0 列 → "张老师"
    grid, sel, msg = on_toggle_select(0, 0, "", [], repo)
    assert "张老师" in sel
    # 再点 → 取消
    grid, sel, msg = on_toggle_select(0, 0, "", sel, repo)
    assert "张老师" not in sel


def test_on_toggle_select_invalid_row(repo):
    from slirn_home.hotword_ui import on_toggle_select
    grid, sel, msg = on_toggle_select(-1, 0, "", [], repo)
    assert sel == []


# ---------- on_confirm_selection ----------

def test_on_confirm_selection_appends(repo):
    from slirn_home.hotword_ui import on_confirm_selection
    new_text, msg = on_confirm_selection(["张老师", "达摩院"], "")
    assert "张老师" in new_text
    assert "达摩院" in new_text
    assert "✅" in msg


def test_on_confirm_selection_dedup():
    from slirn_home.hotword_ui import on_confirm_selection
    # 现有已有"张老师"，再选一次 → 不重复
    new_text, _ = on_confirm_selection(["张老师"], "张老师")
    assert new_text == "张老师"


def test_on_confirm_selection_preserves_existing():
    from slirn_home.hotword_ui import on_confirm_selection
    new_text, _ = on_confirm_selection(["FunASR"], "张老师 达摩院")
    assert "张老师" in new_text
    assert "达摩院" in new_text
    assert "FunASR" in new_text


# ---------- on_clear_selection ----------

def test_on_clear_selection():
    from slirn_home.hotword_ui import on_clear_selection
    grid, msg = on_clear_selection()
    assert grid == []
    assert "清空" in msg


# ---------- build_hotword_library_components ----------

def test_build_hotword_library_components_returns_dict(repo):
    import gradio as gr

    from slirn_home.hotword_ui import build_hotword_library_components
    with gr.Blocks():
        components = build_hotword_library_components(repo)
    expected = {
        "add_word_box", "add_category_box", "add_word_btn",
        "new_category_box", "add_category_btn",
        "refresh_btn", "remove_word_box", "remove_btn",
        "library_md", "status_md",
    }
    assert expected.issubset(set(components.keys()))


# ---------- build_hotword_picker_components + create_task 集成 ----------

def test_build_picker_after_returns_dict(repo):
    """AC-D.4.1 — 「从公共库选择」面板可构造。"""
    import gradio as gr

    from slirn_home.create_task import build_picker_after

    mgr_mock = mock.Mock()
    mgr_mock.repo_root = repo

    with gr.Blocks():
        hotwords_box = gr.Textbox(label="hotwords")
        picker = build_picker_after(mgr_mock, hotwords_box)

    expected = {
        "picker_acc", "search_box", "grid_df", "status_md",
        "prev_btn", "next_btn", "confirm_btn", "clear_btn",
        "page_state", "selected_state",
    }
    assert expected.issubset(set(picker.keys()))


# ---------- build_app 集成 ----------

def test_build_app_with_hotword_tabs():
    """AC-D.3.1 / AC-D.4.1 — build_app() 含「热词库管理」tab + 「从库选择」面板。"""
    import warnings
    warnings.filterwarnings("ignore")
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        from tasklib import TaskManager

        from slirn_home import build_app
        m = TaskManager(Path(tmp))
        app = build_app(repo_root=m.repo_root)
        assert app is not None
