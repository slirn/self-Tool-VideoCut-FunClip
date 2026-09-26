"""REQ-20260926-NNN：用户管理 + 会话 + 项目成员 端点。

全部用 @app.app.post/get 散挂（与现有风格一致）；
中间件已在白名单放行 /auth/login /auth/logout /auth/me /auth/bootstrap，
其他端点要求中间件已注入 slirn_user。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import Body
from fastapi.responses import JSONResponse
from starlette.requests import Request as _StarletteRequest

from slirn_home.auth import AuthStore, get_current_user_from_context

log = logging.getLogger(__name__)


def _set_session_cookie(response: JSONResponse, token: str, max_age: int = 30 * 86400) -> None:
    """Set-Cookie: slirn_session=...; HttpOnly; SameSite=Lax; Path=/"""
    response.set_cookie(
        key="slirn_session",
        value=token,
        max_age=max_age,
        path="/",
        httponly=True,
        samesite="lax",
        # secure=False — 本地 HTTP 部署；HTTPS 时再开
    )


def _clear_session_cookie(response: JSONResponse) -> None:
    response.delete_cookie("slirn_session", path="/")


def register_auth_endpoints(app: Any, auth: AuthStore, repo_root: Path) -> None:
    """挂 /slirn/api/auth/* 端点到 app.app。"""

    # ---------- 登录（公开） ----------

    async def login(body: dict = Body(default_factory=dict)):
        username = (body.get("username") or "").strip()
        password = body.get("password") or ""
        if not username or not password:
            return JSONResponse(
                {"ok": False, "error": "用户名和密码必填"}, status_code=400
            )
        user = auth.verify_credentials(username, password)
        if user is None:
            return JSONResponse(
                {"ok": False, "error": "用户名或密码错误"}, status_code=401
            )
        token = auth.create_session(username, ttl_days=30)
        resp = JSONResponse({"ok": True, "user": user})
        _set_session_cookie(resp, token)
        log.info("登录: %s", username)
        return resp

    app.app.post("/slirn/api/auth/login")(login)

    # ---------- 登出（公开） ----------

    async def logout(body: dict = Body(default_factory=dict)):
        token = (body.get("token") or "").strip()
        if not token:
            # 从 cookie 删即可
            resp = JSONResponse({"ok": True})
            _clear_session_cookie(resp)
            return resp
        auth.delete_session(token)
        resp = JSONResponse({"ok": True})
        _clear_session_cookie(resp)
        return resp

    app.app.post("/slirn/api/auth/logout")(logout)

    # ---------- 当前用户（公开：无登录返 user:null） ----------

    async def me(request: _StarletteRequest):                                # noqa: ARG001 — starlette Request
        # 中间件已挂白名单放行 /me，未登录 → user:null
        user = getattr(getattr(request, "state", None), "slirn_user", None)
        if user is None:
            # 兜底：直接从 cookie 读（无中间件时）
            from slirn_home.auth import SlirnAuthMiddleware
            token = None
            for k, v in request.scope.get("headers", []):
                if k == b"cookie":
                    for part in v.decode("latin-1").split(";"):
                        name, _, val = part.strip().partition("=")
                        if name == "slirn_session":
                            token = val
                            break
                    if token:
                        break
            user = auth.user_from_token(token) if token else None
        return {"ok": True, "user": user}

    app.app.get("/slirn/api/auth/me")(me)

    # ---------- bootstrap：仅无用户时可用 ----------

    async def bootstrap(body: dict = Body(default_factory=dict)):
        if auth.count_users() > 0:
            return JSONResponse(
                {"ok": False, "error": "系统已有用户，禁止 bootstrap"},
                status_code=403,
            )
        username = (body.get("username") or "").strip()
        password = body.get("password") or ""
        if not username or not password:
            return JSONResponse(
                {"ok": False, "error": "用户名和密码必填"}, status_code=400
            )
        if len(password) < 4:
            return JSONResponse(
                {"ok": False, "error": "密码至少 4 位"}, status_code=400
            )
        try:
            user = auth.create_user(username, username, password, is_admin=True)
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        token = auth.create_session(username, ttl_days=30)
        resp = JSONResponse({"ok": True, "user": {
            "username": user["username"],
            "display_name": user["display_name"],
            "is_admin": True,
        }})
        _set_session_cookie(resp, token)
        log.info("bootstrap 创建 admin: %s", username)
        return resp

    app.app.post("/slirn/api/auth/bootstrap")(bootstrap)

    # ---------- 用户管理（需登录；写操作需 admin） ----------

    async def list_users():
        users = auth.list_users()
        # 抹掉 salt/hash 字段，对外只暴露公开摘要
        safe = [{
            "username": u["username"],
            "display_name": u.get("display_name", u["username"]),
            "is_admin": u.get("is_admin", False),
            "created_at": u.get("created_at"),
        } for u in users]
        return {"ok": True, "users": safe}

    app.app.get("/slirn/api/auth/users")(list_users)

    async def create_user_endpoint(body: dict = Body(default_factory=dict)):
        me_user = get_current_user_from_context()
        if not me_user.get("is_admin"):
            return JSONResponse({"ok": False, "error": "需 admin 权限"},
                                status_code=403)
        username = (body.get("username") or "").strip()
        display_name = (body.get("display_name") or username).strip()
        password = body.get("password") or ""
        is_admin = bool(body.get("is_admin", False))
        if not username or not password:
            return JSONResponse(
                {"ok": False, "error": "用户名和密码必填"}, status_code=400
            )
        if len(password) < 4:
            return JSONResponse(
                {"ok": False, "error": "密码至少 4 位"}, status_code=400
            )
        try:
            u = auth.create_user(username, display_name, password, is_admin=is_admin)
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        return {"ok": True, "user": {
            "username": u["username"], "display_name": u["display_name"],
            "is_admin": u["is_admin"], "created_at": u["created_at"],
        }}

    app.app.post("/slirn/api/auth/users")(create_user_endpoint)

    async def delete_user_endpoint(username: str):
        me_user = get_current_user_from_context()
        if not me_user.get("is_admin"):
            return JSONResponse({"ok": False, "error": "需 admin 权限"},
                                status_code=403)
        if username == me_user.get("username"):
            return JSONResponse(
                {"ok": False, "error": "不能删除自己"}, status_code=400
            )
        ok = auth.delete_user(username)
        if not ok:
            return JSONResponse({"ok": False, "error": "用户不存在"}, status_code=404)
        return {"ok": True}

    app.app.delete("/slirn/api/auth/users/{username}")(delete_user_endpoint)

    async def change_password_endpoint(body: dict = Body(default_factory=dict)):
        me_user = get_current_user_from_context()
        target = (body.get("username") or me_user.get("username") or "").strip()
        new_pw = body.get("new_password") or ""
        # admin 可改任何人；非 admin 只能改自己
        if target != me_user.get("username") and not me_user.get("is_admin"):
            return JSONResponse({"ok": False, "error": "无权修改他人密码"},
                                status_code=403)
        if len(new_pw) < 4:
            return JSONResponse({"ok": False, "error": "密码至少 4 位"},
                                status_code=400)
        if not auth.update_user_password(target, new_pw):
            return JSONResponse({"ok": False, "error": "用户不存在"},
                                status_code=404)
        return {"ok": True}

    app.app.post("/slirn/api/auth/users/change_password")(change_password_endpoint)

    # ---------- 任务成员管理（需登录 + 是该任务成员/admin） ----------

    async def list_members_endpoint(tid: str):
        return {"ok": True, "members": auth.list_task_members(tid)}

    app.app.get("/slirn/api/auth/tasks/{tid}/members")(list_members_endpoint)

    async def add_member_endpoint(tid: str, body: dict = Body(default_factory=dict)):
        me_user = get_current_user_from_context()
        # 成员管理端点：必须 admin 或 创建者（成员加成员）
        from tasklib import TaskManager            # noqa: PLC0415
        from tasklib.exceptions import TaskNotFoundError  # noqa: PLC0415
        # 复用 slirn_home.app 内的 mgr（通过 repo_root 拿到 — 简化：从 TaskManager(repo_root) 构造）
        mgr = TaskManager(repo_root)
        try:
            task = mgr.get(tid)
        except TaskNotFoundError:
            return JSONResponse({"ok": False, "error": "任务不存在"},
                                status_code=404)
        creator = getattr(task, "created_by", None)
        if not (me_user.get("is_admin") or me_user.get("username") == creator):
            return JSONResponse(
                {"ok": False, "error": "需 admin 权限或是任务创建者"},
                status_code=403,
            )
        username = (body.get("username") or "").strip()
        if not username:
            return JSONResponse({"ok": False, "error": "username 必填"},
                                status_code=400)
        if auth.get_user(username) is None:
            return JSONResponse({"ok": False, "error": "用户不存在"},
                                status_code=404)
        added = auth.add_task_member(tid, username)
        return {"ok": True, "added": added, "members": auth.list_task_members(tid)}

    app.app.post("/slirn/api/auth/tasks/{tid}/members")(add_member_endpoint)

    async def remove_member_endpoint(tid: str, username: str):
        me_user = get_current_user_from_context()
        from tasklib import TaskManager            # noqa: PLC0415
        from tasklib.exceptions import TaskNotFoundError  # noqa: PLC0415
        mgr = TaskManager(repo_root)
        try:
            task = mgr.get(tid)
        except TaskNotFoundError:
            return JSONResponse({"ok": False, "error": "任务不存在"},
                                status_code=404)
        creator = getattr(task, "created_by", None)
        if not (me_user.get("is_admin") or me_user.get("username") == creator):
            return JSONResponse(
                {"ok": False, "error": "需 admin 权限或是任务创建者"},
                status_code=403,
            )
        removed = auth.remove_task_member(tid, username)
        return {"ok": True, "removed": removed, "members": auth.list_task_members(tid)}

    app.app.delete("/slirn/api/auth/tasks/{tid}/members/{username}")(remove_member_endpoint)