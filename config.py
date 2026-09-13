# -*- coding: utf-8 -*-
"""rag_qa_project 配置中心。

所有可调参数集中在此，通过环境变量覆盖（前缀 RAGQA_），
例如：RAGQA_TOP_K=6 python run.py "年假有几天？"
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_DIR = Path(__file__).resolve().parent

# 每个入口（run.py / ingest.py / uvicorn）都会 import config，所以 .env 在这里统一加载一次。
# pydantic-settings 的 env_file 只喂给 Settings 模型，**不会写进 os.environ**，
# 而 DashScopeEmbeddings / ChatDeepSeek 是用 os.getenv 取 key 的 ——
# 少了这一句，直接跑 uvicorn 时嵌入和 LLM 都会因为拿不到 key 而失败。
load_dotenv(PROJECT_DIR.parent.parent / ".env")   # 项目根 .env
# HF 镜像要赶在 huggingface_hub 被导入前设好，否则 ENDPOINT 会被固化成 huggingface.co
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")


class Settings(BaseSettings):
    """项目配置。默认值面向本地开发，生产环境用环境变量覆盖。"""

    model_config = SettingsConfigDict(
        env_prefix="RAGQA_",          # 环境变量前缀，避免与全局变量冲突
        env_file=str(PROJECT_DIR.parent.parent / ".env"),  # 项目根 .env
        extra="ignore",
    )

    # ---- LLM（DeepSeek）----
    deepseek_model: str = "deepseek-chat"
    temperature: float = 0.1          # RAG 场景用低温度，减少编造

    # ---- 嵌入（本地 bge，离线可用）----
    embed_model: str = "qwen3.7-text-embedding"
    hf_endpoint: str = "https://hf-mirror.com"   # 国内镜像

    # ---- 检索 ----
    top_k: int = 4                    # 每次检索返回的文档数
    chunk_size: int = 1000          # markdown 技术文档切分；原 200（中文短政策）会切碎语义、且导致嵌入调用暴增
    chunk_overlap: int = 150
    grade_preview_chars: int = 400  # 交给 grade 判官看的每段预览长度；原 120 只占 chunk 的 12%，信息太少导致误杀

    # ---- 工作流 ----
    max_rewrites: int = 2             # 查询重写最大次数（防死循环）
    grade_strict: bool = True         # True: 全部文档不相关才重写；False: 低于半数即重写

    # ---- 路径 ----
    data_dir: Path = PROJECT_DIR / "data"
    chroma_dir: Path = PROJECT_DIR / "data" / "chroma_db"
    collection_name: str = "langchain_docs"
    docs_dir: Path = data_dir / "langchain_docs"
    # 用户上传的原件落盘处：向量库是可重建的派生数据，ingest.py --force 重建时
    # 要靠这里的原件把用户文档捞回来，否则一次重建 = 用户资料永久丢失。
    uploads_dir: Path = data_dir / "uploads"


settings = Settings()
