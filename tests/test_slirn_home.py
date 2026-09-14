"""测试 slirn_home 任务列表渲染 — REQ-B AC-B.3 / AC-B.4。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 把 funclip-main 仓库根 + slirn-standalone 加到 sys.path
FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
SLIRN_STANDALONE = FUNCLIP_ROOT.parent / "slirn-standalone"

if str(FUNCLIP_ROOT) not in sys.path:
    sys.path.insert(0, str(FUNCLIP_ROOT))
if SLIRN_STANDALONE.exists() and str(SLIRN_STANDALONE) not in sys.path:
    sys.path.insert(0, str(SLIRN_STANDALONE))


@pytest.fixture
def mgr(tmp_path: Path):
    """构造一个临时仓库 + 假视频 + TaskManager 实例。"""
    from tasklib import TaskManager

    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"fake")

    m = TaskManager(tmp_path)
    m.create(name="讲座 1", original_video=video, hotwords=["FunASR"])
    m.create(name="讲座 2", original_video=video)
    return m


# ---------- render_task_list ----------

def test_render_task_list_returns_rows_and_meta(mgr):
    from slirn_home.task_list import render_task_list

    rows, meta = render_task_list(mgr)
    assert len(rows) == 2
    assert meta == {"当前任务数": 2}


def test_render_task_list_columns(mgr):
    """AC-B.3.2 — 列表字段完整。"""
    from slirn_home.task_list import render_task_list

    rows, _ = render_task_list(mgr)
    assert len(rows[0]) == 6  # ID / 任务名 / 视频 / 状态 / 创建 / 修改


def test_render_task_list_uses_chinese_status_label(mgr):
    """AC-B.3.6 — 状态显示用中文。"""
    from tasklib import TASK_STATUS_LABEL, TaskStatus

    from slirn_home.task_list import render_task_list

    rows, _ = render_task_list(mgr)
    # DRAFT 的中文标签是 "草稿"
    assert rows[0][3] == TASK_STATUS_LABEL[TaskStatus.DRAFT]


def test_render_task_list_sorted_by_updated_desc(mgr):
    """AC-B.3.3 — 按 updated_at 倒序。"""
    from tasklib import TaskStatus

    from slirn_home.task_list import render_task_list

    rows, _ = render_task_list(mgr)
    # 后建的应该在前面
    assert "讲座 2" in rows[0][1]
    # 更新第一个任务的状态后，刷新列表
    mgr.update_status(mgr.list()[1].task_id, TaskStatus.ASSETS_READY)
    rows2, _ = render_task_list(mgr)
    # "讲座 1" 现在 updated 更新，应该排第一
    assert rows2[0][1] == "讲座 1"


def test_render_task_list_empty(tmp_path: Path):
    """AC-B.3.4 — 空列表处理。"""
    from tasklib import TaskManager

    from slirn_home.task_list import render_task_list

    m = TaskManager(tmp_path)
    rows, meta = render_task_list(m)
    assert rows == []
    assert meta == {"当前任务数": 0}


# ---------- show_detail ----------

def test_show_detail_returns_markdown(mgr):
    from slirn_home.task_list import show_detail

    task_id = mgr.list()[0].task_id
    md = show_detail(mgr, task_id)
    assert "### 任务详情" in md
    assert task_id in md
    assert "讲座" in md


def test_show_detail_handles_missing_task(mgr):
    """AC-B.4.2 — 不存在任务返回错误提示。"""
    from slirn_home.task_list import show_detail

    md = show_detail(mgr, "99999999-999")
    assert "❌" in md
    assert "不存在" in md


# ---------- arm / cancel / confirm delete ----------

def test_arm_delete(mgr):
    """AC-B.4.4-5 — arm 状态。"""
    from slirn_home.task_list import arm_delete

    btn_text, cancel_text, cancel_visible = arm_delete()
    assert "确认删除" in btn_text
    assert cancel_text == "取消"
    assert cancel_visible is True


def test_cancel_delete(mgr):
    from slirn_home.task_list import cancel_delete

    btn_text, cancel_text, cancel_visible = cancel_delete()
    assert "删除" in btn_text
    assert "⚠️" not in btn_text
    assert cancel_visible is False


def test_confirm_delete_removes_task(mgr):
    """AC-B.4.6 — 确认后真正删除。"""
    from slirn_home.task_list import confirm_delete

    task_id = mgr.list()[0].task_id
    rows, meta, msg, btn_text, cancel_visible = confirm_delete(mgr, task_id)
    assert "已删除" in msg
    assert len(rows) == 1  # 少了一个任务


def test_confirm_delete_handles_missing(mgr):
    from slirn_home.task_list import confirm_delete

    rows, meta, msg, btn_text, cancel_visible = confirm_delete(mgr, "99999999-999")
    assert "不存在" in msg


# ---------- placeholder_action ----------

def test_placeholder_action_returns_toast():
    """AC-B.4.8 — 占位按钮返回 toast。"""
    from slirn_home.task_list import placeholder_action

    msg = placeholder_action("开始剪辑")
    assert "开始剪辑" in msg
    assert "后续 REQ" in msg


# ---------- paths ----------

def test_find_slirn_standalone_root_via_sibling(tmp_path: Path, monkeypatch):
    """通过 sibling 目录查找。"""
    from slirn_home.paths import find_slirn_standalone_root

    # 模拟 funclip-main + sibling slirn-standalone 结构
    fake_parent = tmp_path / "parent"
    fake_parent.mkdir()
    fake_funclip = fake_parent / "FunClip-main"
    fake_funclip.mkdir()
    fake_slirn = fake_parent / "slirn-standalone"
    fake_slirn.mkdir()
    (fake_slirn / "tasklib").mkdir()

    # 改 __file__ 让 find_repo_root 返回 fake_funclip
    import slirn_home.paths as paths_mod
    monkeypatch.setattr(paths_mod, "__file__", str(fake_funclip / "slirn_home" / "paths.py"))

    root = find_slirn_standalone_root()
    assert root == fake_slirn.resolve()


def test_find_slirn_standalone_root_via_env(tmp_path: Path, monkeypatch):
    """通过环境变量查找。"""
    from slirn_home.paths import find_slirn_standalone_root

    env_root = tmp_path / "env_slirn"
    env_root.mkdir()
    (env_root / "tasklib").mkdir()
    monkeypatch.setenv("SLIRN_STANDALONE_ROOT", str(env_root))

    root = find_slirn_standalone_root()
    assert root == env_root.resolve()
