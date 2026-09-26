"""测试 slirn_home/auth.py — REQ-20260926-NNN：用户管理 + 会话 + 成员。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

FUNCLIP_ROOT = Path(__file__).resolve().parent.parent
SLIRN_STANDALONE = FUNCLIP_ROOT.parent / "slirn-standalone"

if str(FUNCLIP_ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(FUNCLIP_ROOT))
if SLIRN_STANDALONE.exists() and str(SLIRN_STANDALONE) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(SLIRN_STANDALONE))


@pytest.fixture
def auth(tmp_path: Path):
    """每个测试一个干净 data/ 目录。"""
    from slirn_home.auth import AuthStore
    return AuthStore(tmp_path)


# ---------- 密码哈希 ----------

def test_hash_password_and_verify_roundtrip():
    from slirn_home.auth import hash_password, verify_password
    salt_hex, hash_hex = hash_password("hello-world")
    assert len(salt_hex) == 32            # 16 字节 hex
    assert len(hash_hex) == 64            # sha256 32 字节 hex
    assert verify_password("hello-world", salt_hex, hash_hex) is True
    assert verify_password("wrong", salt_hex, hash_hex) is False


def test_hash_password_uses_unique_salt_per_call():
    from slirn_home.auth import hash_password
    s1, _ = hash_password("same-password")
    s2, _ = hash_password("same-password")
    assert s1 != s2                       # 每次随机 salt


def test_verify_password_handles_garbage_input():
    from slirn_home.auth import verify_password
    assert verify_password("x", "not-hex", "also-not-hex") is False
    assert verify_password("x", "00", "00") is False              # 太短


# ---------- 用户 CRUD ----------

def test_create_user_persists_and_reload(auth):
    auth.create_user("alice", "Alice", "pass1234", is_admin=True)
    assert auth.count_users() == 1
    u = auth.get_user("alice")
    assert u is not None
    assert u["username"] == "alice"
    assert u["is_admin"] is True
    assert "pw_salt" in u and "pw_hash" in u


def test_create_user_duplicate_raises(auth):
    auth.create_user("alice", "Alice", "pass1234")
    with pytest.raises(ValueError, match="已存在"):
        auth.create_user("alice", "Alice", "pass1234")


def test_get_user_missing_returns_none(auth):
    assert auth.get_user("nobody") is None


def test_delete_user_removes_and_cascades(auth):
    auth.create_user("alice", "Alice", "p")
    auth.create_user("bob", "Bob", "p")
    auth.add_task_member("t1", "alice")
    auth.add_task_member("t1", "bob")
    token = auth.create_session("alice")
    assert auth.user_from_token(token) is not None
    auth.delete_user("alice")
    assert auth.count_users() == 1
    assert auth.get_user("alice") is None
    assert auth.list_task_members("t1") == ["bob"]    # bob 仍在
    assert auth.user_from_token(token) is None         # alice 会话清掉


def test_update_user_password(auth):
    auth.create_user("alice", "Alice", "old")
    assert auth.verify_credentials("alice", "old") is not None
    assert auth.verify_credentials("alice", "new") is None
    auth.update_user_password("alice", "new")
    assert auth.verify_credentials("alice", "old") is None
    assert auth.verify_credentials("alice", "new") is not None


def test_verify_credentials_timing_safe_against_user_enumeration(auth):
    """不存在的用户也要走一次 hash 消耗大致相同时间，防时序探测。
    这里只验证返 None + 不抛错。"""
    auth.create_user("alice", "Alice", "p")
    assert auth.verify_credentials("alice", "wrong") is None
    assert auth.verify_credentials("nobody", "anything") is None


# ---------- 会话 ----------

def test_create_session_and_user_from_token(auth):
    auth.create_user("alice", "Alice", "p")
    token = auth.create_session("alice", ttl_days=1)
    u = auth.user_from_token(token)
    assert u is not None
    assert u["username"] == "alice"


def test_user_from_token_reusable(auth):
    """回归守卫：有效会话查一次不能被删（曾因 matched 不进 kept →
    单次会话，登录后第二个请求就 401）。"""
    auth.create_user("alice", "Alice", "p")
    token = auth.create_session("alice")
    assert auth.user_from_token(token) is not None
    assert auth.user_from_token(token) is not None     # 第二次仍有效
    assert auth.user_from_token(token) is not None     # 第三次仍有效


def test_user_from_token_expired(auth, monkeypatch):
    auth.create_user("alice", "Alice", "p")
    token = auth.create_session("alice", ttl_days=1)
    # 把所有 session 的 expires_at 改成过去
    from slirn_home import auth as _auth_mod
    sessions = _auth_mod._load_json(auth.sessions_path, [])
    for s in sessions:
        s["expires_at"] = 0
    _auth_mod._save_json(auth.sessions_path, sessions)
    assert auth.user_from_token(token) is None
    # 过期 session 已自动清理
    sessions_after = _auth_mod._load_json(auth.sessions_path, [])
    assert all(s.get("token") != token for s in sessions_after)


def test_delete_session(auth):
    auth.create_user("alice", "Alice", "p")
    t = auth.create_session("alice")
    assert auth.user_from_token(t) is not None
    auth.delete_session(t)
    assert auth.user_from_token(t) is None


def test_purge_expired_sessions(auth):
    auth.create_user("alice", "Alice", "p")
    t1 = auth.create_session("alice", ttl_days=1)
    t2 = auth.create_session("alice", ttl_days=30)
    from slirn_home import auth as _auth_mod
    sessions = _auth_mod._load_json(auth.sessions_path, [])
    for s in sessions:
        if s["token"] == t1:
            s["expires_at"] = 0
    _auth_mod._save_json(auth.sessions_path, sessions)
    n = auth.purge_expired_sessions()
    assert n == 1
    sessions_after = _auth_mod._load_json(auth.sessions_path, [])
    tokens = {s["token"] for s in sessions_after}
    assert t1 not in tokens and t2 in tokens


# ---------- 成员（task_memberships.json） ----------

def test_membership_add_remove_list(auth):
    auth.add_task_member("t1", "alice")
    auth.add_task_member("t1", "bob")
    auth.add_task_member("t1", "alice")           # 重复 → no-op
    assert sorted(auth.list_task_members("t1")) == ["alice", "bob"]
    auth.remove_task_member("t1", "alice")
    assert auth.list_task_members("t1") == ["bob"]
    auth.remove_task_member("t1", "bob")
    assert auth.list_task_members("t1") == []      # 空列表后从 dict 删 key
    # 没 key 也返空
    assert auth.list_task_members("never") == []


def test_list_user_tasks(auth):
    auth.add_task_member("t1", "alice")
    auth.add_task_member("t2", "alice")
    auth.add_task_member("t1", "bob")
    assert sorted(auth.list_user_tasks("alice")) == ["t1", "t2"]
    assert auth.list_user_tasks("bob") == ["t1"]
    assert auth.list_user_tasks("nobody") == []


# ---------- TaskManager 集成：created_by + list_for_user + assert_access ----------

def test_create_task_with_created_by_persists(auth):
    from tasklib import TaskManager
    mgr = TaskManager(SLIRN_STANDALONE / "tests" / "_fake_repo")
    # 真实 repo 路径不重要：只验证字段持久化。手动调 mgr.create 需真实视频文件，跳过用 schema 直测。
    from tasklib.schema import task_to_dict, task_from_dict
    from tasklib.models import Task, TaskStatus
    from datetime import datetime, timezone
    from pathlib import Path as _P
    # 构造一个最小 Task，写 dict，再读回
    t = Task(task_id="20260101-001", name="t",
             original_video_source=_P("/tmp/x.mp4"),
             original_video_symlink=_P("/tmp/x.mp4"),
             segment=None,
             hotwords_path=_P("/tmp/h.txt"),
             created_by="alice")
    d = task_to_dict(t, SLIRN_STANDALONE)
    assert d["created_by"] == "alice"
    t2 = task_from_dict(d, SLIRN_STANDALONE)
    assert t2.created_by == "alice"


def test_create_task_without_created_by_defaults_none(auth):
    """向后兼容：旧 metadata.json 无 created_by → None。"""
    from tasklib.schema import task_from_dict
    d = {
        "schema_version": "1",
        "task_id": "20260101-001",
        "name": "t",
        "original_video_source": "/tmp/x.mp4",
        "original_video_symlink": "/tmp/x.mp4",
        "segment": None,
        "hotwords_path": "/tmp/h.txt",
        "inherit_public": False,
        "hotword_sources": None,
        "status": "DRAFT",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        # 注意：故意没有 created_by
    }
    t = task_from_dict(d, SLIRN_STANDALONE)
    assert t.created_by is None


def test_list_for_user_filters_correctly(tmp_path: Path):
    """admin 看全部；普通用户看「自己创建 + 自己是成员」。"""
    from tasklib import TaskManager
    repo = tmp_path / "repo"
    repo.mkdir()
    mgr = TaskManager(repo)
    # 造 3 个任务（用 dummy 视频）
    import shutil
    videos = []
    for i in range(3):
        v = repo / f"v{i}.mp4"
        v.write_bytes(b"\x00" * 32)
        videos.append(v)
    t1 = mgr.create(name="alice-owned", original_video=videos[0], created_by="alice")
    t2 = mgr.create(name="bob-owned",   original_video=videos[1], created_by="bob")
    t3 = mgr.create(name="charlie-owned", original_video=videos[2], created_by="charlie")

    # alice 加入 t2（作为成员）
    from slirn_home.auth import AuthStore
    auth = AuthStore(repo)
    auth.add_task_member(t2.task_id, "alice")
    # alice 视角：t1（自己创建）+ t2（成员）= 2 个
    visible = mgr.list_for_user("alice", is_admin=False,
                                member_of=auth.list_user_tasks("alice"))
    visible_ids = {s.task_id for s in visible}
    assert visible_ids == {t1.task_id, t2.task_id}
    # admin 视角：3 个
    visible = mgr.list_for_user("admin", is_admin=True)
    assert {s.task_id for s in visible} == {t1.task_id, t2.task_id, t3.task_id}
    # charlie 视角：仅 t3
    visible = mgr.list_for_user("charlie", is_admin=False, member_of=[])
    assert {s.task_id for s in visible} == {t3.task_id}


def test_assert_access_admin_creator_member(tmp_path: Path):
    from tasklib import TaskManager
    from slirn_home.auth import AuthStore
    repo = tmp_path / "repo"
    repo.mkdir()
    mgr = TaskManager(repo)
    v = repo / "v.mp4"
    v.write_bytes(b"\x00" * 32)
    t = mgr.create(name="x", original_video=v, created_by="alice")
    auth = AuthStore(repo)
    auth.add_task_member(t.task_id, "bob")

    # admin 豁免
    assert mgr.assert_access({"username": "admin", "is_admin": True}, t.task_id, []).task_id == t.task_id
    # 创建者
    assert mgr.assert_access({"username": "alice", "is_admin": False}, t.task_id, []).task_id == t.task_id
    # 成员
    assert mgr.assert_access({"username": "bob", "is_admin": False}, t.task_id, [t.task_id]).task_id == t.task_id
    # 非成员
    with pytest.raises(PermissionError):
        mgr.assert_access({"username": "charlie", "is_admin": False}, t.task_id, [])
    # 任务不存在
    with pytest.raises(Exception):
        mgr.assert_access({"username": "alice", "is_admin": False}, "nope", [])


# ---------- 中间件（纯 ASGI）：contextvar 直达端点 + task 守卫 ----------

def _make_test_app(auth, routes):
    """包一层 SlirnAuthMiddleware 的 Starlette 测试 app（模拟线上挂载）。"""
    from starlette.applications import Starlette
    from starlette.testclient import TestClient
    from slirn_home.auth import SlirnAuthMiddleware
    app = Starlette(routes=routes)
    return TestClient(SlirnAuthMiddleware(app, auth))


def test_middleware_contextvar_reaches_endpoint(auth, monkeypatch):
    """回归守卫：纯 ASGI 中间件 set 的 contextvar 必须在端点里可见。
    （BaseHTTPMiddleware 的 call_next 跨 task → contextvar 隔离，曾导致
    refresh_tasks 拿到空 user、scope radio 不渲染。）"""
    # conftest 全局开了 bypass — 本测试要走真实 cookie 会话路径
    monkeypatch.delenv("SLIRN_AUTH_BYPASS", raising=False)
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from slirn_home.auth import get_current_user_from_context

    auth.create_user("alice", "Alice", "pw1234", is_admin=True)
    token = auth.create_session("alice")

    async def who(request):                                     # noqa: ARG001
        return JSONResponse({"user": get_current_user_from_context()})

    client = _make_test_app(auth, [Route("/slirn/api/who", who, methods=["POST"])])
    # 未登录 → 401
    r = client.post("/slirn/api/who")
    assert r.status_code == 401
    # 登录 → 端点从 contextvar 读到 user
    r = client.post("/slirn/api/who", cookies={"slirn_session": token})
    assert r.status_code == 200
    assert r.json()["user"]["username"] == "alice"
    assert r.json()["user"]["is_admin"] is True


def test_middleware_task_access_guards_json_body(auth, monkeypatch):
    """非 admin 带 task_id 的 JSON 请求 → task_access 校验；
    body 被中间件读过后端点仍能拿到（回放 receive）。admin 豁免。"""
    monkeypatch.delenv("SLIRN_AUTH_BYPASS", raising=False)
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    auth.create_user("bob", "Bob", "pw1234")
    auth.create_user("boss", "Boss", "pw1234", is_admin=True)
    tok_bob = auth.create_session("bob")
    tok_boss = auth.create_session("boss")

    async def echo(request):
        data = await request.json()
        return JSONResponse({"ok": True, "task_id": data.get("task_id")})

    allowed = {"t_bob"}
    from starlette.applications import Starlette
    from starlette.testclient import TestClient
    from slirn_home.auth import SlirnAuthMiddleware
    app = Starlette(routes=[Route("/slirn/api/do", echo, methods=["POST"])])
    client = TestClient(SlirnAuthMiddleware(app, auth, task_access=lambda u, tid: tid in allowed))
    # bob 访问自己的任务 → 200（body 回放正常，端点读到 task_id）
    r = client.post("/slirn/api/do", json={"task_id": "t_bob"},
                    cookies={"slirn_session": tok_bob})
    assert r.status_code == 200
    assert r.json()["task_id"] == "t_bob"
    # bob 访问别人的任务 → 403
    r = client.post("/slirn/api/do", json={"task_id": "t_other"},
                    cookies={"slirn_session": tok_bob})
    assert r.status_code == 403
    # admin 豁免（task_access 不触发）
    r = client.post("/slirn/api/do", json={"task_id": "t_other"},
                    cookies={"slirn_session": tok_boss})
    assert r.status_code == 200


def test_middleware_task_access_guards_query_param(auth, monkeypatch):
    """GET 端点（fine_material_file/output_file）task_id 走 query — 同样拦截。"""
    monkeypatch.delenv("SLIRN_AUTH_BYPASS", raising=False)
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    auth.create_user("bob", "Bob", "pw1234")
    tok_bob = auth.create_session("bob")

    async def echo_get(request):
        return JSONResponse({"ok": True, "task_id": request.query_params.get("task_id")})

    from starlette.applications import Starlette
    from starlette.testclient import TestClient
    from slirn_home.auth import SlirnAuthMiddleware
    app = Starlette(routes=[Route("/slirn/api/file", echo_get, methods=["GET"])])
    client = TestClient(SlirnAuthMiddleware(app, auth, task_access=lambda u, tid: tid == "t_bob"))
    r = client.get("/slirn/api/file?task_id=t_other",
                   cookies={"slirn_session": tok_bob})
    assert r.status_code == 403
    r = client.get("/slirn/api/file?task_id=t_bob",
                   cookies={"slirn_session": tok_bob})
    assert r.status_code == 200


def test_middleware_bypass_env_injects_test_admin(auth, monkeypatch):
    """SLIRN_AUTH_BYPASS=1 → 未登录也放行 + contextvar 挂 test-admin（测试兼容）。"""
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from slirn_home.auth import get_current_user_from_context

    monkeypatch.setenv("SLIRN_AUTH_BYPASS", "1")

    async def who(request):                                     # noqa: ARG001
        return JSONResponse({"user": get_current_user_from_context()})

    client = _make_test_app(auth, [Route("/slirn/api/who", who, methods=["POST"])])
    r = client.post("/slirn/api/who")
    assert r.status_code == 200
    assert r.json()["user"]["username"] == "test-admin"


def test_middleware_public_prefixes_pass_through(auth):
    """白名单端点（如 /auth/me）不要求登录 — 不吃 401。"""
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def me(request):                                      # noqa: ARG001
        return JSONResponse({"ok": True, "user": None})

    client = _make_test_app(auth, [Route("/slirn/api/auth/me", me, methods=["GET"])])
    r = client.get("/slirn/api/auth/me")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "user": None}


def test_install_auth_middleware_actually_enforces(auth, monkeypatch):
    """回归守卫：install_auth_middleware 必须强制 rebuild middleware_stack，
    否则 uvicorn 已启动时仅改 user_middleware 等于没装（实测未登录返 200）。
    这里模拟「server 已被访问一次（middleware_stack 已缓存）+ 再装」的恢复路径场景。"""
    monkeypatch.delenv("SLIRN_AUTH_BYPASS", raising=False)
    from starlette.applications import Starlette
    from starlette.testclient import TestClient
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from slirn_home.auth import install_auth_middleware

    async def hello(request):                                   # noqa: ARG001
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/slirn/api/hi", hello, methods=["GET"])])
    # 先触发 middleware_stack 构造（模拟 uvicorn 已启动 + 服务过请求）
    _ = TestClient(app).get("/slirn/api/hi")
    assert app.middleware_stack is not None
    # 安装中间件
    install_auth_middleware(app, auth)
    # 强制重建后的 stack 应该是新对象
    new_stack = app.middleware_stack
    assert new_stack is not None
    # 未登录请求必须被拦（401）
    r = TestClient(app).get("/slirn/api/hi")
    assert r.status_code == 401


# ---------- app.py / launch.py / router.js 接线守卫（source-grep） ----------

def test_app_py_wires_task_access_and_members_ui():
    src = (FUNCLIP_ROOT / "slirn_home" / "app.py").read_text(encoding="utf-8")
    assert "def slirn_task_access" in src, "app.py 必须有 slirn_task_access 工厂"
    assert "task_access=slirn_task_access(mgr, auth)" in src, (
        "install_auth_middleware 必须传 task_access")
    # create_task 记录创建者 + 自动加成员
    i = src.find('"/slirn/api/create_task"')
    j = src.find("\n    @app.app.post", i + 1)
    body = src[i:j]
    assert "created_by=creator" in body, "create_task 必须传 created_by"
    assert "add_task_member(task.task_id, creator)" in body, (
        "create_task 必须把创建者加为成员")
    # 编辑页成员管理块 + 用户管理 modal
    assert "slirn-members-section" in src, "编辑页必须有成员管理折叠块"
    assert "slirn-users-modal" in src, "必须有用户管理 modal（admin）"
    assert 'data-action="member-add"' in src
    assert 'data-action="users-create"' in src


def test_launch_py_passes_task_access():
    src = (FUNCLIP_ROOT / "funclip" / "launch.py").read_text(encoding="utf-8")
    assert src.count("task_access=_slirn_task_access(_mgr, _auth)") == 2, (
        "launch.py 成功路径 + 恢复路径都必须传 task_access")


def test_router_js_member_and_users_actions():
    src = (FUNCLIP_ROOT / "slirn_home" / "static" / "router.js").read_text(encoding="utf-8")
    for marker in (
        "_loadTaskMembers", "_showUsersModal", "_hideUsersModal", "_loadUsers",
        "'users-open'", "'users-close'", "'users-create'", "'users-delete'",
        "'users-reset-pw'", "'member-add'", "'member-remove'",
        "_refreshTasksList",
    ):
        assert marker in src, f"router.js 缺少 {marker}"
    # 登录/登出后必须重拉任务列表
    i = src.find("action === 'do-login'")
    body = src[i:src.find("else if (action === 'logout')")]
    assert "_refreshTasksList" in body, "登录成功后必须刷新任务列表"
    i = src.find("action === 'logout'")
    body = src[i:i + 700]
    assert "_refreshTasksList" in body, "登出后必须刷新任务列表"