# -*- coding: utf-8 -*-
"""rag_qa_project 配置中心。

所有可调参数集中在此，通过环境变量覆盖（前缀 RAGQA_），
例如：RAGQA_TOP_K=6 python run.py "年假有几天？"
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_DIR = Path(__file__).resolve().parent


# 每个入口（run.py / ingest.py / uvicorn）都会 import config，所以 .env 在这里统一加载一次。
# pydantic-settings 的 env_file 只喂给 Settings 模型，**不会写进 os.environ**，
# 而 DashScopeEmbeddings / ChatDeepSeek 是用 os.getenv 取 key 的 ——
# 少了这一句，直接跑 uvicorn 时嵌入和 LLM 都会因为拿不到 key 而失败。
def _find_env_file() -> Path | None:
    """定位 .env：**由外向内**逐级回退，找到第一个存在的就用。

    为什么是「由外向内」而不是「就近优先」：monorepo 布局下同时存在两个用途不同的 .env ——
      · `<monorepo 根>/.env`（两级之上）      本地开发用，含 RAGQA_MYSQL_DATABASE_URL / REDIS_URL
      · `PROJECT_DIR/.env`（本项目根，就地）  docker compose 用，只有 MYSQL_PASSWORD / REDIS_PASSWORD
    就近优先会让本地开发读到 docker 那份 —— 少了 mysql_database_url，启动期直接快速失败。
    所以**外层的开发配置**优先级更高。

    为什么必须逐级回退而不是写死一个：本项目已从 monorepo 抽成独立仓库，
    此时 PROJECT_DIR 就是仓库根、`parent.parent` 会指到仓库外面去。
    四个候选覆盖了全部已知布局：
      ① RAGQA_ENV_FILE 显式指定（最高优先级，逃生舱）
      ② PROJECT_DIR.parent.parent/.env   monorepo 布局（外层的开发配置）
      ③ PROJECT_DIR.parent/.env          中间层
      ④ PROJECT_DIR/.env                 独立仓库根 / docker 镜像内（PROJECT_DIR = /app）
    找不到就返回 None —— 此时全靠进程环境变量（docker compose 就是这种注入方式）。
    """
    explicit = os.environ.get("RAGQA_ENV_FILE", "").strip()
    if explicit:
        return Path(explicit)
    for candidate in (PROJECT_DIR.parent.parent / ".env",
                      PROJECT_DIR.parent / ".env",
                      PROJECT_DIR / ".env"):
        if candidate.is_file():
            return candidate
    return None


ENV_FILE = _find_env_file()
if ENV_FILE is not None:
    load_dotenv(ENV_FILE)
# HF 镜像要赶在 huggingface_hub 被导入前设好，否则 ENDPOINT 会被固化成 huggingface.co
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")


class Settings(BaseSettings):
    """项目配置。默认值面向本地开发，生产环境用环境变量覆盖。"""

    model_config = SettingsConfigDict(
        env_prefix="RAGQA_",          # 环境变量前缀，避免与全局变量冲突
        env_file=str(ENV_FILE) if ENV_FILE else None,  # 与 load_dotenv 同一份，见 _find_env_file
        extra="ignore",
    )

    @model_validator(mode="after")
    def _require_jwt_secret(self):
        secret = (self.jwt_secret or "").strip()
        if not secret:
            raise ValueError(
                f"RAGQA_JWT_SECRET 未设置。请在 .env 里加一行 "
                f"RAGQA_JWT_SECRET=<openssl rand -hex 32 的输出>"
                f"（当前加载的 .env：{ENV_FILE or '未找到 —— 请用 RAGQA_ENV_FILE 显式指定'}）")
        if len(secret.encode("utf-8")) < 32:
            # HS256 的 HMAC 密钥要 ≥ 32 字节，否则 PyJWT 会在每次 encode/decode
            # 发 InsecureKeyLengthWarning（RFC 7518 §3.2）。在启动期拦掉更省事。
            raise ValueError(
                "RAGQA_JWT_SECRET 太短：HS256 要求 ≥ 32 字节（RFC 7518 §3.2）。"
                "请用 openssl rand -hex 32 生成（64 个字符）")
        return self

    # ---- LLM（DeepSeek）----
    deepseek_model: str = "deepseek-chat"
    temperature: float = 0.1          # RAG 场景用低温度，减少编造

    # ---- 嵌入（本地 bge，离线可用）----
    embed_model: str = "qwen3.7-text-embedding"
    hf_endpoint: str = "https://hf-mirror.com"   # 国内镜像

    # ---- 重排 ----
    rerank_model: str = "qwen3.7-text-rerank"

    # ---- 检索 ----
    top_k: int = 4                    # 每次检索返回的文档数
    chunk_size: int = 1000          # markdown 技术文档切分；原 200（中文短政策）会切碎语义、且导致嵌入调用暴增
    chunk_overlap: int = 150
    grade_preview_chars: int = 400  # 交给 grade 判官看的每段预览长度；原 120 只占 chunk 的 12%，信息太少导致误杀

    # ---- 工作流 ----
    max_rewrites: int = 2             # 查询重写最大次数（防死循环）
    # 改写的触发门槛：
    #   False（默认）保留数**不到召回数一半**就改写 —— 纠错回路才真正起作用
    #   True         只有「一条都没留下」才改写（改动前的行为）
    # 为什么默认 False：grade 的判定标准是「宁可多留、不可误删」，叠加 True 之后
    #   改写几乎永不触发，中文问英文库这类「召回质量差但非零」的场景完全无法自我纠正。
    # 代价：改写会多花 LLM 调用（每轮改写 + 重新评分，最多 2×(1+1) 次）。
    # 退回旧行为：设环境变量 RAGQA_GRADE_STRICT=true
    grade_strict: bool = False

    # ---- 路径 ----
    data_dir: Path = PROJECT_DIR / "data"
    chroma_dir: Path = PROJECT_DIR / "data" / "chroma_db"
    collection_name: str = "langchain_docs"
    docs_dir: Path = data_dir / "langchain_docs"
    # 用户上传的原件落盘处：向量库是可重建的派生数据，ingest.py --force 重建时
    # 要靠这里的原件把用户文档捞回来，否则一次重建 = 用户资料永久丢失。
    uploads_dir: Path = data_dir / "uploads"
    checkpoint_db: Path = data_dir / "checkpoints.db"

    # ---- 数据库 ----
    mysql_database_url: str = ""  # 由 .env 的 RAGQA_MYSQL_DATABASE_URL 注入
    sql_echo: bool = False

    # ---- Redis ----
    # P4 起：会话列表读缓存用它（backend/tools/redis_cache_tools.py + api/deps.py 的
    # get_optional_redis），tests/test_redis.py 的连通性自检也读它。
    # ⚠ 用 validation_alias 而不是只靠 env_prefix，是因为两种写法都要认：
    #   · .env 里现用的是 12-factor 约定的裸 `REDIS_URL`（docker-compose / PaaS 也注入这个名字）；
    #   · 项目约定是 `RAGQA_` 前缀。
    #   带前缀的那个必须**显式列出来**：一旦设置 validation_alias，pydantic-settings 就
    #   不再自动补 env_prefix，只写 "REDIS_URL" 会让 RAGQA_REDIS_URL 静默失效。
    #   顺序即优先级：项目前缀写法 > 裸名。
    redis_url: str = Field("", validation_alias=AliasChoices("RAGQA_REDIS_URL", "REDIS_URL"))

    # ---- Redis 读缓存（P4）----
    # ⚠ 三个都必须有默认值：Redis 是**可选依赖**，缺它不该拦启动（与 mysql_database_url
    #   的「空串即快速失败」不同 —— 那条是必需依赖，这条不是）。
    redis_cache_enabled: bool = True   # 一键回退到「无缓存」行为（RAGQA_REDIS_CACHE_ENABLED）
    conv_cache_ttl: int = 60           # 列表缓存 TTL 秒；只作兜底（主策略是写时删键）
    qa_stream_key: str = "ragqa:stream:qa_stats"   # 问答统计事件流

    # ---- 鉴权 ----
    jwt_secret: str = ""  # 必填；为空则启动即失败（见下）
    jwt_algorithm: str = "HS256"
    jwt_expire_days: int = 7

settings = Settings()
