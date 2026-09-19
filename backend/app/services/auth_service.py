"""注册与登录的业务逻辑。密码哈希/JWT 签发复用 tools/security.py。"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.agent.schemas import AuthUser, LoginResponse
from backend.app.models import User
from backend.tools.http_tools import api_error
from backend.tools.security import create_access_token, hash_password, verify_password
# 登录失败统一文案：故意不区分「用户不存在」与「密码错」，避免用户名枚举（spec §6.4）
_LOGIN_FAILED = "用户名或密码错误"



async def register(db: AsyncSession, *, username: str, password: str)->AuthUser:
    existing = await db.scalar(select(User.id).where(User.username == username))
    if existing is not None:
        api_error(409, "USERNAME_TAKEN", "用户名已存在")

    user = User(username=username, password_hash=hash_password(password))

    db.add(user)
    await db.commit()
    await db.refresh(user)
    return AuthUser(id=user.id, username=user.username)


async def login(db: AsyncSession, *, username: str, password: str) -> LoginResponse:
    """登录。失败一律 401 INVALID_CREDENTIALS（不区分原因）。"""
    user = await db.scalar(select(User).where(User.username == username))
    # 注意：verify_password 对脏哈希/超长口令都回 False，永不抛异常
    if user is None or not verify_password(password, user.password_hash):
        api_error(401, "INVALID_CREDENTIALS", _LOGIN_FAILED)

    return LoginResponse(
        access_token=create_access_token(user.id, user.username),
        user=AuthUser(id=user.id, username=user.username))