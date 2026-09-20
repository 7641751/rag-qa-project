from datetime import UTC, datetime
from typing import TypedDict, Annotated

from langgraph.graph import add_messages
from pydantic import BaseModel, Field, field_serializer, field_validator

"""agent 相关的 Schema"""

BCRYPT_MAX_BYTES = 72
# ---------- 状态 ----------
class RAGState(TypedDict):
    question: str           # **用户原话，全程不被覆写**（改写只动 search_query）
    search_query: str       # 改写后的检索查询；未发生改写时**该键不存在**
    documents: list  # 整体覆盖语义：retrieve / grade 每次返回完整新列表
    generation: str
    rewrites: int # 已重写次数
    messages: Annotated[list, add_messages]  # 累积对话历史
    user_id: int | None    # 由 chat_service 从 token 注入；CLI / 测试可为 None

# ---------- 结构化输出 Schema ----------
# 注意：Field(description=...) 会进入发给模型的 JSON schema，等同于半条提示词，
# 所以判定标准 / 输出语言 / 长度约束都写在这里才最有效。
class GradeDocuments(BaseModel):
    """对 documents 列表逐一判断与 question 的相关性。"""
    relevance: list[bool] = Field(
        description="与候选文档**按编号顺序一一对应**的布尔值列表，长度必须等于文档条数。"
                    "True（判定从宽）= 能直接回答、仅部分相关、需与其他片段组合才能回答、"
                    "或含问题所涉及的关键概念/API/术语；False = 主题完全无关。"
                    "片段可能被截断，看不到全貌时倾向判 True")


class RewrittenQuery(BaseModel):
    """改写后的检索查询。"""
    query: str = Field(
        description="改写后的检索查询。知识库是 LangChain / LangGraph 的**英文**官方文档，"
                    "因此应输出**英文**查询（checkpointer、StateGraph 等专有名词保持原样），"
                    "并补上同义术语与上位概念以提高召回；查询需自包含（脱离对话也能看懂）；"
                    "只输出查询本身，不要解释、不要引号")

class ChatRequest(BaseModel):
    """聊天接口请求体"""
    question:str = Field(description="问题",min_length=1,max_length=14000) #必填，长度 14000
    thread_id:str = Field(description="线程ID",min_length=1) #必填

class HistoryMessage(BaseModel):
    role: str = Field(description="user 或 assistant")
    content: str
    sources: list | None = None
    grounded: bool | None = Field(
        default=None,
        description="仅 assistant 有意义。false = 该回答未命中知识库、基于模型通用知识，"
                    "刷新后前端仍应渲染警示标识；未持久化时为 null")

class HistoryResponse(BaseModel):
    thread_id: str
    messages: list[HistoryMessage]   # ✅ 普通 Pydantic 列表


class ChatDeleteResponse(BaseModel):
    """删除会话响应（契约 components.schemas.ChatDeleteResponse）。"""
    thread_id: str = Field(description="被删除的会话 ID")
    deleted: bool = Field(
        description="删除前该 thread 是否存在至少一个 checkpoint。"
                    "false = 本来就不存在（幂等，非错误）")


class Health(BaseModel):
    """系统健康检查响应（契约 components.schemas.Health）。"""
    status: str = Field(description="服务状态，正常为 ok")
    kb_count: int | None = Field(default=None, description="知识库向量条数；读不到则省略")
    model: str | None = Field(default=None, description="当前嵌入模型名")

class Auth(BaseModel):
    """注册 / 登录请求体（契约 components.schemas.RegisterRequest 与 LoginRequest 同形）。"""
    username: str = Field(
        description="用户名", min_length=3, max_length=32, pattern="^[A-Za-z0-9_]+$")
    password: str = Field(description="密码", min_length=8)

    @field_validator("password")
    @classmethod
    def _password_within_bcrypt_limit(cls, v: str) -> str:
        """schema 层拦超长密码：让 422 由 pydantic 产生、错误定位到 password 字段本身。

        第二层防护在 security.hash_password（同样 72 字节 → 422 VALIDATION_ERROR），
        **两层都保留**：本模型只管 HTTP 请求体，而 hash_password 还会被
        改密码 / 重置密码等不经过本模型的路径调用，那时没有 schema 兜底。
        """
        n = len(v.encode("utf-8"))
        if n > BCRYPT_MAX_BYTES:
            raise ValueError(
                f"密码 UTF-8 编码后不能超过 {BCRYPT_MAX_BYTES} 字节（当前 {n} 字节）")
        return v


class AuthUser(BaseModel):
    """鉴权响应里的用户信息（契约 components.schemas.AuthUser）。"""
    id: int = Field(description="用户 ID")
    username: str = Field(description="用户名")


# ---------- 会话列表（P3） ----------
# title 的真实上限来自 DB 列宽（models.Conversation.title 是 String(60)），
# 这里复述一份用于 422 的前置拦截。改列宽时两处都要改。
RENAME_MAX_CHARS = 60


class ConversationSummary(BaseModel):
    """会话列表项（契约 components.schemas.ConversationSummary）。"""
    thread_id: str = Field(description="会话 ID")
    title: str = Field(description="标题：首问前 20 字，之后可由用户手动重命名")
    created_at: datetime = Field(description="创建时间")
    updated_at: datetime = Field(description="最后一次提问时间；列表按它倒序")

    @field_serializer("created_at", "updated_at")
    def _iso8601_utc_z(self, v: datetime) -> str:
        """序列化成**带 Z** 的 ISO 8601。

        库里存的是 naive UTC（MySQL DATETIME 不带时区，写入值是 datetime.now(UTC)），
        Pydantic 默认会输出 `2026-09-19T06:00:00` —— **没有 Z**。而按 ECMAScript 规范，
        JS 的 `new Date('2026-09-19T06:00:00')` 把这种形式解析成**本地时间**，于是
        UTC 时间戳被当成本地时间，前端的相对时间整体偏掉一个时区（UTC+8 下
        「刚刚」会显示成「8 小时前」）。契约样例与前端 mock 都带 Z，这里补齐。
        """
        if v.tzinfo is None:
            v = v.replace(tzinfo=UTC)
        return v.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class ThreadListResponse(BaseModel):
    """GET /api/chat/threads 响应（契约 components.schemas.ThreadListResponse）。"""
    threads: list[ConversationSummary]
    total: int = Field(
        description="本人会话的**真实总数**。threads 最多 50 条，total 更大即被截断")


class RenameRequest(BaseModel):
    """PATCH /api/chat/threads/{thread_id} 请求体（契约 components.schemas.RenameRequest）。"""
    title: str = Field(description="新标题", min_length=1, max_length=RENAME_MAX_CHARS)

    @field_validator("title")
    @classmethod
    def _strip_then_require_non_empty(cls, v: str) -> str:
        """去空白后判空，并**返回去空白后的值**。

        `min_length=1` 只挡得住空串：`"   "` 能通过校验，但它存进去就是一个
        「看起来是空的」标题。返回 stripped 值而不是原值，否则库里留下 `"  x  "`，
        列表里看着像有缩进。
        """
        s = v.strip()
        if not s:
            raise ValueError("标题不能为空")
        return s


class RenameResponse(BaseModel):
    """PATCH /api/chat/threads/{thread_id} 响应（契约 components.schemas.RenameResponse）。"""
    thread_id: str
    title: str


class LoginResponse(BaseModel):
    """登录成功响应（契约 components.schemas.LoginResponse）。"""
    access_token: str = Field(description="JWT")
    token_type: str = Field(default="bearer", description="固定 bearer（OAuth2 惯例）")
    user: AuthUser = Field(description="登录用户")