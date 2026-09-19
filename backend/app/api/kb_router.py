# -*- coding: utf-8 -*-
"""知识库端点（契约 docs/api/README.md「知识库端点」）。"""
import asyncio
from typing import Annotated

from fastapi import APIRouter, File, UploadFile, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.agent.schemas import AuthUser
from backend.app.api.deps import get_current_user
from backend.tools.http_tools import err
from backend.tools.upload_function_tools import (
    builtin_stats, delete_upload_doc, get_upload_doc, list_upload_docs,
    remove_upload_copies,
)
from backend.app.services import upload_service
from backend.tools.mysql_db_tools import get_db_session
from backend.app.services import conversation_service

router = APIRouter(prefix="/api/kb", tags=["kb"])
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
CurrentUser = Annotated[AuthUser, Depends(get_current_user)]


# 不写 user_id，服务端无从判断某个 doc_id 属于谁，所以 _user 只声明、不使用。
# 用 `_` 前缀是刻意的 —— 标明"这里故意不用它"，免得后人误以为漏了校验，或顺手补一个
# 拿不到依据的 owner 判断。归属隔离留待 P3（届时 metadata 加 user_id + 检索过滤）。
@router.post("/documents")
async def upload_documents(_user: CurrentUser, file: UploadFile = File(...)):
    """上传文档入库，以 SSE 流式返回处理进度（progress × N → done）。

    ⚠ 必须 return：sse_upload 给出的是 StreamingResponse / JSONResponse，
    写成 `await sse_upload(file)` 会把响应体丢掉，前端只能拿到 null。
    """
    return await upload_service.sse_upload(file)


@router.get("/documents")
async def get_documents(_user: CurrentUser):
    """列出用户上传的文档，用于抽屉的文件列表。

    只含 origin="upload"，按 uploaded_at 倒序；空库返回 200 + 空列表，不报 404。
    Chroma 查询是同步阻塞的，一律丢线程池，别卡住事件循环。
    """
    payload = {"documents": await asyncio.to_thread(list_upload_docs)}
    stats = await asyncio.to_thread(builtin_stats)
    if stats:  # builtin 整个对象可选，统计不到就省略，前端自动隐藏那行摘要
        payload["builtin"] = stats
    return payload


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, _user: CurrentUser, db_session: DbSession):
    """删除某上传文档的全部向量块，同时清掉 data/uploads/ 里的落盘原件。

    过滤条件含 origin=upload，所以预置的官方文档天然删不掉（命中 0 条 → 404），
    无需额外的 403 分支；重复删同一 doc_id 第二次也是 404 而不是 500（幂等）。
    """


    target = await asyncio.to_thread(get_upload_doc, doc_id)
    if target is None:
        return err(404, "NOT_FOUND", f"文档不存在: {doc_id}")

    deleted = await asyncio.to_thread(delete_upload_doc, doc_id)
    await asyncio.to_thread(remove_upload_copies, doc_id)
    return {"doc_id": doc_id, "filename": target["filename"], "deleted_chunks": deleted}
