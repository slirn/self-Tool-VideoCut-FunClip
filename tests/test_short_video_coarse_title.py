"""REQ-20261016-134 — Stage 5 coarse title 透传。

Stage 3 highlights.json 的 title 必须透传到 stage5_coarse.highlights，
否则 Stage 5 / Stage 6 卡片顶部显示不出标题。
"""

from pathlib import Path

FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
COARSE = (FUNCLIP_ROOT / "slirn_home" / "short_video_coarse.py").read_text(
    encoding="utf-8")


def test_coarse_new_highlights_includes_title_field():
    """new_highlights 构造里必须含 title 字段（从 stage3 highlights 取）。"""
    # 直接验证：new_highlights 字面量必须含 "title":
    assert '"title":' in COARSE, \
        "short_video_coarse.py 的 new_highlights 字典里没 title 字段"
    # 必须从 raw_highlights 拿（hl_by_idx 或类似）
    assert "hl_by_idx" in COARSE or "raw_highlights" in COARSE, \
        "title 应通过 index 索引从 raw_highlights 读取"


def test_coarse_title_lookup_uses_index():
    """title 查找用 index 索引避免 N² 嵌套。"""
    # hl_by_idx = {int(h.get("index") or 0): h for h in raw_highlights}
    assert "hl_by_idx" in COARSE, \
        "应构造 hl_by_idx 字典以 O(1) 查找 hl.title（避免 N² 嵌套循环）"
