"""Pytest 配置 — REQ-20260926-NNN：所有 /slirn/api/* 测试默认放行 auth 中间件。

设置 SLIRN_AUTH_BYPASS=1 让中间件挂 test-admin 假 user；现有 TestClient 调用
无需改造 cookie/credentials。生产环境绝不要设置此 env。
"""
import os

os.environ.setdefault("SLIRN_AUTH_BYPASS", "1")