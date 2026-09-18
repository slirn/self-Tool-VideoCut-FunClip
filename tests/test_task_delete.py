"""测试 REQ-20260917-034 — 任务删除：真删 + 如实报错 + manager 加固。

覆盖：
- 端点：成功（磁盘真删 + toast + 按钮 data-task-name）/ 缺 task_id / 任务不存在 /
  文件占用（Windows）→ 如实报错，不再假「已删除」
- manager.delete：metadata.json 最后删（中途失败不留幽灵任务）+ 只读清位 + 占用重试
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
SLIRN_STANDALONE = FUNCLIP_ROOT.parent / "slirn-standalone"

if str(FUNCLIP_ROOT) not in sys.path:
    sys.path.insert(0, str(FUNCLIP_ROOT))
if SLIRN_STANDALONE.exists() and str(SLIRN_STANDALONE) not in sys.path:
    sys.path.insert(0, str(SLIRN_STANDALONE))

skip_non_windows = pytest.mark.skipif(
    sys.platform != "win32", reason="文件占用行为仅 Windows 可复现"
)


@pytest.fixture
def mgr(tmp_path: Path):
    from tasklib import TaskManager

    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"fake")
    return TaskManager(tmp_path)


def _make_task(m, name: str = "删除测试") -> tuple[str, Path]:
    """建任务 + outputs 放两个产物文件，返回 (task_id, task_dir)。"""
    t = m.create(name=name, original_video=m.repo_root / "lecture.mp4")
    task_dir = m.tasks_dir / t.task_id
    outputs = task_dir / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "subtitle.json").write_text("{}", encoding="utf-8")
    (outputs / "rough.mp4").write_bytes(b"mp4")
    return t.task_id, task_dir


# ---------- manager.delete ----------

def test_delete_removes_whole_dir(mgr):
    tid, task_dir = _make_task(mgr)
    mgr.delete(tid)
    assert not task_dir.exists(), "任务目录应从磁盘整体消失（真删）"
    assert not mgr.exists(tid)


def test_delete_missing_raises(mgr):
    from tasklib import TaskNotFoundError

    with pytest.raises(TaskNotFoundError):
        mgr.delete("20990101-001")


def test_delete_readonly_files(mgr):
    """AC-4 — 只读属性自动清位后可删（Windows 上 rmtree 原本会 PermissionError）。"""
    tid, task_dir = _make_task(mgr, name="只读任务")
    for p in task_dir.rglob("*"):
        os.chmod(p, stat.S_IREAD)
    os.chmod(task_dir / "metadata.json", stat.S_IREAD)
    mgr.delete(tid)
    assert not task_dir.exists()


@skip_non_windows
def test_delete_locked_file_metadata_last(mgr):
    """AC-3/-5 — 占用导致失败时：如实抛 OSError，且 metadata.json 幸存（无幽灵目录），
    任务在列表里仍完整可见；句柄释放后重删成功。"""
    tid, task_dir = _make_task(mgr, name="占用任务")
    handle = open(task_dir / "outputs" / "rough.mp4", "rb")
    try:
        with pytest.raises(OSError):
            mgr.delete(tid)
        # 中途失败：目录仍在 + metadata 未删 → 列表仍能看到该任务（完整可见）
        assert task_dir.is_dir()
        assert (task_dir / "metadata.json").exists(), \
            "metadata.json 必须最后删 — 中途失败不留幽灵目录"
        assert any(s.task_id == tid for s in mgr.list()), "失败后任务仍应可见"
    finally:
        handle.close()
    mgr.delete(tid)
    assert not task_dir.exists()


# ---------- 端点 ----------

@pytest.fixture
def client(mgr):
    import warnings

    warnings.filterwarnings("ignore")
    from fastapi.testclient import TestClient

    from slirn_home import build_app

    app = build_app(repo_root=mgr.repo_root)
    return TestClient(app.app)


def test_endpoint_delete_success(client, mgr):
    """AC-2 — 真删 + 列表刷新 + 按钮 data-task-name（确认框文案来源）。"""
    tid, task_dir = _make_task(mgr, name="端点删除A")
    keep_tid, _ = _make_task(mgr, name="幸存任务B")  # 留一个：列表才有卡片可断言
    r = client.post("/slirn/api/delete_task", json={"task_id": tid})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True
    assert "磁盘" in d["toast"], "成功 toast 应明确是磁盘级删除"
    assert not task_dir.exists(), "任务目录应已从磁盘移除"
    assert tid not in d["html"], "列表不应再包含该任务"
    assert keep_tid in d["html"], "其余任务不受影响"
    assert f'data-action="delete-task" data-task-id="{keep_tid}"' in d["html"]
    assert 'data-task-name="幸存任务B"' in d["html"], "删除按钮应带任务名（确认框用）"


def test_endpoint_delete_errors(client, mgr):
    """AC-5 — 缺 task_id / 任务不存在 → 明确报错而非「已删除」。"""
    d1 = client.post("/slirn/api/delete_task", json={}).json()
    assert d1["ok"] is False and "缺少 task_id" in d1["error"]

    d2 = client.post("/slirn/api/delete_task", json={"task_id": "20990101-999"}).json()
    assert d2["ok"] is False and "任务不存在" in d2["error"]
    assert "已删除" not in d2.get("toast", "")


@skip_non_windows
def test_endpoint_delete_locked_reports_error(client, mgr):
    """AC-3 — 占用 → ok:false + 占用指引（旧版吞异常假成功），释放后重删成功。"""
    tid, task_dir = _make_task(mgr, name="占用端点B")
    handle = open(task_dir / "outputs" / "rough.mp4", "rb")
    try:
        d = client.post("/slirn/api/delete_task", json={"task_id": tid}).json()
        assert d["ok"] is False, "删除失败必须如实报错（不再假成功）"
        assert "删除失败" in d["error"] and "占用" in d["error"]
        assert task_dir.is_dir(), "磁盘上任务仍在（用户刷新会看到它回来 — 这是如实的）"
    finally:
        handle.close()
    d2 = client.post("/slirn/api/delete_task", json={"task_id": tid}).json()
    assert d2["ok"] is True and not task_dir.exists()
