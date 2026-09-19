# rag_qa_project/backend/app/api/chat_router.py
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.agent.schemas import AuthUser, ChatDeleteResponse, ChatRequest
from backend.app.api.deps import get_current_user
from backend.app.services import chat_service
from backend.tools.mysql_db_tools import get_db_session

router = APIRouter(prefix="/api/chat", tags=["chat"])

DbSession = Annotated[AsyncSession, Depends(get_db_session)]
CurrentUser = Annotated[AuthUser, Depends(get_current_user)]


@router.post("/stream")
async def stream_chat(chat_request: ChatRequest, user: CurrentUser, db: DbSession):
    """流式聊天（要求登录 + 归属校验）。

    403 由 prepare_stream 在返回 StreamingResponse 之前抛出。
    """
    agen = await chat_service.prepare_stream(chat_request, user.id, db)
    return StreamingResponse(
        agen, media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},   # 关代理缓冲，否则 token 被憋住
    )


@router.get("/history")
async def get_chat_history(thread_id: str, user: CurrentUser, db: DbSession):
    """聊天历史。无行 → 200 空数组（既有约定，前端新会话依赖它）；非本人 → 403。"""
    return await chat_service.get_chat_history(thread_id, user.id, db)


@router.delete("/threads/{thread_id}", response_model=ChatDeleteResponse)
async def delete_chat(thread_id: str, user: CurrentUser, db: DbSession):
    """删除一个会话在服务端的全部状态（checkpoints + writes + 索引行）。

    不可恢复；幂等：未知/已删过的 thread_id 返回 deleted=false（非 404）。
    thread_id 用 str 而非 UUID：与 /history 一致，契约也建议后端用 str。
    """
    return await chat_service.delete_chat_thread(thread_id, user.id, db)