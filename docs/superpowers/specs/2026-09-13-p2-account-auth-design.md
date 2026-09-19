# 账号与鉴权（P2）设计文档

- **日期**：2026-09-13
- **状态**：设计已确认，待实现
- **阶段**：P2（三阶段规划的第二阶段）
- **前置**：P1 后端已实现（`2026-09-13-delete-conversation-design.md`：**AsyncSqliteSaver** 持久化 + `DELETE /api/chat/threads/{thread_id}`；2026-09-16 修订：checkpointer 由同步 SqliteSaver 改为 AsyncSqliteSaver）；前端待接入
- **分工**：后端由用户实现；接口契约与前端由 AI 产出
- **关联文档**：`2026-09-09-langchain-qa-web-design.md`、`2026-09-11-kb-upload-design.md`、`2026-09-13-delete-conversation-design.md`、`2026-09-13-p3-conversation-list-kb-isolation-design.md`

---

## 1. 背景与问题

| # | 现状 | 证据 |
|---|---|---|
| 1 | **所有端点零鉴权**：`GET /api/chat/history?thread_id=X` 任何人猜到 uuid 即可读他人会话；P1 新增的 `DELETE /api/chat/threads/{X}` 同理可删他人会话 | `backend/app/api/chat_router.py:10,21`；P1 spec §10「越权风险」 |
| 2 | 没有任何 user 概念：`thread_id` 由前端 `crypto.randomUUID()` 生成后直接进 checkpointer，**无处记录归属** | `frontend/src/hooks/useThreadId.ts:6-14`、`backend/app/agent/graph.py:222` |
| 3 | 后端零 auth 代码：`password` / `jwt` / `login` / `current_user` 在 `backend/**/*.py` 中 grep **0 命中** | 实测 grep |
| 4 | **422 错误体违背契约**：`main.py` 未注册 `RequestValidationError` 处理器，实际返回 FastAPI 默认的 `{"detail":[...]}`，而契约要求 `{code,message}` | `backend/app/api/main.py`（全文无 exception_handler）vs `docs/api/README.md` 错误码表 |
| 5 | 依赖已就绪但未使用：`sqlalchemy[asyncio] 2.0.52`、`aiomysql`、`asyncpg`、`bcrypt 5.0.0`、`pyjwt` 均在 lock 中，其中 **`bcrypt` 与 `pyjwt` 是传递依赖**（`pyjwt` 来自 `mcp`），未显式声明 | `pyproject.toml:6-36`、`uv.lock:1733,2715` |

## 2. 目标与非目标

### 目标
1. 用户可注册、登录，前端持 JWT 访问
2. chat 三端点（`stream` / `history` / `threads/{id}` DELETE）**全部要求登录**并做**归属校验**
3. KB 三端点要求登录，但**本期不做归属隔离**（P3 处理）
4. 建立 `users` 与 `conversations` 两张业务表，`conversations` 记录 `thread_id → user_id` 映射（归属校验的唯一依据）
5. 顺带修掉 422 错误体的契约违背

### 非目标（YAGNI，全部记入 §13 技术债）
- refresh token、token 续期、服务端 token 吊销
- `POST /api/auth/logout` 端点（JWT 无状态，前端删 token 即可）
- 邮箱验证、找回密码、第三方登录（OAuth）
- 角色与权限（RBAC）、管理员后台
- 登录限流 / 失败锁定 / 验证码
- Alembic 迁移（用启动时 `create_all`）
- 审计日志
- KB 按用户隔离、会话列表 UI（→ P3）
- CSRF 防护（Bearer token 方案下不需要；若将来改 httpOnly cookie 则必须加）

## 3. 决策记录

| # | 决策 | 选择 | 被否决的方案与理由 |
|---|---|---|---|
| 1 | 凭证载体 | **JWT Bearer + localStorage** | ✗ httpOnly cookie：防 XSS 更好，但要加 CSRF 防护，且 `client.ts` 6 处 fetch 都要加 `credentials:'include'`；CORS 已开 `allow_credentials=True`（`main.py:25`）故技术可行，留作升级路径<br>✗ 服务端 Session：可主动吊销，但要建 sessions 表 + 过期清理，最重 |
| 2 | 业务库 | **MySQL（本地已有 8.x 实例）+ 测试用 SQLite 内存库** | ✗ 全 SQLite：与「想用 MySQL」的意愿不符<br>✗ 测试也连 MySQL：pytest 需数据库在线、每用例建/清表，慢且脆 |
| 3 | 注册方式 | **用户名 + 密码，开放注册，无邮箱验证** | ✗ 邮箱注册：无法验证真实性（不发验证邮件），字段更多收益为零<br>✗ 内置单账号：测不到多用户隔离，「账号功能」名不副实 |
| 4 | KB 过渡 | **P2 只要求登录，不做归属隔离** | ✗ P2 同期隔离：体量翻倍（metadata、检索过滤、迁移脚本），等于把 P3 一半提前做<br>✗ KB 完全开放：未登录者也能上传/删除，越权窗口最大 |
| 5 | `conversations` 表建在哪一期 | **P2** | ✗ 留到 P3：**归属校验需要 `thread_id → user_id` 映射**，没有这张表 P2 的 403 无从判断。这是本期最重要的边界修正 |
| 6 | `title` 何时写入 | **P2 首次提问 upsert 时写**（首问前 20 字） | ✗ P3 再加：需要回填历史行；而 P2 的 upsert 现场正好握着 `question`，零额外成本 |
| 7 | 密码哈希 | **直接用已装的 `bcrypt 5.0.0`** | ✗ passlib：未安装且已停止维护，其对 bcrypt 4.x+ 的版本探测有已知告警 |
| 8 | 密码长度上限 | **>72 字节返回 422，不静默截断** | ✗ 静默截断：bcrypt 只取前 72 字节，会导致「前 72 字节相同的两个密码等价」这种诡异漏洞 |
| 9 | 建表方式 | **启动时 `Base.metadata.create_all`** | ✗ Alembic：学习项目引入迁移框架收益低于成本；记为技术债（改字段需手工 ALTER） |
| 10 | 前端路由 | **不引 react-router，App 条件渲染** | ✗ react-router：只有「登录页 / 主应用」两个视图，新增依赖不划算 |
| 11 | 归属不匹配的状态码 | **403 FORBIDDEN** | ✗ 404（不泄露存在性）：`thread_id` 是不可枚举的 uuid，泄露风险可忽略，而 403 在调试期信息量更大 |

## 4. 架构与数据流

```mermaid
sequenceDiagram
    participant U as 浏览器
    participant A as useAuth
    participant C as client.ts
    participant BE as FastAPI
    participant DB as MySQL
    participant CP as AsyncSqliteSaver
    U->>A: 输入用户名密码
    A->>C: POST /api/auth/login
    C->>BE: login
    BE->>DB: SELECT users WHERE username
    BE->>BE: bcrypt.checkpw
    BE->>BE: jwt.encode(sub=user_id, exp=7d)
    BE-->>C: 200 {access_token, user}
    C->>C: localStorage['ragqa_token'] = token
    Note over U,C: 之后每个请求都带 Authorization: Bearer
    U->>C: POST /api/chat/stream
    C->>BE: + Authorization 头
    BE->>BE: get_current_user 解 JWT
    BE->>DB: UPSERT conversations(thread_id,user_id,title)
    alt thread 已属于他人
        BE-->>C: 403 FORBIDDEN（流开始前）
    else 归属正确
        BE->>CP: astream(thread_id)
        BE-->>C: 200 SSE 事件流
    end
```

**两个数据源的职责边界**

| 数据源 | 存什么 | 谁写 |
|---|---|---|
| **MySQL**（`users` / `conversations`） | 账号、会话归属与标题、时间戳 | 业务代码（SQLAlchemy async） |
| **SQLite**（`data/checkpoints.db`，P1 建） | LangGraph 图状态与消息正文 | LangGraph checkpointer |
| **Chroma**（`data/chroma_db`） | 向量与文档 metadata | `ingest.py` / KB 上传链路 |

**消息正文不进 MySQL**：`conversations` 只是索引与归属凭证，正文的唯一真相源仍是 checkpointer（延续既有约定，避免两份状态漂移）。

## 5. 数据模型

```sql
CREATE TABLE users (
  id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  username      VARCHAR(32)     NOT NULL,
  password_hash VARCHAR(60)     NOT NULL,   -- bcrypt 输出固定 60 字符
  created_at    DATETIME(6)     NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_users_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE conversations (
  thread_id  VARCHAR(36)     NOT NULL,      -- 即 LangGraph thread_id（uuid）
  user_id    BIGINT UNSIGNED NOT NULL,
  title      VARCHAR(60)     NOT NULL,      -- 首问前 20 字；P3 可重命名
  created_at DATETIME(6)     NOT NULL,
  updated_at DATETIME(6)     NOT NULL,
  PRIMARY KEY (thread_id),
  KEY ix_conversations_user_updated (user_id, updated_at DESC),  -- P3 列表查询靠它
  CONSTRAINT fk_conversations_user FOREIGN KEY (user_id)
    REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
```

**注意**：`utf8mb4_0900_ai_ci` 是大小写与重音不敏感的排序规则，所以 `Alice` 与 `alice` 会撞唯一键——这是想要的行为（避免同名混淆），但注册报错文案要说清「用户名已存在」而不是「格式错误」。

**`ON DELETE CASCADE` 的局限**：删 user 会级联删 `conversations` 行，但**不会**删 checkpointer 里的 thread 数据。本期不提供删用户功能；将来若要做，必须先遍历该用户所有 `thread_id` 调 `delete_thread`，再删 user 行。

## 6. 接口契约

### 6.1 新增端点

| 方法 | 路径 | 请求体 | 成功 | 失败 |
|---|---|---|---|---|
| POST | `/api/auth/register` | `{username, password}` | `201 {id, username}` | `409 USERNAME_TAKEN`、`422 VALIDATION_ERROR` |
| POST | `/api/auth/login` | `{username, password}` | `200 {access_token, token_type:"bearer", user:{id,username}}` | `401 INVALID_CREDENTIALS`、`422 VALIDATION_ERROR` |
| GET | `/api/auth/me` | — | `200 {id, username}` | `401 UNAUTHORIZED` |

新增 tag：`auth`（`openapi.yaml` 的 `tags` 段现有 chat/kb/system 三个）。

### 6.2 字段定义

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `username` | string | 3–32 位，`^[A-Za-z0-9_]+$` | 大小写不敏感唯一（由 DB 排序规则保证） |
| `password` | string | 最小 8 字符，**UTF-8 编码后 ≤ 72 字节** | 不做复杂度校验；超 72 字节返回 422（bcrypt 硬上限） |
| `access_token` | string | JWT HS256 | payload：`{sub: "<user_id>", username, exp, iat}` |
| `token_type` | string | 固定 `"bearer"` | 与 OAuth2 惯例一致 |

### 6.3 既有端点的改造（**请求/响应字段全部不变**）

| 端点 | 改造 |
|---|---|
| `POST /api/chat/stream` | 要求登录。取 `user_id` 后 **upsert** `conversations`：新行则 `title = question[:20]`、`created_at = updated_at = now`；已有行则只更新 `updated_at`。若该 `thread_id` 已存在且 `user_id` 不是当前用户 → **在返回 StreamingResponse 之前**用 `403 FORBIDDEN` 拒绝（不是流内 `error` 事件，因为响应头一旦发出就改不了状态码） |
| `GET /api/chat/history` | 要求登录。查 `conversations`：**无行 → `200 {"messages": []}`**（保持既有约定，前端新会话依赖它）；有行但 `user_id` 非本人 → `403 FORBIDDEN` |
| `DELETE /api/chat/threads/{thread_id}` | 要求登录 + 归属校验（非本人 → `403`）。**P1 的幂等语义保留**：`conversations` 无行 → `200 {deleted:false}`。删除顺序见 §8.6 |
| `POST /api/kb/documents` | 要求登录，**不做归属隔离**；本期 metadata 仍不写 `user_id`（P3 才写） |
| `GET /api/kb/documents` | 要求登录，返回全部上传件（P3 收窄） |
| `DELETE /api/kb/documents/{doc_id}` | 要求登录，任何登录用户都能删任何上传件（**本期已知越权，P3 修复**） |
| `GET /api/health` | **保持公开**（探活不该要求登录；`kb_count` 不泄露隐私） |

### 6.4 错误码

**新增 4 个**（`ErrorCode` 从 10 个增至 **14 个**）：

| code | HTTP | 触发 |
|---|---|---|
| `UNAUTHORIZED` | 401 | 缺 `Authorization` 头、token 格式错、token 过期或签名无效 |
| `INVALID_CREDENTIALS` | 401 | 用户名或密码错（**故意不区分**，避免用户名枚举） |
| `FORBIDDEN` | 403 | 已登录但访问不属于自己的 thread |
| `USERNAME_TAKEN` | 409 | 注册时用户名已存在 |

统一错误体仍是 `{"code", "message"}`。

### 6.5 curl 样例

```bash
# 注册
curl -i -X POST http://localhost:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"hao","password":"abcd1234"}'
# HTTP/1.1 201 Created
# {"id":1,"username":"hao"}

# 登录
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"hao","password":"abcd1234"}' \
  | node -pe "JSON.parse(require('fs').readFileSync(0)).access_token")

# 带 token 提问
curl -N -X POST http://localhost:8000/api/chat/stream \
  -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d '{"question":"checkpointer 怎么删会话","thread_id":"2f1c9a04-6b7e-4d21-9c33-1ab2cd34ef56"}'

# 不带 token → 401
curl -i http://localhost:8000/api/chat/history?thread_id=2f1c9a04-6b7e-4d21-9c33-1ab2cd34ef56
# HTTP/1.1 401 Unauthorized
# {"code":"UNAUTHORIZED","message":"缺少或无效的 Authorization 头"}

# 访问他人 thread → 403（用第二个账号的 token）
curl -i http://localhost:8000/api/chat/history?thread_id=2f1c9a04-6b7e-4d21-9c33-1ab2cd34ef56 \
  -H "Authorization: Bearer $TOKEN_OTHER"
# HTTP/1.1 403 Forbidden
# {"code":"FORBIDDEN","message":"该会话不属于当前用户"}

# 422 现在是契约格式（本期修好的）
curl -i -X POST http://localhost:8000/api/auth/register \
  -H "Content-Type: application/json" -d '{"username":"ab","password":"x"}'
# HTTP/1.1 422 Unprocessable Entity
# {"code":"VALIDATION_ERROR","message":"username: String should have at least 3 characters"}
```

### 6.6 契约文件改动位置

| 文件 | 位置 | 改动 |
|---|---|---|
| `docs/api/openapi.yaml` | `tags`（当前 11-17 行） | 新增 `- name: auth` |
| `docs/api/openapi.yaml` | `paths` 开头（当前 18 行之后） | 新增 `/api/auth/register`、`/api/auth/login`、`/api/auth/me` |
| `docs/api/openapi.yaml` | `paths` 的 chat 三端点 | 各加 `security: [{bearerAuth: []}]` 与 `401`/`403` 响应 |
| `docs/api/openapi.yaml` | `components` | 新增 `securitySchemes.bearerAuth`（`type: http, scheme: bearer, bearerFormat: JWT`）、schemas `AuthUser`/`LoginRequest`/`LoginResponse`/`RegisterRequest`、responses `Unauthorized`/`Forbidden`/`UsernameTaken` |
| `docs/api/openapi.yaml` | `ErrorEvent.code` 枚举（当前 179 行） | 追加 4 个新码 |
| `docs/api/README.md` | 端点表 | 新增 3 行 auth 端点；chat/kb 行标注「需登录」 |
| `docs/api/README.md` | 错误码表 | 新增 4 行 |
| `docs/api/README.md` | 新增「鉴权」章节 | Bearer 用法、token 有效期、401 与 403 的区别、`/api/health` 保持公开、KB 本期不隔离的已知越权 |

## 7. 后端实现要求（交接给用户）

### 7.1 依赖

```bash
uv add pyjwt bcrypt          # 两者目前都是传递依赖，必须显式声明
```

`requirements.txt` 同步加 `pyjwt`、`bcrypt`（`sqlalchemy[asyncio]`、`aiomysql`、`aiosqlite` 已在）。

### 7.2 配置（`config.py`）

在 `Settings` 里新增（沿用 `RAGQA_` 前缀，故环境变量为 `RAGQA_MYSQL_DATABASE_URL` / `RAGQA_JWT_SECRET`）：

> ✅ **连接串部分已实现**（2026-09-16）。差异与理由见 **§7.9**，那里是本 spec 的唯一权威口径。
> `config.py` 的「---- 数据库 ----」段已是下方形态。

```python
    # ---- 业务数据库（users / conversations）----
    # ⚠ 绝不把真实连接串写进代码：仓库里已有一处教训
    #   （fastapi_tutorial/fastapi_06_SQLAlchemy ORM.ipynb:117 硬编码了 MySQL root 密码）。
    # 默认空串 = 未配置，由 mysql_db_tools._mysql_url() 在构造引擎**之前**快速失败并点名环境变量。
    # 刻意不用 "CHANGE_ME" 之类占位值：占位符会被当真去连，变成
    # 「看起来配好了但连不上」这种更差的失败模式。
    mysql_database_url: str = ""
    sql_echo: bool = False        # SQL 全量日志开关，默认关（SSE 场景下会淹没应用日志）

    # ---- 鉴权 ----
    jwt_secret: str = ""          # 必填；为空则启动即失败（见下）
    jwt_algorithm: str = "HS256"
    jwt_expire_days: int = 7
```

**`jwt_secret` 为空必须启动失败**，不能退化成默认值——否则任何人都能伪造 token：

```python
    @model_validator(mode="after")
    def _require_jwt_secret(self):
        if not self.jwt_secret:
            raise RuntimeError(
                "RAGQA_JWT_SECRET 未设置。请在项目根 .env 里加一行 "
                "RAGQA_JWT_SECRET=<openssl rand -hex 32 的输出>")
        return self
```

同时新增 **`.env.example`**（`.gitignore:9` 已有 `!.env.example` 白名单，可安全提交）：

```
RAGQA_MYSQL_DATABASE_URL=mysql+aiomysql://<user>:<pwd>@localhost:3306/rag_qa?charset=utf8mb4
RAGQA_JWT_SECRET=用 openssl rand -hex 32 生成，不要用示例值
```

> ⚠ **该文件尚未创建**（2026-09-16 核对）：`advanced_tutorial/.gitignore:10` 的 `!.env.example`
> 白名单已就位，但模板本身还没有。另注意 `.env` 实际位于 `my_langchain_demo/`
> （git 仓库根 `advanced_tutorial/` 的**上一级**），模板若放进仓库内，`config.py` 的
> `PROJECT_DIR.parent.parent` 就读不到它 —— 放置位置需先确认，见 §7.9。

### 7.3 新增文件

| 文件 | 职责 |
|---|---|
| `backend/tools/mysql_db_tools.py` ✅**已建** | `get_db_engine()` / `get_db_session_maker()`（**同步** `@lru_cache(maxsize=1)` 单例）/ `get_db_session()` 依赖 / `init_db()`（`create_all`）/ `aclose_db()`。〈初稿此处为 `backend/app/db.py`，含 `engine`/`Base`/`close_db()`/`get_db()`，差异见 §7.9〉 |
| `backend/app/models.py` ✅**已建**（当前含 `Base` + `User`） | SQLAlchemy ORM：`User`、`Conversation`（**`Conversation` 尚未建**）。`Base` 放这里而不是 db 基础设施模块里，避免 db ⇄ models 循环引用 |
| `backend/tools/security.py` ✅**已建** | `hash_password` / `verify_password`（bcrypt）/ `create_access_token` / `decode_token`（PyJWT HS256）。〈初稿此处为 `backend/app/security.py`，差异见 §7.9〉 |
| `backend/tools/http_tools.py` ✅**已建** | 契约格式错误响应原语：`err()`（路由内 return）/ `api_error()`（依赖注入与 service 层 raise）。〈初稿计划「放 `function_tools.py` 与 `err()` 并列」，实际抽成独立模块，差异见 §7.9〉 |
| `backend/app/api/deps.py` | `HTTPBearer(auto_error=False)` + `get_current_user` 依赖 |
| `backend/app/api/auth_router.py` | `register` / `login` / `me` 三个端点 |
| `backend/app/services/auth_service.py` | 注册与登录的业务逻辑（查重、校验、发 token） |
| `backend/app/services/conversation_service.py` | `upsert_conversation` / `assert_owner` / `delete_conversation_row` |
| `backend/app/agent/schemas.py`（改） | 新增 `RegisterRequest`/`LoginRequest`/`AuthUser`/`LoginResponse`；`ErrorCode` 相关的枚举文案 |

Pydantic 模型放在既有的 `agent/schemas.py`（跟随现有约定：`ChatRequest`/`HistoryMessage`/`Health` 都在那里）。该文件正在变大，**记为技术债**：将来可拆成 `schemas/chat.py`、`schemas/auth.py`、`schemas/kb.py`。

### 7.4 五个必须遵守的约束

1. **`HTTPBearer(auto_error=False)`**：默认 `auto_error=True` 会自己抛 403 且返回 `{"detail":...}`，与契约的 `{code,message}` 冲突。设成 `False` 后自己判断 `creds is None` 并抛契约格式的错误。

2. **统一异常处理器**（`main.py`）——依赖注入里没法 `return JSONResponse`，只能抛异常，所以必须有两个 handler 把异常翻译成契约格式：

```python
from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

@app.exception_handler(HTTPException)
async def _http_exception_handler(_: Request, exc: HTTPException):
    """detail 传 dict 时原样吐出（契约要 {code,message}），否则包成 INTERNAL_ERROR。"""
    d = exc.detail
    body = d if isinstance(d, dict) and "code" in d else {"code": "INTERNAL_ERROR", "message": str(d)}
    return JSONResponse(status_code=exc.status_code, content=body)

@app.exception_handler(RequestValidationError)
async def _validation_handler(_: Request, exc: RequestValidationError):
    """修掉既有契约违背：422 之前返回 FastAPI 默认的 {"detail":[...]}。"""
    first = (exc.errors() or [{}])[0]
    loc = ".".join(str(x) for x in first.get("loc", []) if x != "body")
    return JSONResponse(status_code=422, content={
        "code": "VALIDATION_ERROR",
        "message": f"{loc}: {first.get('msg', '校验失败')}" if loc else first.get("msg", "校验失败")})
```

配套一个抛错辅助 ✅**已建**：`backend/tools/http_tools.py` 的 `api_error()`，与 `err()` 同处一个模块。
〈初稿写的是「放 `function_tools.py`，与既有 `err()` 并列」，实际抽成了独立模块，差异与理由见 §7.9〉

```python
def api_error(status: int, code: str, message: str) -> NoReturn:
    """在依赖注入 / service 层用（那里不能 return JSONResponse）；
    路由函数内部仍可用既有的 err() 直接返回。"""
    raise HTTPException(status_code=status, detail={"code": code, "message": message})
```

> `-> NoReturn` 是**调用方的必需品**，不是装饰。`hash_password` 那种「校验失败就抛、否则 return」
> 的写法，缺了它类型检查器会在每个调用点误报 `Missing return statement`；运行期行为不变。

3. **密码 72 字节上限要主动校验**，不能依赖 bcrypt 静默截断：

```python
def hash_password(pw: str) -> str:
    raw = pw.encode("utf-8")
    if len(raw) > 72:
        api_error(422, "VALIDATION_ERROR", "密码不能超过 72 字节")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("ascii")
```

4. **`lifespan` 管理引擎**（`main.py` 目前没有 lifespan）：

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()      # create_all（含空 metadata 守卫）+ 连通性检查
    try:
        yield
    finally:
        await aclose_db()            # 引擎 dispose + **成对**清空两个 lru_cache
        await aclose_checkpointer()  # 既有收尾，别漏（main.py 现在只有这一句）

app = FastAPI(..., lifespan=lifespan)
```

`init_db` 失败要让进程**起不来**（连不上 MySQL 却静默继续，只会在第一个请求时炸出难懂的 500）。

⚠ **与现状的差异（2026-09-16）**：`backend/app/api/main.py` 目前**已有** lifespan，但里面只有收尾一句
`await aclose_checkpointer()`，既没有 `init_db` 也没有 `aclose_db`（`mysql_db_tools` 与
`get_db_session` 目前尚无任何运行时调用方）。接线时是**在既有 lifespan 上补三行**，
不要照抄上面那段重写、更不要丢掉 `aclose_checkpointer()`。

5. **测试用 SQLite 内存库必须配 `StaticPool`**——这是最容易踩的坑：`sqlite+aiosqlite:///:memory:` 每个连接都是**独立的空库**，默认连接池下建完表换个连接就没了。
   （例外：若用例要用 `engine.pool.checkedout()` 断言「连接已归还池子」，必须改用
   `AsyncAdaptedQueuePool` —— `checkedout()` 只定义在 `QueuePool` 上，`StaticPool` 没有。
   两种池的分工见 `tests/test_mysql_db.py` 的模块 docstring。）

```python
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

test_engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    poolclass=StaticPool,                          # 全测试共用同一个连接
    connect_args={"check_same_thread": False},
)
```

### 7.5 归属校验的实现要点

```python
async def assert_owner(db, thread_id: str, user_id: int) -> None:
    """无行 = 新会话（放行，保持 history 返回 200 空数组的既有约定）；
    有行且不是本人 = 403。"""
    row = await db.get(Conversation, thread_id)
    if row is not None and row.user_id != user_id:
        api_error(403, "FORBIDDEN", "该会话不属于当前用户")
```

**为什么「无行」要放行**：前端 `useThreadId` 在本地生成 uuid 后立刻可能调 `history`（`useChat.ts:66-82`），此时后端还没有这一行。若返回 403，新会话永远打不开。

### 7.6 删除会话的顺序（两个数据源）

```
1) assert_owner(...)                             # 先鉴权
2) await checkpointer.adelete_thread(thread_id)  # 先删大头（不可逆）。必须用异步版：
                                                 #   AsyncSqliteSaver 的同步桥接在事件循环线程上调用会抛 InvalidStateError
3) DELETE FROM conversations WHERE thread_id     # 再删索引行
```

**为什么这个顺序**：反过来（先删行再删 checkpoint）一旦第 2 步失败，就留下一条**无主的 checkpoint 数据且再也无法通过 API 删除**（归属信息已丢）。按上述顺序，最坏情况是留下一份孤儿 checkpoint——只占空间，不影响正确性，且可用对账脚本清理（找出 `checkpoints.db` 里 `thread_id` 不在 `conversations` 中的行）。

### 7.7 验收清单（手工）

| # | 步骤 | 预期 |
|---|---|---|
| 1 | 不设 `RAGQA_JWT_SECRET` 启动 | **启动失败**，报明确提示 |
| 2 | 注册 `hao` / `abcd1234` | `201 {id,username}`；DB 里 `password_hash` 以 `$2b$` 开头，**不是明文** |
| 3 | 用同名再注册 | `409 {"code":"USERNAME_TAKEN"}` |
| 4 | 注册 `ab` / `x` | `422 {"code":"VALIDATION_ERROR","message":"username: ..."}` ← **验证 422 已符合契约** |
| 5 | 注册 73 字节密码 | `422`，不是静默成功 |
| 6 | 登录正确密码 | `200`，拿到 `access_token` |
| 7 | 登录错误密码 | `401 {"code":"INVALID_CREDENTIALS"}`（**不透露是用户名还是密码错**） |
| 8 | 不带 token 调 `history` | `401 {"code":"UNAUTHORIZED"}` |
| 9 | 带 token 提问一次，查 MySQL | `conversations` 有一行，`title` = 首问前 20 字 |
| 10 | 注册第二个账号，用它的 token 访问第一个账号的 thread | `403 {"code":"FORBIDDEN"}` |
| 11 | 第二个账号访问一个**从未用过**的 uuid | `200 {"messages":[]}`（**不是 403**） |
| 12 | `GET /api/health` 不带 token | `200`（保持公开） |
| 13 | 手改 token 一个字符 | `401 UNAUTHORIZED`（签名校验生效） |
| 14 | 把 `exp` 改成过去时间的 token（需自签） | `401 UNAUTHORIZED` |

### 7.8 后端 pytest 用例

1. `test_register_hashes_password` — 注册后 DB 里的 hash 不等于明文，且 `verify_password` 通过
2. `test_register_duplicate_username_409`
3. `test_register_short_username_422_and_contract_shape` — 断言错误体是 `{code,message}` 而非 `{detail}`
4. `test_password_over_72_bytes_rejected`
5. `test_login_wrong_password_401` — 且响应体不区分「用户不存在」与「密码错」
6. `test_me_requires_token` / `test_me_rejects_tampered_token`
7. `test_stream_creates_conversation_row_with_title`
8. `test_history_of_other_users_thread_403`
9. `test_history_of_unknown_thread_returns_empty_200`（守住既有约定）
10. `test_delete_thread_removes_both_checkpoint_and_row`
11. `test_delete_other_users_thread_403_and_keeps_data`（403 时**不得**删任何东西）
12. `test_kb_endpoints_require_login_but_not_ownership`（本期语义：登录即可删他人文档 —— 用测试钉住这个已知行为，P3 再改）

> **与已落地单测的分工（2026-09-16）**：`backend/tools/security.py` 的**单元**行为已由
> `tests/test_security.py`（21 个用例）锁死 —— 覆盖「73 字节 → 422」「脏哈希 / 超长口令回 False」
> 「过期 / 篡改 / 无 `exp` / 垃圾串 → 401」「`sub` 是字符串」「空密钥快速失败」。
> 因此**本节用例只该覆盖端点 / 集成层**，不要再测一遍工具函数：
> 第 4 条要断言的是「HTTP 请求 73 字节密码 → `422` 且错误体是 `{code,message}`」；
> 第 1 条测的是「注册后 DB 里存的是哈希而非明文」（需要 DB）；第 6 条测的是
> `deps.get_current_user` 与端点的接线，不是 `decode_token` 本身。

### 7.9 实现回写（2026-09-16）

基础设施与鉴权原语已**先行落地**，与本文档初稿有**六处有意偏离**。本节是本 spec 的**唯一权威口径**，
§7.2 / §7.3 / §7.4 已按此同步；后续按 spec 施工时以本节为准。

| # | 初稿 | 实际落地 | 理由 |
|---|---|---|---|
| 1 | `backend/app/db.py` | `backend/tools/mysql_db_tools.py` | 与拆分后的 `backend/tools/upload_function_tools.py` 同层；`Base` 单独放 `backend/app/models.py`，避免 db ⇄ models 循环引用 |
| 2 | `RAGQA_DATABASE_URL` / `settings.database_url` | `RAGQA_MYSQL_DATABASE_URL` / `settings.mysql_database_url` | 更具名：项目里会并存多种存储（Chroma / SQLite checkpointer / Redis），名字要能区分 |
| 3 | `close_db()` / `get_db()` | `aclose_db()` / `get_db_session()` | `aclose_db` 与既有 `graph.aclose_checkpointer()` 命名对称（都是异步释放）；`get_db_session` 表明它 yield 的是 `AsyncSession` |
| 4 | 默认值 `...CHANGE_ME...` 占位串 | `""` + 构造引擎前快速失败 | 占位符会被当真去连，属「看起来配好了但连不上」的更差失败模式；空串会抛点名 `RAGQA_MYSQL_DATABASE_URL` 的 `RuntimeError` |
| 5 | `backend/app/security.py` | `backend/tools/security.py` | 与 `mysql_db_tools.py` / `http_tools.py` 同层。本模块是**纯函数工具**（不碰 DB、不碰请求上下文），放 tools 层比 app 层更贴切 |
| 6 | `api_error` 放 `function_tools.py`、与 `err()` 并列 | 独立模块 `backend/tools/http_tools.py` | 它已不再只服务上传链路：`security.py`、P2 的 `deps.py`、各 service 都要用。留在 `upload_function_tools.py` 里会让「鉴权」反向依赖「知识库上传」，层次错乱 |

**已实现、可直接复用**：

*A. `backend/tools/mysql_db_tools.py`*（`tests/test_mysql_db.py`，13 个离线用例锁死）

- `get_db_engine() -> AsyncEngine` / `get_db_session_maker()`：**同步**函数 + `@lru_cache(maxsize=1)`。
  ⚠ 这两个**绝不能**写成 `async def` —— `lru_cache` 缓存的是**协程对象**，第一次 `await` 后作废，
  第二次调用抛 `RuntimeError: cannot reuse already awaited coroutine`，且被缓存强引用永不回收。
- `get_db_session()`：异步生成器依赖，消费方式是 `Depends(get_db_session)` 或 `async for`，
  **不能** `await get_db_session()`（拿到的是生成器对象，不是 session）。
- `init_db()`：函数内局部导入 `Base` 以**保证模型注册顺序**（`create_all` 只建已注册到该 metadata
  的表）；metadata 为空时**直接报错**，不静默建 0 张表。
- `aclose_db()`：`dispose()` + **成对**清空两个 `lru_cache`（只清引擎会留下「引擎已释放、
  工厂仍持有旧引擎」的半残状态）。

*B. `backend/tools/security.py`*（`tests/test_security.py`，21 个离线用例锁死）

- `hash_password(password) -> str`：bcrypt，输出 `$2b$` 开头、**固定 60 字符**，正合 §5 的 `VARCHAR(60)`。
  ⚠ **>72 字节必须前置拦成 422**：bcrypt 5.0.0 的 changelog 原文 —— "Passing `hashpw` a password
  longer than 72 bytes now raises a `ValueError`. Previously the password was silently truncated."
  少了前置校验就是 **500**，而不是契约要的 422。阈值按 **UTF-8 字节**算、不是字符数
  （30 个汉字 = 90 字节；只看 `len(str)` 会漏判）。
- `verify_password(password, password_hash) -> bool`：**永不抛异常**。空串 / 非 bcrypt 串 /
  非 ASCII 脏哈希 / 超长口令一律回 `False`。否则**一条脏数据行**就能把登录接口打成 500；
  登录失败是预期内的业务分支，不该用异常表达。
- `create_access_token(user_id, username) -> str`：payload 与 §6.2 一致 `{sub, username, exp, iat}`。
  `sub` 必须是**字符串**（RFC 7519 StringOrURI）；`iat` / `exp` 用 **UTC aware** 时间
  —— 传 naive datetime 会被按本地时区解释，表现为极难查的"token 时区偏移"。
- `decode_token(token) -> dict`：`algorithms=[settings.jwt_algorithm]` **显式白名单**
  （防算法混淆攻击；绝不能"从 token 头读 `alg`、读什么算什么"）；`options={"require": ["exp","sub"]}`
  拒掉无 `exp` 的**永不过期**凭证；过期 / 篡改 / 缺字段 / 垃圾串统一 401 且**不区分原因**。

*C. `backend/tools/http_tools.py`*

- `err(status, code, message) -> JSONResponse`（路由内 `return`）与
  `api_error(status, code, message) -> NoReturn`（依赖注入 / service 层 `raise`）成对存在，
  `detail` 形状统一为 `{"code","message"}`，由 §7.4 item 2 的统一异常处理器翻译成最终响应体。
- 它是**所有层的公共依赖**，因此**不得**在其中 import 任何项目内业务模块（否则立刻出现 import 环）。
- 副产品：抽出后 `upload_function_tools.py` 不再依赖 fastapi，只剩解析 / 切分 / 嵌入 / 落库这类纯数据活儿。

**尚未对齐、P2 施工前需要拍板**：

1. `backend/app/models.py` 的 `User` 目前只有 `id / username / password`，与 §5 的
   `password_hash VARCHAR(60)` / `created_at DATETIME(6)` / 主键 `BIGINT UNSIGNED` /
   显式 `utf8mb4_0900_ai_ci`（**大小写不敏感**的唯一性，§5 明确 `Alice` 与 `alice` 撞唯一键是想要的行为）
   **尚未对齐**。⚠ **表还没有在任何真实库里落地，现在是改字段名的零成本窗口**，
   越晚改越贵（需手工 `ALTER`，本项目未引入迁移框架）。
2. `Conversation` 模型（`thread_id → user_id` 归属映射）尚未创建，而它是 §7.5 归属校验的
   **唯一依据**，本期必须补。
3. `rag_qa` 数据库在本地 MySQL 上**尚不存在**：实测报 `1049 Unknown database 'rag_qa'`
   （服务可达、口令认证通过）。先执行
   `CREATE DATABASE rag_qa CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;`，再跑 `init_db()`。
4. `.env.example` 尚未创建，且放置位置需确认（见 §7.2 的说明）。
5. `backend/tools/security.py` 目前**自带一份 `_api_error` 本地副本**（`http_tools.api_error`
   落地前的权宜之计）。两者 `detail` 形状完全一致、行为无差异，21 个用例不受影响；
   后续把那 2 处调用改成 `from backend.tools.http_tools import api_error` 即可消除重复。
6. `config.py` 的 `jwt_secret` **还缺启动期校验**：§7.2 要求 `@model_validator(mode="after")`
   为空就启动失败，目前只有字段声明。当前 `security._jwt_secret()` 保证了「一旦用到就失败」
   （绝不会静默拿空密钥去签发），但正确的是让 uvicorn **起不来**，而不是等第一个登录请求才 500。

## 8. 前端实现设计

### 8.1 文件清单

| 文件 | 动作 | 职责 |
|---|---|---|
| `src/api/types.ts` | 改 | `AuthUser`/`LoginRequest`/`LoginResponse`/`RegisterRequest`；`ErrorCode` +4 |
| `src/api/tokenStore.ts` | **新建** | `getToken`/`setToken`/`clearToken`（key `ragqa_token`）——单独成模块便于测试时替换 |
| `src/api/client.ts` | 改 | `authHeaders()`；6 处 fetch 全部带头；新增 `register`/`login`/`fetchMe`；`setUnauthorizedHandler()` |
| `src/hooks/useAuth.ts` | **新建** | `{status:'loading'\|'anon'\|'authed', user, login, register, logout, error}` |
| `src/components/LoginPage.tsx` | **新建** | 登录/注册切换、两个输入框、错误行、busy 态 |
| `src/App.tsx` | 改 | 未登录渲染 `LoginPage`，已登录渲染现有界面 |
| `mock/server.mjs` | 改 | `/api/auth/*` 三端点 + 内存 users + 假 JWT；chat/kb 端点校验 `Authorization`，缺失返回 401 |
| `src/test/useAuth.test.ts` | **新建** | +6 用例 |
| `src/test/login-page.test.tsx` | **新建** | +5 用例 |
| `src/test/client.test.ts` | 改 | +4 用例（authHeaders、401 回调） |
| `src/test/app.test.tsx` | 改 | 适配（mock 工厂补 `fetchMe`），+2 用例（未登录渲染 LoginPage） |
| `frontend/README.md` | 改 | 补 `useAuth` / `LoginPage` / `tokenStore` 与 401 处理说明 |

### 8.2 `client.ts` 的 401 集中处理

```ts
let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: () => void) { onUnauthorized = fn; }

/** 所有 fetch 都过这一层：加 Authorization 头 + 统一识别 401 */
async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const token = getToken();
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { ...(init.headers || {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) },
  });
  if (res.status === 401) { clearToken(); onUnauthorized?.(); }
  return res;
}
```

**为什么集中**：现有 6 处 fetch 各自拼 URL 与头，漏一处就是一个「带着过期 token 静默失败」的 bug。收敛到 `request()` 后，401 处理只有一份实现。

`useAuth` 在挂载时注册 `setUnauthorizedHandler(() => setStatus('anon'))` → token 过期时自动回登录页，不需要用户手动刷新。

### 8.3 `useAuth` 状态机

| status | 含义 | 触发 |
|---|---|---|
| `loading` | 有 token，正在 `fetchMe()` 验证 | 初始挂载且 `getToken()` 非空 |
| `anon` | 无 token 或验证失败 | 无 token / `fetchMe` 401 / logout |
| `authed` | 验证通过，`user` 有值 | `fetchMe` 200 或 `login` 成功 |

**为什么启动时要 `fetchMe()` 而不是直接信任 localStorage**：token 可能已过期、可能被改过、可能 secret 换了。不验证就会出现「界面显示已登录，但每个请求都 401」的错位状态。

`logout()` = `clearToken()` + `setStatus('anon')`，**不调后端**（决策：无 logout 端点）。

### 8.4 `LoginPage` 规格

- 单卡片居中，标题「登录 / 注册」用一个 segmented 切换（不引路由）
- 两个输入：用户名（`autoComplete="username"`）、密码（`type="password"`，`autoComplete="current-password"` / 注册态 `new-password`）
- 提交按钮 busy 时禁用并显示「处理中…」
- 错误行 `data-testid="auth-error"`，红色小字，直接显示后端 `message`
- **前端只做非空校验**，长度/字符集校验交给后端（避免两处规则漂移）
- `data-testid`：`auth-username`、`auth-password`、`auth-submit`、`auth-toggle`

### 8.5 测试影响（必须提前知道）

`App.tsx` 加了鉴权门槛后，**现有渲染 App 的测试会全部失败**，除非 mock 工厂补 `fetchMe`：

- `src/test/app.test.tsx`：`vi.mock('../api/client')` 的工厂要加 `fetchMe: vi.fn().mockResolvedValue({id:1,username:'hao'})`、`register`、`login`、`setUnauthorizedHandler: vi.fn()`
- 涉及 KB 的测试若渲染 `App` 同样要补
- 只渲染单个组件（`Header`/`ConfirmDialog`/`DocList`）的测试不受影响

## 9. 错误处理矩阵

| 情况 | 后端 | 前端 |
|---|---|---|
| 未带 token | `401 UNAUTHORIZED` | 清 token → 回登录页 |
| token 过期/被篡改 | `401 UNAUTHORIZED` | 同上（`request()` 统一拦截） |
| 用户名或密码错 | `401 INVALID_CREDENTIALS` | 登录页错误行显示后端 message |
| 用户名已存在 | `409 USERNAME_TAKEN` | 注册态错误行显示 |
| 访问他人 thread | `403 FORBIDDEN` | 聊天区显示错误；**不清空当前消息** |
| 校验失败 | `422 VALIDATION_ERROR` | 错误行显示（本期起是契约格式） |
| MySQL 连不上 | 启动即失败（lifespan `init_db`） | — |
| MySQL 请求中断连 | `500 INTERNAL_ERROR` | 「无法连接后端」 |

## 10. 测试策略

| 层 | 数量 | 重点 |
|---|---|---|
| 后端 pytest | +12（§7.8） | 归属校验、422 契约格式、72 字节上限、`StaticPool` 内存库 |
| 前端 vitest | +17（useAuth 6 / LoginPage 5 / client 4 / app 2） | 401 自动登出、启动时 `fetchMe` 验证、authHeaders 注入 |
| mock 端到端 | — | curl 走 §7.7 的 14 步（mock 与真后端各一遍） |

前端既有 66（P1 后 83）用例必须保持绿色——**除 §8.5 说明的 mock 工厂适配外，不允许修改既有断言**。

## 11. 安全考量与已知风险

| 风险 | 说明 | 缓解 / 升级路径 |
|---|---|---|
| **XSS 窃取 token** | localStorage 里的 JWT 可被任意注入脚本读取（本期选定的方案） | 不引入第三方脚本；升级路径：改 httpOnly cookie + `SameSite=Lax` + CSRF 双重提交 |
| **JWT 无法吊销** | 登出只清前端，token 在 7 天内仍有效 | 缩短有效期 + 加 refresh token（本期 YAGNI）；或改服务端 Session |
| **无登录限流** | 可离线/在线暴力破解密码 | 生产必须加（slowapi / nginx limit_req）；本期记为已知风险 |
| **KB 越权窗口** | P2 期间任何登录用户可删他人上传文档 | P3 修复；本期用测试钉住该行为（§7.8 用例 12），避免误以为已修 |
| **`create_all` 不做迁移** | 改字段需手工 `ALTER TABLE` | 引入 Alembic（技术债） |
| **孤儿 checkpoint** | §7.6 顺序下第 3 步失败会留下无主数据 | 只占空间不影响正确性；提供对账脚本思路 |
| **用户名枚举** | 注册返回 409 会暴露用户名是否存在 | 学习项目可接受；生产可改为「注册成功」统一文案 + 邮件验证 |

## 12. 上线顺序

1. `.env` 加 `RAGQA_JWT_SECRET`（**先于代码**，否则启动失败）。
   `RAGQA_MYSQL_DATABASE_URL` **已就位**（2026-09-16 落地，见 §7.9），指向库 `rag_qa`。
2. MySQL 建库：`CREATE DATABASE rag_qa CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;`
   ⚠ 本地实例上该库**尚不存在**（实测 `1049 Unknown database 'rag_qa'`），**必须先建**，
   否则 `init_db()` 会让进程起不来（那正是 §7.4 期望的行为，不是 bug）。
   另建议建一个非 root 账号（**不要用 notebook 里那个 root**）。
3. `uv add pyjwt bcrypt`
4. 部署代码，启动 → `create_all` 自动建两张表
5. 跑 §7.7 的 14 步验收
6. 前端 `npm run test` 全绿 + `npm run build`

## 13. 技术债与遗留项

| 项 | 说明 |
|---|---|
| `agent/schemas.py` 过大 | chat/auth/kb/system 四类 schema 混在一个文件，建议拆分 |
| 无 Alembic | 改表结构需手工 ALTER，多环境易漂移 |
| 无 refresh token | 7 天后必须重新登录 |
| 无登录限流 | 见 §11 |
| KB 归属隔离 | → P3 |
| 会话列表 UI | → P3 |
| `fastapi_tutorial/fastapi_06_SQLAlchemy ORM.ipynb:117` 硬编码 MySQL root 密码 | 该文件在 git 仓库根（`advanced_tutorial/`）**之外**，未被推送；但建议就地改成 `.env` 读取，避免哪天扩大仓库范围时泄露 |
