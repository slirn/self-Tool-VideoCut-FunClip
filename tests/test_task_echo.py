"""测试任务详情回显新建信息 — REQ-20260915-002。

详情页信息区与「新建任务」时显示方式的一致性：
- 原视频行 `📁 name（size MB · duration）`（探测失败 → 时长未知；文件缺失 → 降级标注）
- 截取段 原样起止 + 时长 + MB；无截取段明示「未截取」
- 热词 chips 全量 + 来源标签（继承/已选/手动）+ 汇总；旧任务无来源 → 无标签纯 chips
"""

from __future__ import annotations

import sys
from pathlib import Path

FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
SLIRN_STANDALONE = FUNCLIP_ROOT.parent / "slirn-standalone"

if str(FUNCLIP_ROOT) not in sys.path:
    sys.path.insert(0, str(FUNCLIP_ROOT))
if SLIRN_STANDALONE.exists() and str(SLIRN_STANDALONE) not in sys.path:
    sys.path.insert(0, str(SLIRN_STANDALONE))


def _make_task(tmp_path: Path, name: str = "回显测试", **kwargs):
    from tasklib import TaskManager

    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"fake-video-bytes")
    m = TaskManager(tmp_path)
    t = m.create(name=name, original_video=video, **kwargs)
    return m, t.task_id, video


# ---------- 原视频行（格式对齐新建步骤1 文件信息条） ----------

def test_detail_video_line_format(tmp_path: Path):
    """📁 name（N MB · duration）— fake 文件 ffprobe 失败 → 时长未知降级。"""
    from slirn_home.app import _render_task_detail

    m, tid, video = _make_task(tmp_path)
    html = _render_task_detail(tid, m)
    assert "📁 lecture.mp4（0.0 MB · 时长未知）" in html
    # 完整路径保留为小字副行
    assert "slirn-detail-sub" in html
    assert str(video).replace("\\", "/") in html or str(video) in html


def test_detail_video_missing_degrades(tmp_path: Path):
    """原视频文件被移走 → ⚠️ 文件缺失，页面不报错。"""
    from slirn_home.app import _render_task_detail

    m, tid, video = _make_task(tmp_path)
    video.unlink()
    html = _render_task_detail(tid, m)
    assert "⚠️ 文件缺失" in html
    assert "lecture.mp4" in html


# ---------- 截取段行 ----------

def test_detail_segment_line_with_duration_and_size(tmp_path: Path):
    from tasklib.models import TimeSegment

    from slirn_home.app import _render_task_detail

    m, tid, video = _make_task(tmp_path, name="带截取")
    t = m.get(tid)
    seg_path = tmp_path / "tasks" / tid / "raw_input" / "seg.mp4"
    seg_path.write_bytes(b"x" * 2048)
    t.segment = TimeSegment(start="00:00:05.000", end="00:01:00.500", path=seg_path)
    m._write_metadata(t)  # 详情页每次从 metadata.json 重读，需写回

    html = _render_task_detail(tid, m)
    # 原样起止时间（含毫秒）+ 时长 + 大小
    assert "⏱ 00:00:05.000 → 00:01:00.500（00:00:55 · 0.0 MB）" in html


def test_detail_no_segment_shows_full_video_note(tmp_path: Path):
    from slirn_home.app import _render_task_detail

    m, tid, _ = _make_task(tmp_path)
    html = _render_task_detail(tid, m)
    assert "未截取 · 使用完整原视频" in html


# ---------- 热词 chips 回显 ----------

def test_detail_hotword_chips_with_sources(tmp_path: Path):
    """三种来源标签 + 汇总行（勾了继承）+ 全量不截断。"""
    from slirn_home.app import _render_task_detail

    m, tid, _ = _make_task(
        tmp_path, hotwords=["张老师", "李教授", "训练营", "IP影视"],
        inherit_public=True,
        hotword_sources={"张老师": "inherit", "李教授": "inherit",
                         "训练营": "pick", "IP影视": "manual"},
    )
    html = _render_task_detail(tid, m)
    assert 'class="slirn-hw-chip src-inherit"' in html
    assert 'class="slirn-hw-chip src-pick"' in html
    assert 'class="slirn-hw-chip src-manual"' in html
    assert ">继承</span>" in html and ">已选</span>" in html and ">手动</span>" in html
    assert "共 4 个词 · 来自继承全部公共库" in html
    # 全量展示（旧实现截断为前 8 个）
    for w in ("张老师", "李教授", "训练营", "IP影视"):
        assert f'>{w}</span>' in html
    # 回显只读：无 ✕ 移除按钮
    assert "slirn-hw-chip-x" not in html


def test_detail_hotword_chips_legacy_no_sources(tmp_path: Path):
    """旧任务（无 hotword_sources）→ 无标签纯 chips + 汇总无继承后缀。"""
    from slirn_home.app import _render_task_detail

    m, tid, _ = _make_task(tmp_path, hotwords=["FunASR", "热词"])
    html = _render_task_detail(tid, m)
    assert "slirn-hw-chips" in html
    assert ">FunASR</span>" in html
    assert "共 2 个词" in html
    assert "来自继承全部公共库" not in html
    assert "src-inherit" not in html and "src-pick" not in html and "src-manual" not in html


def test_detail_hotword_many_words_not_truncated(tmp_path: Path):
    """超过 8 个词也不截断（旧实现 '前 8 个…'）。"""
    from slirn_home.app import _render_task_detail

    words = [f"词{i:02d}" for i in range(12)]
    m, tid, _ = _make_task(tmp_path, hotwords=words)
    html = _render_task_detail(tid, m)
    assert "共 12 个词" in html
    assert ">词11</span>" in html  # 第 12 个也在


def test_detail_hotword_empty(tmp_path: Path):
    from slirn_home.app import _render_task_detail

    m, tid, _ = _make_task(tmp_path)
    html = _render_task_detail(tid, m)
    assert "（无）" in html
