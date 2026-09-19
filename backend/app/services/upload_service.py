# -*- coding: utf-8 -*-
"""知识库上传服务：multipart 校验 -> SSE 逐帧进度 -> 分批嵌入 Chroma。

契约见 docs/api/README.md「知识库端点」。三个关键设计：

- **流开始前**的错误用 HTTP 状态码（415/413/422），**流开始后**只能发 SSE error 帧
  ——响应头已经出去了，改不了状态码。校验必须在返回 StreamingResponse 之前做完，
  否则前端要为同一个端点写两套错误处理。
- **同步阻塞活儿一律丢 asyncio.to_thread**（解析、切分、嵌入、落盘）。直接调用会占住
  事件循环，别的请求（包括正在跑的聊天流）全部卡死。
- **中途取消必须回滚**：客户端 abort 时异步生成器收到的是 asyncio.CancelledError，
  已写入的"半截"向量会污染检索，必须按 doc_id 连向量带原件一起清掉。
"""
import asyncio
import uuid

from fastapi import File, UploadFile
from starlette.responses import StreamingResponse

from backend.tools.http_tools import err
from backend.tools.upload_function_tools import (
    EMBED_BATCH, delete_upload_doc, doc_title, embed_batch, find_upload_doc_ids,
    now_iso, parse_and_split, remove_upload_copies, save_upload_copy, sse,
)

ALLOWED_EXT = (".md", ".txt", ".pdf")
MAX_BYTES = 20 * 1024 * 1024


def _rollback(doc_id: str) -> int:
    """回滚：删掉已写入的向量 + 落盘原件，返回清除的向量条数。

    这里**故意不走 asyncio.to_thread**：CancelledError / GeneratorExit 分支里再 await，
    很可能被二次取消打断，回滚就做不完了。Chroma 按 metadata 删除是毫秒级 SQLite 操作，
    用"短暂阻塞事件循环"换"回滚一定执行完"是划算的。
    """
    gone = delete_upload_doc(doc_id)
    remove_upload_copies(doc_id)
    print(f"[kb] ↳ 回滚 doc_id={doc_id[:8]}…（清除 {gone} 段向量）")
    return gone


async def stream_upload(filename: str, raw: bytes, doc_id: str,
                        uploaded_at: str, replaced: bool):
    """异步生成器：每 yield 一个字符串就是一帧 SSE。

    成功时序：progress(saved) -> progress(parsed) -> progress(split)
              -> progress(embedding) × N -> done
    失败：发 error 后关闭流，**不再有 done**。
    """
    written = 0
    try:
        yield sse("progress", {"stage": "saved",
                               "message": f"已接收 {filename} ({len(raw) / 1024:.1f} KB)"})

        # 解析 + 切分：两类失败要分开报，前端文案不一样
        try:
            chunks = await asyncio.to_thread(parse_and_split, raw, filename)
        except ValueError:
            yield sse("error", {"code": "EMPTY_DOCUMENT", "message": "解析后无有效文本"})
            _rollback(doc_id)
            return
        except Exception as exc:  # PDF 加密/损坏、编码无法识别
            yield sse("error", {"code": "PARSE_ERROR",
                                "message": f"解析失败：{type(exc).__name__}: {exc}"})
            _rollback(doc_id)
            return

        yield sse("progress", {"stage": "parsed",
                               "message": f"解析出 {sum(len(c) for c in chunks)} 字符"})

        total = len(chunks)
        # split 阶段一次性给出 current = total，前端把它当 20% 基线
        yield sse("progress", {"stage": "split", "current": total, "total": total,
                               "message": f"切分为 {total} 段"})

        # metadata 必须在写入时就带齐：doc_id/filename/origin/uploaded_at 是契约必填，
        # 列表、删除、同名替换、取消回滚全靠它们定位。
        meta = {
            "doc_id": doc_id,
            "filename": filename,
            "origin": "upload",
            "uploaded_at": uploaded_at,
            "title": doc_title(filename, chunks),
            "source": f"uploads/{doc_id}__{filename}",
            "size_bytes": len(raw),
        }

        # 每批一次 progress —— 这是"真实进度"，不是前端假装的
        for i in range(0, total, EMBED_BATCH):
            try:
                n = await asyncio.to_thread(embed_batch, chunks[i:i + EMBED_BATCH], meta, i)
            except Exception as exc:  # DashScope 限额 / 网络 / key 失败
                yield sse("error", {"code": "EMBEDDING_ERROR",
                                    "message": f"嵌入失败（第 {i + 1} 段起）："
                                               f"{type(exc).__name__}: {exc}"})
                _rollback(doc_id)
                return
            written += n
            yield sse("progress", {"stage": "embedding", "current": written, "total": total,
                                   "message": f"嵌入 {written}/{total}"})

        yield sse("done", {"doc_id": doc_id, "filename": filename, "chunks": written,
                           "replaced": replaced, "uploaded_at": uploaded_at})
        print(f"[kb] ✔ 入库完成 doc_id={doc_id[:8]}… {filename} 共 {written} 段")

    # 客户端 abort 时，异步生成器收到的是 CancelledError（实测，不是 GeneratorExit）
    except asyncio.CancelledError:
        print("[kb] ✘ 收到 asyncio.CancelledError")
        _rollback(doc_id)
        raise  # 关键：不要吞掉取消信号
    except GeneratorExit:
        print("[kb] ✘ 收到 GeneratorExit")
        _rollback(doc_id)
        # ⚠ 这里**绝不能再 yield**，否则 RuntimeError: async generator ignored GeneratorExit
        raise
    except Exception as exc:  # 流已开始，只能用 SSE error 通知
        print(f"[kb] ✘ 收到普通异常 {type(exc).__name__}: {exc}")
        yield sse("error", {"code": "INTERNAL_ERROR", "message": f"{type(exc).__name__}: {exc}"})
        _rollback(doc_id)
    finally:
        print(f"[kb]   finally 执行（written={written}）")


async def sse_upload(file: UploadFile = File(...)):
    """POST /api/kb/documents 的实际处理：校验 -> 落盘 -> 返回 SSE 流。"""
    filename = file.filename or "unknown.bin"
    if not filename.lower().endswith(ALLOWED_EXT):
        return err(415, "UNSUPPORTED_FILE_TYPE", f"仅支持 {' / '.join(ALLOWED_EXT)}")

    raw = await file.read()  # ≤20MB 直接读进内存最省事
    if len(raw) > MAX_BYTES:
        return err(413, "FILE_TOO_LARGE", f"文件大小不能超过 {MAX_BYTES // (1024 * 1024)} MB")
    if not raw:
        return err(422, "VALIDATION_ERROR", "文件为空")

    # 同名视为替换（契约 §4.2）：先删旧向量与旧落盘原件，再按新 doc_id 入库
    old_ids = await asyncio.to_thread(find_upload_doc_ids, filename)
    for old_id in old_ids:
        await asyncio.to_thread(delete_upload_doc, old_id)
        await asyncio.to_thread(remove_upload_copies, old_id)
    replaced = bool(old_ids)

    doc_id = str(uuid.uuid4())
    # uploaded_at 在嵌入前就定下来并写进每段 metadata：Chroma 事后改 metadata
    # 等于整块重嵌，所以不能用"全部完成后再补时间戳"的写法。
    uploaded_at = now_iso()
    await asyncio.to_thread(save_upload_copy, doc_id, filename, raw)

    return StreamingResponse(
        stream_upload(filename, raw, doc_id, uploaded_at, replaced),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},  # 关代理缓冲，否则帧被憋住
    )
