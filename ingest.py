# -*- coding: utf-8 -*-
"""知识库入库脚本：加载 -> 切分 -> 嵌入 -> Chroma 持久化。

运行方式（在 rag_qa_project 目录下）：
    python ingest.py            # 幂等：已有库则跳过，--force 强制重建
    python ingest.py --force
"""
import os
import re
import sys
from pathlib import Path

from langchain_community.embeddings import DashScopeEmbeddings
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter, Language
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[2] / ".env")   # 项目根 .env

# Windows 控制台 UTF-8 输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 关键：HF 镜像必须在任何 langchain/chromadb/huggingface_hub 导入之前设置，
# 否则 huggingface_hub 会把 ENDPOINT 固化为 huggingface.co 导致下载失败
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from config import settings


def build_embeddings():
    return (
    DashScopeEmbeddings(
    model="qwen3.7-text-embedding",
    dashscope_api_key=os.getenv("DASHSCOPE_API_KEY"),
))


def load_documents(docs_dir: Path):
    """递归加载目录下所有 .md，从首个 # 标题提取 title，路径作为 source。"""
    docs = []
    for p in sorted(docs_dir.rglob("*.md")):
        if p.name == "INDEX.md":  # 只是目录，跳过避免重复噪声
            continue
        text = p.read_text(encoding="utf-8")
        m = re.search(r"^#\s+(.+)$", text, flags=re.M)
        title = m.group(1).strip() if m else p.stem
        docs.append(Document(
            page_content=text,
            metadata={
                "source": str(p.relative_to(docs_dir)).replace("\\", "/"),
                "title": title,
                "kb": "langchain_docs",
            },
        ))
    return docs


def split_documents(docs: list[Document]):
    """递归切分 markdown 文档为 chunks。

    使用 markdown 感知的分隔符（标题 #{1,6}、代码围栏、水平线、段落）优先切分，
    再按 chunk_size 限长、chunk_overlap 保留相邻块重叠，尽量避免语义被截断。
    """

    splitter = RecursiveCharacterTextSplitter.from_language(Language.MARKDOWN, chunk_size=settings.chunk_size,
                                                            chunk_overlap=settings.chunk_overlap)
    return splitter.split_documents(docs)


def ingest(force: bool = False):
    if settings.chroma_dir.exists() and any(settings.chroma_dir.iterdir()) and not force:
        print(f"[skip] 向量库已存在: {settings.chroma_dir}（--force 重建）")
        return

    from langchain_chroma import Chroma

    docs = load_documents(settings.docs_dir)
    print(f"[1/3] 加载文档: {len(docs)} 篇")

    chunks = split_documents(docs)
    print(f"[2/3] 切分 chunks: {len(chunks)} 段")

    embeddings = build_embeddings()
    settings.chroma_dir.mkdir(parents=True, exist_ok=True)
    vectorstore = Chroma(
        collection_name=settings.collection_name,
        embedding_function=embeddings,
        persist_directory=str(settings.chroma_dir),
    )
    if force:  # 重建：先删除目标 collection，避免与旧向量重复
        try:
            vectorstore.delete_collection()
        except Exception:
            pass
        vectorstore = Chroma(
            collection_name=settings.collection_name,
            embedding_function=embeddings,
            persist_directory=str(settings.chroma_dir),
        )

    # DashScope 嵌入接口单次批量上限为 20 条，超过会报 400 InvalidParameter，
    # 因此必须分批写入（留余量取 10）。
    EMBED_BATCH = 10
    total = len(chunks)
    for i in range(0, total, EMBED_BATCH):
        vectorstore.add_documents(chunks[i:i + EMBED_BATCH])
        done = min(i + EMBED_BATCH, total)
        print(f"\r[3/3] 嵌入入库: {done}/{total}", end="", flush=True)
    print()
    print(f"[done] 入库完成: {vectorstore._collection.count()} 条向量 -> {settings.chroma_dir}")


if __name__ == "__main__":
    ingest(force="--force" in sys.argv)
