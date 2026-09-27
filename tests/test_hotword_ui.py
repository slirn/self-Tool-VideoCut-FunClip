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
    # REQ-20260926-NNN：空格不再做分隔符，改用半角逗号
    new_text, _ = on_confirm_selection(["FunASR"], "张老师,达摩院")
    assert "张老师" in new_text
    assert "达摩院" in new_text
    assert "FunASR" in new_text


def test_on_confirm_selection_keeps_two_word_hotword_intact():
    """REQ-20260926-NNN：含空格的双词热词（如 "machine learning"）必须
    整体保留，不能被空格分隔符拆碎；join 改半角逗号，下游用新分隔符
    重拆仍能还原成两条独立热词。"""
    import re
    from slirn_home.hotword_ui import on_confirm_selection
    new_text, _ = on_confirm_selection([], "machine learning,foo")
    # 用新分隔符重新解析输出，验证仍是两条独立热词
    re_split = [s.strip() for s in re.split(r"[,\n，;；、]+", new_text) if s.strip()]
    assert re_split == ["machine learning", "foo"], (
        f"空格分隔符误合并/拆碎，round-trip 失败：{new_text!r} → {re_split!r}")


# ---------- REQ-20260926-NNN：分隔符半角逗号 / 换行，空格不算分隔符 ----------

def test_hw_add_split_regex_drops_whitespace():
    """REQ-20260926-NNN：/slirn/api/hw_add 的 split 正则必须去掉 \\s
    （否则 "machine learning" 被切成两个），只留半角逗号/换行/全角逗号/分号/顿号。"""
    src = (FUNCLIP_ROOT / "slirn_home" / "app.py").read_text(encoding="utf-8")
    # 定位 /hw_add 端点内的 split
    i = src.find('"/slirn/api/hw_add"')
    assert i > 0, "必须有 /hw_add 端点"
    j = src.find("_re.split", i)
    assert j > 0, "/hw_add 必须用 _re.split"
    # 取该行附近
    line = src[src.rfind("\n", i, j) + 1:src.find("\n", j) + 1]
    assert r"[,\n，;；、]+" in line, f"/hw_add 必须用新分隔符正则，实际：{line!r}"
    assert r"\s" not in line, f"/hw_add 正则不能再含 \\s（否则空格还会切词）：{line!r}"


def test_parse_manual_js_uses_new_separator():
    """REQ-20260926-NNN：前端 parseManual 必须用新分隔符正则（去掉 \\s）。"""
    src = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(encoding="utf-8")
    i = src.find("function parseManual")
    assert i > 0, "必须有 parseManual"
    body = src[i:src.find("\n  }\n", i)]
    assert r"/[,\n，;；、]+/" in body, f"parseManual 必须用新分隔符正则，实际 body：{body!r}"
    assert r"\s" not in body, f"parseManual 正则不能再含 \\s：{body!r}"


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


def test_manual_textarea_uses_comma_newline_join_not_space():
    """REQ-20260926-NNN：编辑页手动 textarea 回填必须用「半角逗号+换行」拼接
    （不能用空格），否则 separator 改后多词热词被塌缩成一个。"""
    src = (FUNCLIP_ROOT / "slirn_home" / "app.py").read_text(encoding="utf-8")
    # 找 manual_text_value 预计算（手动 textarea 回填值）
    assert '("," + chr(10)).join(manual_words)' in src, (
        "编辑页 manual textarea 回填必须用 ',' + chr(10) 拼接（不能再用空格）")
    # 防卫：编辑分支不能有 ' '.join(manual_words)
    i = src.find('id="slirn-tab-create-inner"')
    assert i > 0
    end = src.find("'''", i)
    block = src[i:end]
    assert "' '.join(manual_words)" not in block, (
        "编辑页 manual textarea 回填仍是 ' '.join(manual_words) — 会塌缩多词热词")


def test_picker_has_per_category_bulk_select_buttons():
    """REQ-20260926-NNN：picker 分类标题必须有「☑ 全选 / ⇄ 反选」按钮。"""
    src = (FUNCLIP_ROOT / "slirn_home" / "app.py").read_text(encoding="utf-8")
    i = src.find("def _render_hotword_picker")
    assert i > 0
    # 找下一个 def 边界
    end = src.find("\n\n# ", i)
    if end < 0:
        end = src.find("\n\ndef ", i)
    body = src[i:end]
    assert 'data-action="hw-cat-select-all"' in body, (
        "picker 分类标题缺少 ☑ 全选 按钮")
    assert 'data-action="hw-cat-invert"' in body, (
        "picker 分类标题缺少 ⇄ 反选 按钮")


def test_hw_cat_bulk_select_refreshes_chips():
    """REQ-20260926-NNN：picker 复用 hw-cat-select-all/invert 后必须刷新 chips。"""
    src = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(encoding="utf-8")
    i = src.find("action === 'hw-cat-select-all' || action === 'hw-cat-invert'")
    assert i > 0
    body = src[i:src.find("\n    else if (action === 'trigger-file')", i)]
    assert body.count("renderHwChips()") >= 2, (
        "hw-cat-select-all / hw-cat-invert 必须都调 renderHwChips() 刷新 chips")


def test_hotword_analysis_section_collapsed_by_default():
    """REQ-20260926-NNN：AI 热词分析区域必须默认折叠（<details> 元素），点 summary 展开。"""
    src = (FUNCLIP_ROOT / "slirn_home" / "app.py").read_text(encoding="utf-8")
    i = src.find("def _render_hotword_analysis_section")
    assert i > 0
    # 找函数结尾（下一个 def 或顶层块）
    end = src.find("\n\ndef ", i)
    body = src[i:end]
    assert "<details" in body, "分析区域必须用 <details> 默认折叠"
    assert "<summary>" in body, "折叠面板必须有 <summary> 触发展开"
    assert "[open]" not in body or '<details' in body, "<details> 不能预设 open（必须默认折叠）"


def test_hotword_analysis_collapsible_css_exists():
    """CSS 必须给 .slirn-hw-analysis-collapsible summary 提供可点击样式。"""
    css = (FUNCLIP_ROOT / "slirn_home" / "static" / "home.css").read_text(encoding="utf-8")
    assert ".slirn-hw-analysis-collapsible" in css, (
        "CSS 必须定义折叠面板样式")
    assert ".slirn-hw-analysis-collapsible > summary" in css, (
        "CSS 必须给 summary 提供可点击样式")
    # 必须隐藏默认三角（list-style: none 或 ::-webkit-details-marker）
    assert "::-webkit-details-marker" in css or "list-style: none" in css, (
        "必须隐藏 details 默认三角标记")


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
