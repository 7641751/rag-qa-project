from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from backend.app.services import chat_service
from backend.app.agent.schemas import ChatRequest

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("/stream")
async def stream_chat(chat_request: ChatRequest):
    # TODO: 流式聊天
    return StreamingResponse(
        chat_service.stream_chat(chat_request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},  # 关代理缓冲，否则 token 被憋住
    )


@router.get("/history")
async def get_chat_history(thread_id: str):
    # TODO: 获取聊天历史
    return chat_service.get_chat_history(thread_id)
    pass
