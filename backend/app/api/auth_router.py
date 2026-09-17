# -*- coding: utf-8 -*-
"""账号端点（契约 docs/api/README.md「鉴权」）。

⚠ 三个端点当前是 P2 的**占位实现**，显式抛 501。
原先是 `pass` —— 那会让 FastAPI 返回 `200 null`，比 404 更具误导性：调用方会以为
「请求成功但没数据」，而实际是功能根本不存在。真实实现见
docs/superpowers/plans/2026-09-17-p2-account-auth.md 的 Task 6。
"""
from fastapi import APIRouter

from backend.app.agent.schemas import Auth
from backend.tools.http_tools import api_error

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register")
async def register(auth: Auth):
    """注册。占位：501（Task 6 接 auth_service.register）。"""
    api_error(501, "NOT_IMPLEMENTED", "注册待实现")


@router.post("/login")
async def login(auth: Auth):
    """登录。占位：501（Task 6 接 auth_service.login）。"""
    api_error(501, "NOT_IMPLEMENTED", "登录待实现")


@router.get("/me")
async def me():
    """当前登录用户。占位：501（Task 6 接 get_current_user）。"""
    api_error(501, "NOT_IMPLEMENTED", "me 待实现")
