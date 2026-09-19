# rag_qa_project/backend/app/api/auth_router.py
"""账号端点（契约 docs/api/README.md「鉴权」）。"""
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.agent.schemas import Auth, AuthUser, LoginResponse
from backend.app.api.deps import get_current_user
from backend.app.services import auth_service
from backend.tools.mysql_db_tools import get_db_session

router = APIRouter(prefix="/api/auth", tags=["auth"])

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


@router.post("/register", status_code=201, response_model=AuthUser)
async def register(auth: Auth, db: DbSession):
    """注册。重名 → 409 USERNAME_TAKEN；字段不合法 → 422（契约格式）。"""
    return await auth_service.register(db, username=auth.username, password=auth.password)


@router.post("/login", response_model=LoginResponse)
async def login(auth: Auth, db: DbSession):
    """登录。失败 → 401 INVALID_CREDENTIALS（不区分用户名/密码错）。"""
    return await auth_service.login(db, username=auth.username, password=auth.password)


@router.get("/me", response_model=AuthUser)
async def me(user: Annotated[AuthUser, Depends(get_current_user)]):
    """返回当前登录用户（token 由 get_current_user 校验）。"""
    return user
