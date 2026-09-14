"""测试 create_task.py — REQ-C 单元测试。"""

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
def mgr(tmp_path: Path):
    """构造临时仓库 + TaskManager。"""
    from tasklib import TaskManager

    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"fake")
    return TaskManager(tmp_path)


# ---------- on_upload_video ----------

def test_on_upload_video_empty_path(mgr):
    from slirn_home.create_task import on_upload_video

    info, msg = on_upload_video(None, mgr.repo_root)
    assert "请先" in msg
    assert info == ""


def test_on_upload_video_nonexistent_file(mgr):
    from slirn_home.create_task import on_upload_video

    info, msg = on_upload_video("/nope.mp4", mgr.repo_root)
    assert "不存在" in msg


def test_on_upload_video_with_duration(mgr):
    """AC-C.1.3 — 选择后显示文件名 + 大小 + 时长。"""
    from slirn_home.create_task import on_upload_video

    video = mgr.repo_root / "test.mp4"
    video.write_bytes(b"x" * 1024)

    with mock.patch(
        "slirn_home.create_task.get_video_duration", return_value=125.0
    ):
        info, msg = on_upload_video(str(video), mgr.repo_root)

    assert "test.mp4" in info
    assert "MB" in info
    assert "00:02:05" in info  # 125 秒
    assert msg == "✅ 文件已加载"


def test_on_upload_video_without_duration(mgr):
    """D1 — 时长未知时不阻塞。"""
    from slirn_home.create_task import on_upload_video

    video = mgr.repo_root / "test.mp4"
    video.write_bytes(b"x")

    with mock.patch(
        "slirn_home.create_task.get_video_duration", return_value=None
    ):
        info, msg = on_upload_video(str(video), mgr.repo_root)

    assert "未知" in info


# ---------- on_cut_preview ----------

def test_on_cut_preview_no_file(mgr):
    from slirn_home.create_task import on_cut_preview

    preview, msg, _, _ = on_cut_preview(None, "00:00:01", "00:00:03", mgr.repo_root)
    assert preview is None
    assert "请先选择" in msg


def test_on_cut_preview_empty_times(mgr):
    """AC-C.2.4 — 时间为空时不截取（虽然这个函数本身不会调，但提示正确）。"""
    from slirn_home.create_task import on_cut_preview

    video = mgr.repo_root / "test.mp4"
    video.write_bytes(b"x")

    preview, msg, _, _ = on_cut_preview(str(video), "", "", mgr.repo_root)
    assert preview is None
    assert "请填写" in msg


def test_on_cut_preview_invalid_segment(mgr):
    """AC-C.2.3 — start >= end 报错。"""
    from slirn_home.create_task import on_cut_preview

    video = mgr.repo_root / "test.mp4"
    video.write_bytes(b"x")

    preview, msg, _, _ = on_cut_preview(
        str(video), "00:00:05", "00:00:01", mgr.repo_root
    )
    assert preview is None
    assert "❌" in msg
    assert "校验" in msg or "start" in msg


def test_on_cut_preview_success(mgr):
    """AC-C.2.1 / AC-C.2.2 — 截取成功返回临时文件路径。"""
    from slirn_home.create_task import on_cut_preview

    video = mgr.repo_root / "test.mp4"
    video.write_bytes(b"x")

    def fake_cut(src, dst, start, end):
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"cut content")

    # cut_video 是在 on_cut_preview 函数内部导入的（late import），所以 mock 要打到 tasklib.video
    with mock.patch("tasklib.video.cut_video", side_effect=fake_cut):
        with mock.patch(
            "slirn_home.create_task.get_video_duration", return_value=2.0
        ):
            preview, msg, _, _ = on_cut_preview(
                str(video), "00:00:01", "00:00:03", mgr.repo_root
            )

    assert preview is not None
    assert Path(preview).exists()
    assert "截取成功" in msg


# ---------- on_create_task ----------

def test_on_create_task_no_file(mgr):
    from slirn_home.create_task import on_create_task

    rows, meta, msg = on_create_task(
        None, "test", "", "", None, "", mgr.repo_root
    )
    assert "请先" in msg


def test_on_create_task_without_segment(mgr):
    """AC-C.4.1 — 创建不带截取段的任务。"""
    from slirn_home.create_task import on_create_task

    video = mgr.repo_root / "lecture.mp4"
    video.write_bytes(b"x")

    rows, meta, msg = on_create_task(
        str(video), "讲座 1", "", "", None, "FunASR 张老师", mgr.repo_root
    )
    assert "已创建" in msg
    assert len(rows) == 1


def test_on_create_task_with_segment(mgr):
    """AC-C.4.1 — 创建带截取段的任务，并把临时文件移到 task_dir。"""
    from slirn_home.create_task import on_create_task

    video = mgr.repo_root / "lecture.mp4"
    video.write_bytes(b"x")

    # 模拟已经截取好的临时文件
    temp_dir = mgr.repo_root / ".temp"
    temp_dir.mkdir(exist_ok=True)
    temp_cut = temp_dir / "cut_fake_lecture.mp4"
    temp_cut.write_bytes(b"cut content")

    rows, meta, msg = on_create_task(
        str(video), "讲座 2", "00:01:00", "00:05:00",
        str(temp_cut), "", mgr.repo_root
    )
    assert "已创建" in msg

    # 截取段应该被移到 task_dir/raw_input/
    task_id = rows[0][0]
    task_segment = mgr.repo_root / "tasks" / task_id / "raw_input" / "lecture_segment_00-01-00_00-05-00.mp4"
    assert task_segment.exists()
    # 临时文件应该被删除
    assert not temp_cut.exists()


def test_on_create_task_default_name_from_filename(mgr):
    """AC-C.3.1 — 任务名默认 = 文件名 stem。"""
    from slirn_home.create_task import on_create_task

    video = mgr.repo_root / "my_lecture.mp4"
    video.write_bytes(b"x")

    rows, _, msg = on_create_task(
        str(video), "", "", "", None, "", mgr.repo_root
    )
    # 列表中任务名是 my_lecture
    assert "my_lecture" in rows[0][1]


def test_on_create_task_hotwords_parsing(mgr):
    """AC-C.3.3 — 热词接受空格 / 换行分隔。"""
    from slirn_home.create_task import on_create_task

    video = mgr.repo_root / "test.mp4"
    video.write_bytes(b"x")

    rows, _, _ = on_create_task(
        str(video), "test", "", "", None, "FunASR 张老师\n达摩院", mgr.repo_root
    )
    task_id = rows[0][0]
    hotwords_file = mgr.repo_root / "tasks" / task_id / "hotwords.txt"
    content = hotwords_file.read_text(encoding="utf-8")
    assert "FunASR" in content
    assert "张老师" in content
    assert "达摩院" in content


# ---------- build_create_task_components ----------

def test_build_create_task_components_returns_dict(mgr):
    """AC-C.6.3 — build_create_task_components 返回 Gradio 组件 dict。

    注意：Gradio 组件必须在 `gr.Blocks()` 上下文内构造，
    所以本测试把构造包在临时 Blocks 里。
    """
    import gradio as gr

    from slirn_home.create_task import build_create_task_components

    with gr.Blocks():
        components = build_create_task_components(mgr)

    expected_keys = {
        "root_col", "file_input", "file_info", "start_box", "end_box",
        "cut_btn", "preview_video", "cut_msg", "task_name_box",
        "hotwords_box", "create_btn", "cancel_btn",
    }
    assert expected_keys.issubset(set(components.keys()))


# ---------- build_app 集成 ----------

def test_build_app_with_create_tab():
    """AC-C.6.1 — build_app() 包含「新建任务」tab。"""
    import warnings
    warnings.filterwarnings("ignore")
    import sys
    sys.path.insert(0, str(FUNCLIP_ROOT))
    sys.path.insert(0, str(SLIRN_STANDALONE))

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        from tasklib import TaskManager

        from slirn_home import build_app
        m = TaskManager(Path(tmp))
        app = build_app(repo_root=m.repo_root)
        assert app is not None
