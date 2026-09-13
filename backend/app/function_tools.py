# -*- coding: utf-8 -*-
"""知识库上传链路的通用工具：SSE 帧 / 错误响应 / 解析切分 / 嵌入入库 / 元数据查询。

契约见 docs/api/README.md「知识库端点」。改这里之前先记住三条硬约定：

1. **不在 import 期碰向量库**。get_vectorstore() 会加载 Chroma + DashScope 嵌入，
   写成模块级变量等于把重活提到进程启动，知识库没入库时整个 app 都起不来；
   它本身是 @lru_cache 单例，函数里现取的开销只是一次字典查找。
2. **每个向量块写入时就必须带齐 doc_id / filename / origin / uploaded_at**。
   列表、删除、同名替换、取消回滚全靠 metadata 过滤定位，缺一个就变成"孤儿向量"，
   而 Chroma 事后补 metadata 等于整块重新嵌入。
3. **凡是删/查用户上传的过滤条件都带 origin="upload"**。预置的官方文档没有该字段，
   天然删不掉也列不出，不需要额外的 403 分支。
   另注：Chroma 的 $and 至少要两个条件，单条件必须直接写 {"origin": "upload"}，
   包一层 $and 会抛 ValueError。
"""
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi.responses import JSONResponse
from langchain_core.documents import Document
from langchain_text_splitters import Language, RecursiveCharacterTextSplitter

from config import settings
from backend.app.agent.graph import get_vectorstore

# DashScope 嵌入接口单次批量上限 20 条，超过报 400 InvalidParameter，留余量取 10。
# 全项目只有这一个定义，别在调用方再写一份（会慢慢跑偏）。
EMBED_BATCH = 10

# 预置文档在 ingest.py 里统一打的 kb 标记，用来和 origin="upload" 的上传项分开统计。
BUILTIN_KB = "langchain_docs"

# 切分器无状态，模块级复用即可，避免每次上传都重新实例化。
# md 用 markdown 感知分隔符（标题 #{1,6} / 代码围栏 / 水平线），txt、pdf 抽出的
# 纯文本没有这些结构，用通用分隔符即可。chunk_size/overlap 与 ingest.py 共用 settings。
_MD_SPLITTER = RecursiveCharacterTextSplitter.from_language(
    Language.MARKDOWN, chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap)
_PLAIN_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap)


# ---------------------------------------------------------------- HTTP / SSE 原语
def err(status: int, code: str, message: str) -> JSONResponse:
    """契约要求错误体是 {"code","message"}，而 raise HTTPException 会得到 {"detail": ...}，
    所以流开始前的错误**直接返回 JSONResponse**（比装异常处理器简单）。"""
    return JSONResponse(status_code=status, content={"code": code, "message": message})


def now_iso() -> str:
    """ISO 8601 UTC 时间字符串，如 2026-09-11T10:23:41Z。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sse(event: str, data: dict) -> str:
    """SSE 帧 = event 行 + data 行 + **空行**。ensure_ascii=False 让中文可读。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------- 解析与切分
def parse_and_split(raw: bytes, filename: str) -> list[str]:
    """【同步阻塞】解析 + 切分。

    PDF 用 pypdf 抽文本，其余按 UTF-8 解码；解析后无有效文本抛 ValueError，
    由上层转成 EMPTY_DOCUMENT 错误帧。调用方必须丢进 asyncio.to_thread，
    否则会占住事件循环，把同时在跑的聊天流一起卡死。
    """
    is_md = filename.lower().endswith(".md")
    if raw[:4] == b"%PDF":
        from pypdf import PdfReader
        # PdfReader 只接受文件路径或文件流，不能直接吃 bytes，必须包一层 BytesIO
        reader = PdfReader(io.BytesIO(raw))
        # extract_text() 对空白页可能返回 None，用 or "" 兜底避免 join 抛 TypeError
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        splitter = _PLAIN_SPLITTER
    else:
        text = raw.decode("utf-8", errors="ignore")
        splitter = _MD_SPLITTER if is_md else _PLAIN_SPLITTER

    if not text.strip():
        raise ValueError("empty")
    return splitter.split_text(text)


def doc_title(filename: str, chunks: list[str]) -> str:
    """契约里 title 可选：md 取首个 `# 标题`，pdf/txt 回退为文件名（去扩展名）。

    标题一般落在第一段，但切分后不保证在 chunks[0]，所以顺序扫到命中为止。
    """
    if filename.lower().endswith(".md"):
        for chunk in chunks:
            m = re.search(r"^#\s+(.+)$", chunk, flags=re.M)
            if m:
                return m.group(1).strip()
    return filename.rsplit(".", 1)[0] or filename


# ---------------------------------------------------------------- Chroma 读写
def _collection():
    """当前 Chroma collection。get_vectorstore() 是 lru_cache 单例，重复调用零成本。"""
    return get_vectorstore()._collection


def embed_batch(chunks: list[str], meta: dict, start: int = 0) -> int:
    """【同步阻塞】把一批纯文本包成 Document 写入 Chroma，返回实际入库段数。

    add_documents 只吃 Document：直接喂 list[str] 会抛
    AttributeError: 'str' object has no attribute 'id'（LangChain 要读 doc.id）。
    start 是本批在全文中的起始下标，用来给 chunk 编号，删除/重排时能还原原文顺序。
    """
    docs = [Document(page_content=text, metadata={**meta, "chunk": start + i})
            for i, text in enumerate(chunks)]
    get_vectorstore().add_documents(docs)
    return len(docs)


def find_upload_doc_ids(filename: str) -> list[str]:
    """同名替换（契约 §4.2）：按 filename + origin=upload 找出已存在的旧 doc_id。"""
    got = _collection().get(
        where={"$and": [{"filename": filename}, {"origin": "upload"}]},
        include=["metadatas"])
    # 同一 doc_id 有 N 段向量，去重后才是文档数
    return sorted({md["doc_id"] for md in (got["metadatas"] or []) if md.get("doc_id")})


def get_upload_doc(doc_id: str) -> dict | None:
    """按 doc_id 取单个上传文档的元信息（含 chunks 计数），不存在返回 None。

    include=[] 是刻意的：这里只要 ids 计数和一份 metadata，
    省掉 include 的话 Chroma 会把每段 page_content 全拉出来。
    """
    got = _collection().get(
        where={"$and": [{"doc_id": doc_id}, {"origin": "upload"}]},
        include=["metadatas"])
    metadatas = got["metadatas"] or []
    if not metadatas:
        return None
    md = metadatas[0]
    return {"doc_id": doc_id,
            "filename": md.get("filename", ""),
            "title": md.get("title"),
            "size_bytes": md.get("size_bytes"),
            "uploaded_at": md.get("uploaded_at", ""),
            "chunks": len(metadatas)}


def list_upload_docs() -> list[dict]:
    """列出所有 origin=upload 的文档，按 uploaded_at 倒序。空库返回 []，不报 404。

    ⚠ include 必须显式给 ["metadatas"]：省略时 Chroma 默认连 documents 一起返回，
    上传几百段就是数 MB 无用数据。ids 是**分块** id，文档 id 要从 metadata 里取。
    """
    got = _collection().get(where={"origin": "upload"}, include=["metadatas"])
    grouped: dict[str, dict] = {}
    for md in got["metadatas"] or []:
        doc_id = md.get("doc_id")
        if not doc_id:
            continue
        entry = grouped.setdefault(doc_id, {
            "doc_id": doc_id,
            "filename": md.get("filename", ""),
            "title": md.get("title"),
            "size_bytes": md.get("size_bytes"),
            "uploaded_at": md.get("uploaded_at", ""),
            "chunks": 0,
        })
        entry["chunks"] += 1
    # title / size_bytes 是可选字段，缺失就别吐 null 给前端
    return [{k: v for k, v in d.items() if v is not None}
            for d in sorted(grouped.values(), key=lambda x: x["uploaded_at"], reverse=True)]


def builtin_stats() -> dict | None:
    """预置文档统计（契约里整个 builtin 对象可选，统计不到就返回 None 由调用方省略）。"""
    got = _collection().get(where={"kb": BUILTIN_KB}, include=["metadatas"])
    metadatas = got["metadatas"] or []
    if not metadatas:
        return None
    return {"docs": len({md.get("source") for md in metadatas}), "chunks": len(metadatas)}


def delete_upload_doc(doc_id: str) -> int:
    """按 doc_id 删除该上传文档的全部向量块，返回删除条数（0 = 文档不存在）。

    $and 里同时含 origin=upload，一条过滤就实现了"预置文档不可删"：
    官方文档没有 origin 字段，命中 0 条 → 上层直接 404，无需额外 403 分支。
    先 get 再 delete 是为了拿到 deleted_chunks（Chroma 的 delete 不返回条数）。
    """
    where = {"$and": [{"doc_id": doc_id}, {"origin": "upload"}]}
    col = _collection()
    gone = len(col.get(where=where, include=[]).get("ids") or [])
    if gone:  # delete 未命中时是静默 no-op，这里省一次无谓写入
        col.delete(where=where)
    return gone


# ---------------------------------------------------------------- 原件落盘
# 向量库是可重建的派生数据，原件才是真相源：ingest.py --force 重建 collection 时
# 要靠 data/uploads/ 把用户文档捞回来，否则一次重建 = 用户资料永久丢失。
def _safe_disk_name(filename: str) -> str:
    """落盘文件名必须剥掉客户端可能夹带的路径分隔符，否则 ../ 能把文件写到项目外。"""
    name = Path(filename.replace("\\", "/")).name or "upload.bin"
    return re.sub(r'[<>:"|?*\x00-\x1f]', "_", name)


def save_upload_copy(doc_id: str, filename: str, raw: bytes) -> Path:
    """把上传原件写到 data/uploads/<doc_id>__<安全文件名>，返回落盘路径。"""
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    path = settings.uploads_dir / f"{doc_id}__{_safe_disk_name(filename)}"
    path.write_bytes(raw)
    return path


def remove_upload_copies(doc_id: str) -> None:
    """删掉该 doc_id 的落盘原件（回滚与 DELETE 共用）。找不到就静默跳过。"""
    for path in settings.uploads_dir.glob(f"{doc_id}__*"):
        path.unlink(missing_ok=True)
