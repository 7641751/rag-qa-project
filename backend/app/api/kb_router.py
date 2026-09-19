# -*- coding: utf-8 -*-
"""知识库端点（契约 docs/api/README.md「知识库端点」）。

P3 起三个端点都按**归属**工作：上传时把 user_id 写进每段 metadata，列表与删除只作用于
本人的文档。删别人的 → 过滤命中 0 条 → `404 NOT_FOUND`（**不是 403**：403 会泄露
「该 doc_id 存在但不属于你」）。

⚠ 这里**不查 MySQL**：归属的依据是 Chroma 的 metadata，不是 conversations/users 表。
所以三个端点都不需要 db session —— 早先 delete 上挂着的 `DbSession` 是从模板抄来的死参数。
"""
import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile

from backend.app.agent.schemas import AuthUser
from backend.app.api.deps import get_current_user
from backend.app.services import upload_service
from backend.tools.http_tools import err
from backend.tools.upload_function_tools import (
    builtin_stats, delete_upload_doc, get_upload_doc, list_upload_docs,
    remove_upload_copies,
)

router = APIRouter(prefix="/api/kb", tags=["kb"])
CurrentUser = Annotated[AuthUser, Depends(get_current_user)]


@router.post("/documents")
async def upload_documents(user: CurrentUser, file: UploadFile = File(...)):
    """上传文档入库，以 SSE 流式返回处理进度（progress × N → done）。

    ⚠ 必须 return：sse_upload 给出的是 StreamingResponse / JSONResponse，
    写成 `await sse_upload(file)` 会把响应体丢掉，前端只能拿到 null。
    ⚠ `user.id` 必须显式传入 —— 少了它文档没有归属，连上传者自己都检索不到
    （kb_filter 匹配不上），而且不报任何错。
    """
    return await upload_service.sse_upload(file, user.id)


@router.get("/documents")
async def get_documents(user: CurrentUser):
    """列出**本人**上传的文档，用于抽屉的文件列表。

    只含 origin="upload" 且 user_id=本人，按 uploaded_at 倒序；空库返回 200 + 空列表，不报 404。
    `builtin` 摘要是**全局**统计（预置文档对所有用户共享），刻意不加用户过滤。
    Chroma 查询是同步阻塞的，一律丢线程池，别卡住事件循环。
    """
    payload = {"documents": await asyncio.to_thread(list_upload_docs, user.id)}
    stats = await asyncio.to_thread(builtin_stats)
    if stats:  # builtin 整个对象可选，统计不到就省略，前端自动隐藏那行摘要
        payload["builtin"] = stats
    return payload


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, user: CurrentUser):
    """删除**本人**某上传文档的全部向量块，同时清掉 data/uploads/ 里的落盘原件。

    过滤条件含 origin=upload + user_id，一条 where 实现三件事：
    - 预置官方文档删不掉（它们没有 origin 字段）；
    - 别人的文档删不掉（命中 0 条 → 404，不泄露存在性）；
    - 重复删同一 doc_id 第二次仍然是 404 而不是 500（幂等）。
    """
    target = await asyncio.to_thread(get_upload_doc, doc_id, user.id)
    if target is None:
        return err(404, "NOT_FOUND", f"文档不存在: {doc_id}")

    deleted = await asyncio.to_thread(delete_upload_doc, doc_id, user.id)
    await asyncio.to_thread(remove_upload_copies, doc_id)
    return {"doc_id": doc_id, "filename": target["filename"], "deleted_chunks": deleted}
