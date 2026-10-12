"""REQ-20261012-099 修订四：短视频列表对齐长视频列表 — scope 过滤 + 搜索 + 新建按钮。

- 「➕ 新建短视频任务」按钮从 hero 挪入短视频列表头部（hero 不再放新建任务）
- scope radio（我的任务/全部任务）：仅 admin 显示；普通用户固定「我的」，
  传 scope=all 也被后端钳回 mine（与长视频列表语义一致）
- 按短视频任务名搜索：前端 client-side 过滤（对齐 #slirn-task-search 模式）

接线类断言走本仓库既有 source-grep 模式（见 test_auth.py 接线守卫）。
"""

from pathlib import Path

FUNCLIP_ROOT = Path(__file__).resolve().parent.parent


def _src(rel: str) -> str:
    return (FUNCLIP_ROOT / "slirn_home" / rel).read_text(encoding="utf-8")


def _make_project(tmp_path: Path, name: str = "p", created_by: str = ""):
    """建任务（可指定归属用户）+ 一个 split 项目；返回 (tid, pid)。"""
    from tasklib import TaskManager
    from slirn_home import short_video_service as svc

    video = tmp_path / f"src-{name}.mp4"
    video.write_bytes(b"fake")
    mgr = TaskManager(tmp_path)
    task = mgr.create(name=f"t-{name}", original_video=video, created_by=created_by)
    tid = task.task_id
    proj = svc.create_project(tmp_path, tid, name=name, brief="")
    return tid, proj["id"]


# ---------- UI 渲染：scope radio / 搜索框 / 新建按钮 ----------

def test_render_project_list_admin_scope_and_search():
    from slirn_home import short_video_ui as ui

    tasks = [{"task_id": "t1", "name": "公开课"}]
    projects = [{
        "id": "p1", "task_id": "t1", "name": "拆条 A", "kind": "split",
        "materials": [],
    }]
    html = ui.render_project_list(tasks, projects, scope="all", is_admin=True)
    # admin：我的任务/全部任务 radio，当前 all 选中
    assert 'name="slirn-sv-scope"' in html
    assert "我的任务" in html and "全部任务" in html
    assert 'value="all" checked' in html and 'value="mine" checked' not in html
    # scope=mine 时 mine 选中
    html_mine = ui.render_project_list(tasks, projects, scope="mine", is_admin=True)
    assert 'value="mine" checked' in html_mine and 'value="all" checked' not in html_mine
    # 搜索框 + 新建按钮（从 hero 挪来）
    assert 'id="slirn-sv-search"' in html
    assert "搜索短视频任务名" in html
    assert 'data-action="sv-new"' in html and "新建短视频任务" in html
    # 卡片带 task_id（搜索匹配字段）
    assert 'data-task-id="t1"' in html


def test_render_project_list_non_admin_no_scope_toggle():
    from slirn_home import short_video_ui as ui

    html = ui.render_project_list([], [], scope="mine", is_admin=False)
    # 普通用户：不渲染 radio（固定「我的」，后端钳制）
    assert "slirn-sv-scope" not in html
    assert "我的任务" not in html and "全部任务" not in html
    # 搜索框与新建按钮不分角色，都渲染
    assert 'id="slirn-sv-search"' in html
    assert 'data-action="sv-new"' in html


def test_sv_scope_radio_name_distinct_from_task_scope():
    """radio name 必须与长视频列表的 slirn-task-scope 区分，
    否则 router.js 的 change 分发会拿长视频端点刷新短视频列表。"""
    assert 'name="slirn-sv-scope"' in _src("short_video_ui.py")
    assert "slirn-sv-scope" not in _src("static/router.js")
    assert 'name="slirn-task-scope"' not in _src("short_video_ui.py")


# ---------- 端点：scope 语义（admin 可选 / 普通用户钳 mine） ----------

def test_short_video_list_endpoint_admin_scope(tmp_path):
    """bypass=1（conftest 默认）→ test-admin：默认 all、mine 过滤生效、radio 渲染。"""
    from fastapi.testclient import TestClient
    from slirn_home.app import build_app

    tid, pid = _make_project(tmp_path)
    client = TestClient(build_app(tmp_path).app)

    # 默认（不传 scope）→ all：radio 渲染且 all 选中，项目可见
    r = client.post("/slirn/api/short_video_list", json={}).json()
    assert r["ok"] is True, r
    assert 'name="slirn-sv-scope"' in r["html"]
    assert 'value="all" checked' in r["html"]
    assert pid in r["html"]

    # scope=mine → test-admin 自己没有任务（bypass 假用户不拥有任何任务）→ 列表为空
    r = client.post("/slirn/api/short_video_list", json={"scope": "mine"}).json()
    assert r["ok"] is True
    assert 'value="mine" checked' in r["html"]
    assert pid not in r["html"]
    assert "还没有短视频项目" in r["html"]


def test_short_video_list_endpoint_non_admin_clamped(tmp_path, monkeypatch):
    """普通用户传 scope=all 也被钳回 mine：radio 不渲染，只见自己的任务。"""
    from fastapi.testclient import TestClient
    from slirn_home.app import build_app
    from slirn_home.auth import AuthStore

    monkeypatch.delenv("SLIRN_AUTH_BYPASS", raising=False)
    auth = AuthStore(tmp_path)
    auth.create_user("bob", "Bob", "pw12345678")
    tok = auth.create_session("bob")
    # bob 自己的任务 + 他人（charlie）的任务各一个
    _, pid_bob = _make_project(tmp_path, name="bobproj", created_by="bob")
    _, pid_other = _make_project(tmp_path, name="otherproj", created_by="charlie")

    client = TestClient(build_app(tmp_path).app)
    r = client.post("/slirn/api/short_video_list",
                    json={"scope": "all"}, cookies={"slirn_session": tok}).json()
    assert r["ok"] is True, r
    assert "slirn-sv-scope" not in r["html"], "普通用户不渲染 我的/全部 radio"
    assert pid_bob in r["html"], "自己的任务可见"
    assert pid_other not in r["html"], "非 admin 的 scope=all 必须被钳回 mine（看不到他人任务）"


# ---------- JS 接线：radio 切换 / 搜索过滤 / sv-new ----------

def test_short_video_js_filter_wiring():
    src = _src("static/short_video.js")
    # radio 切换 → 重拉列表
    assert "e.target.name === 'slirn-sv-scope'" in src
    assert "loadList(e.target.value)" in src
    # loadList 读当前 radio / 带 scope 发请求
    assert "input[name=\"slirn-sv-scope\"]:checked" in src
    assert "{scope: scope || 'all'}" in src
    # 搜索：事件代理 + 卡片过滤 + 空态
    assert "e.target.id === 'slirn-sv-search'" in src
    assert "_filterSvCards" in src
    assert "没有匹配" in src
    # sv-new：滚动到创建表单 + 聚焦项目名
    assert "action === 'sv-new'" in src
    assert "slirn-sv-create" in src and "slirn-sv-create-name" in src


def test_short_video_js_syntax_valid():
    import subprocess
    r = subprocess.run(
        ["node", "--check", str(FUNCLIP_ROOT / "slirn_home" / "static" / "short_video.js")],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, (
        f"short_video.js 语法错误\nnode --check exit={r.returncode}\nstderr={r.stderr[:1000]}"
    )


# ---------- hero 不再放「新建任务」（入口挪进短视频列表） ----------

def test_hero_create_button_moved_into_sv_list():
    src = _src("app.py")
    hero = src.split("slirn-hero-actions")[1]
    hero = hero[: hero.index("</div>")]
    assert "新建任务" not in hero
    assert "goto-create" not in hero, "hero 不再放新建任务入口（长视频列表工具栏仍保留）"
