# -*- coding: utf-8 -*-
"""SSE 文件上传 · 最小可跑教程 demo（只依赖 fastapi + uvicorn，不碰 LangChain/Chroma）。

⚠ 本文件是**教学/实验用途**，不属于生产链路 —— 所以它住在 docs/examples/ 而不是 backend/。
   （原先放在 backend/ 顶层，容易被误读成生产代码。）

跑起来（在**本目录**下执行，它不 import 项目里任何模块）：
    uv run python -m uvicorn sse_upload_demo:app --port 8010

演示 5 件事，对应 docs/api/README.md 的「知识库端点」章节：
  ① multipart 读文件 + **流开始前**校验（415/413/422 用 HTTP 状态码，不降级成 SSE error）
  ② async generator 逐帧 yield SSE（progress × N → done）
  ③ 同步阻塞活儿丢线程池（asyncio.to_thread），否则卡死整个 uvicorn worker
  ④ 客户端中断时到底收到什么异常 → 决定回滚代码写在哪（本 demo 的核心实验）
  ⑤ 列表/删除用"metadata 过滤"（这里用内存 dict 模拟 Chroma 的 where 条件）
"""
import asyncio
import json
import time
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

ALLOWED_EXT = (".md", ".txt", ".pdf")
MAX_BYTES = 20 * 1024 * 1024
EMBED_BATCH = 10          # DashScope 单次上限 20，留余量取 10
BATCH_SECONDS = 0.6       # 模拟每批嵌入耗时，便于观察进度与中断

app = FastAPI(title="SSE 上传教程 demo")

# 内存"向量库"：doc_id -> 元信息。模拟 Chroma collection（真实项目里换成 get_vectorstore()）
STORE: dict[str, dict] = {
    "builtin-1": {"filename": "langchain_docs.md", "chunks": 2885, "origin": "builtin"},
}


# ---------------------------------------------------------------- 工具函数
def sse(event: str, data: dict) -> str:
    """SSE 帧 = event 行 + data 行 + **空行**。ensure_ascii=False 让中文可读。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def err(status: int, code: str, message: str) -> JSONResponse:
    """契约要求错误体是 {"code","message"}，而 raise HTTPException 会得到 {"detail": ...}，
    所以流开始前的错误**直接返回 JSONResponse**（比装异常处理器简单）。"""
    return JSONResponse(status_code=status, content={"code": code, "message": message})


def parse_and_split(raw: bytes) -> list[str]:
    """【同步阻塞】解析 + 切分。真实项目：pypdf 抽文本 + RecursiveCharacterTextSplitter。"""
    text = raw.decode("utf-8", errors="ignore")
    if not text.strip():
        raise ValueError("empty")
    return [text[i:i + 200] for i in range(0, len(text), 200)]


def embed_batch(batch: list[str]) -> int:
    """【同步阻塞】模拟调 DashScope 嵌入一批。真实项目：vectorstore.add_documents(batch)。"""
    time.sleep(BATCH_SECONDS)
    return len(batch)


def rollback(doc_id: str) -> None:
    """按 doc_id 抹掉已写入的向量。真实项目：
    get_vectorstore()._collection.delete(where={"doc_id": doc_id})"""
    gone = STORE.pop(doc_id, None)
    print(f"[demo]   ↳ 回滚 doc_id={doc_id[:8]}…（已写入 {gone['chunks'] if gone else 0} 段被清除）")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- 上传（SSE）
async def stream(filename: str, raw: bytes, doc_id: str, replaced: bool):
    """异步生成器：每 yield 一个字符串就是一帧 SSE。"""
    written = 0
    try:
        yield sse("progress", {"stage": "saved",
                               "message": f"已接收 {filename} ({len(raw) / 1024:.1f} KB)"})

        # ③ 阻塞活儿丢线程池：直接调用会占住事件循环，别的请求（包括聊天流）全部卡住
        try:
            chunks = await asyncio.to_thread(parse_and_split, raw)
        except ValueError:
            yield sse("error", {"code": "EMPTY_DOCUMENT", "message": "解析后无有效文本"})
            rollback(doc_id)
            return
        yield sse("progress", {"stage": "parsed",
                               "message": f"解析出 {sum(len(c) for c in chunks)} 字符"})

        total = len(chunks)
        yield sse("progress", {"stage": "split", "current": total, "total": total,
                               "message": f"切分为 {total} 段"})

        # ② 每批一次 progress —— 这就是"真实进度"的来源，不是前端假装的
        for i in range(0, total, EMBED_BATCH):
            n = await asyncio.to_thread(embed_batch, chunks[i:i + EMBED_BATCH])
            written += n
            STORE[doc_id]["chunks"] = written          # 模拟逐批落库
            yield sse("progress", {"stage": "embedding", "current": written, "total": total,
                                   "message": f"嵌入 {written}/{total}"})

        STORE[doc_id]["uploaded_at"] = now_iso()
        yield sse("done", {"doc_id": doc_id, "filename": filename, "chunks": written,
                           "replaced": replaced, "uploaded_at": STORE[doc_id]["uploaded_at"]})
        print(f"[demo] ✔ 正常完成 doc_id={doc_id[:8]}… 共 {written} 段")

    # ④ 核心实验：客户端 abort 时，异步生成器收到的到底是哪个异常？
    except asyncio.CancelledError:
        print("[demo] ✘ 收到 asyncio.CancelledError")
        rollback(doc_id)
        raise                                          # 关键：不要吞掉取消信号
    except GeneratorExit:
        print("[demo] ✘ 收到 GeneratorExit")
        rollback(doc_id)
        # ⚠ 这里**绝不能再 yield**，否则 RuntimeError: async generator ignored GeneratorExit
        raise
    except Exception as exc:                           # 流已开始，只能用 SSE error 通知
        print(f"[demo] ✘ 收到普通异常 {type(exc).__name__}: {exc}")
        yield sse("error", {"code": "INTERNAL_ERROR", "message": f"{type(exc).__name__}: {exc}"})
        rollback(doc_id)
    finally:
        print(f"[demo]   finally 执行（written={written}，库里剩 {len(STORE)} 个 doc）")


@app.post("/api/kb/documents")
async def upload_documents(file: UploadFile = File(...)):
    filename = file.filename or "unknown.bin"
    lower = filename.lower()
    # ① 流开始前的校验：能返回 HTTP 状态码就别降级成 SSE error，前端只写一套处理
    if not lower.endswith(ALLOWED_EXT):
        return err(415, "UNSUPPORTED_FILE_TYPE", f"仅支持 {' / '.join(ALLOWED_EXT)}")
    raw = await file.read()                            # ≤20MB 直接读进内存最省事
    if len(raw) > MAX_BYTES:
        return err(413, "FILE_TOO_LARGE", "文件超过 20 MB")
    if not raw:
        return err(422, "VALIDATION_ERROR", "文件为空")

    # 同名视为替换（契约 §4.2）：先按 filename + origin=upload 找到旧的并删掉
    replaced = False
    for old_id, meta in list(STORE.items()):
        if meta["filename"] == filename and meta.get("origin") == "upload":
            STORE.pop(old_id)
            replaced = True

    doc_id = str(uuid.uuid4())
    STORE[doc_id] = {"filename": filename, "chunks": 0, "origin": "upload",
                     "uploaded_at": now_iso(), "size_bytes": len(raw)}

    return StreamingResponse(
        stream(filename, raw, doc_id, replaced),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},           # 关代理缓冲，否则帧被憋住
    )


# ---------------------------------------------------------------- 列表 / 删除
@app.get("/api/kb/documents")
async def list_documents():
    """⑤ 只返回 origin=upload —— 等价于 Chroma 的 where={"origin":"upload"}。"""
    docs = [{"doc_id": k, "filename": v["filename"], "chunks": v["chunks"],
             "size_bytes": v.get("size_bytes"), "uploaded_at": v["uploaded_at"]}
            for k, v in STORE.items() if v.get("origin") == "upload"]
    docs.sort(key=lambda d: d["uploaded_at"], reverse=True)
    builtin_docs = sum(1 for v in STORE.values() if v.get("origin") == "builtin")
    builtin_chunks = sum(v["chunks"] for v in STORE.values() if v.get("origin") == "builtin")
    return {"documents": docs, "builtin": {"docs": builtin_docs, "chunks": builtin_chunks}}


@app.delete("/api/kb/documents/{doc_id}")
async def delete_document(doc_id: str):
    """⑤ 过滤条件必须同时含 origin=upload → 预置文档天然删不掉，无需额外 403 分支。"""
    meta = STORE.get(doc_id)
    if meta is None or meta.get("origin") != "upload":
        return err(404, "NOT_FOUND", f"文档不存在: {doc_id}")
    STORE.pop(doc_id)
    return {"doc_id": doc_id, "filename": meta["filename"], "deleted_chunks": meta["chunks"]}
