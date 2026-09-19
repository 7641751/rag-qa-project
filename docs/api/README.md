# LangChain 智能问答 API 契约

CRAG（纠错式检索增强）问答后端的接口契约。前端（`frontend/`）与后端（用户实现的 FastAPI）**以本文档 + `openapi.yaml` 为唯一真相源**：任何字段/事件变更，先改这里再改代码。

- **Base URL**：`http://localhost:8000`
- **开发期**：前端 Vite（`:5173`）经 proxy 把 `/api` 转发到后端 `:8000`，故前端代码里一律用相对路径 `/api/...`。
- **机器可读版**：见同目录 `openapi.yaml`（可导入 Postman / Apifox）；真实 SSE 样例见 `sse-example.txt`。

---

## 端点清单

| 方法 | 路径 | 用途 | 需登录 | 响应类型 |
|---|---|---|---|---|
| `POST` | `/api/auth/register` | 注册 | — | `application/json` |
| `POST` | `/api/auth/login` | 登录，换取 JWT | — | `application/json` |
| `GET` | `/api/auth/me` | 当前登录用户 | ✅ | `application/json` |
| `POST` | `/api/chat/stream` | 提问并流式作答 | ✅ | `text/event-stream` |
| `GET` | `/api/chat/history` | 拉取会话历史（`?thread_id=`） | ✅ | `application/json` |
| `GET` | `/api/chat/threads` | 列出当前用户的会话（倒序，最多 50 条） | ✅ | `application/json` |
| `PATCH` | `/api/chat/threads/{thread_id}` | 重命名会话 | ✅ | `application/json` |
| `DELETE` | `/api/chat/threads/{thread_id}` | 删除会话的全部服务端状态（幂等） | ✅ | `application/json` |
| `POST` | `/api/kb/documents` | 上传文档并入库（SSE 进度） | ✅ | `text/event-stream` |
| `GET` | `/api/kb/documents` | 列出用户上传的文档 | ✅ | `application/json` |
| `DELETE` | `/api/kb/documents/{doc_id}` | 删除上传文档的全部向量块 | ✅ | `application/json` |
| `GET` | `/api/health` | 健康检查 / 就绪探针 | — | `application/json` |

> 「需登录」= 必须带 `Authorization: Bearer <access_token>`，缺失 / 过期 / 被篡改一律 `401`。`/api/health` 保持公开 —— 网关与容器健康检查不会带 token。详见下方「鉴权」。

> 提问用 `POST` 而非 `GET`：中文长问题走 body，避开 URL 长度/编码限制。
>
> 上传用 `POST` **单文件**而非多文件：每个文件一条独立 SSE 流，进度互不干扰、错误互相隔离（第 2 个文件解析失败不影响第 1 个已入库）。前端多选时**串行**发 N 个请求。

---

## 鉴权

**Bearer JWT**。除 `/api/health`、`/api/auth/register`、`/api/auth/login` 外，所有端点都要求请求头：

```
Authorization: Bearer <access_token>
```

- **签发**：`POST /api/auth/login` 返回 `access_token`，默认有效期 **7 天**（`RAGQA_JWT_EXPIRE_DAYS`）。本期不做 refresh token —— 过期即重新登录。
- **失败**：`401` + `{"code":"UNAUTHORIZED","message":...}`。缺失、非 Bearer、过期、签名被篡改**四种情况返回完全相同的响应**，对客户端都是「重新登录」，不给探测留差异。
- **`401` 与 `403` 的分工**：`401` = 没登录或凭证无效；`403` = 登录了，但目标资源不属于你。前端只在 `401` 时清 token 并跳登录页；`403` 只提示错误，**不清空当前消息**。
- **归属规则**（`/api/chat/*`）：`thread_id` 由前端本地生成，归属记录在 `conversations` 表。
  - 库里**没有**该 `thread_id` → 视为新会话，**放行**并返回 `200 {"messages": []}`。此处若返回 `403`，前端用新 uuid 第一次打开会话时就永远打不开。
  - 库里有、但不是本人 → `403 FORBIDDEN`。
  - 首次提问会把 `thread_id → user_id` 落库，`title` 取首问前 20 字；后续提问只刷新 `updated_at`，标题与归属都不再变。
- **`403` 在流开始前**：`/api/chat/stream` 的归属校验发生在返回 `StreamingResponse` **之前**，所以拿到的是 HTTP `403` 而不是 SSE `error` 帧 —— 前端不必为同一端点写两套错误处理。同理，校验先于读 checkpointer，`403` 不因 checkpointer 不可读而变成 `500`。
- **知识库按归属隔离（P3 起）**：上传件的每段 metadata 都带 `user_id`，列表与删除都按它过滤。
  - 列表只返回本人上传件；`builtin` 摘要是**全局**统计（预置的 88 篇官方文档对所有用户共享），刻意不加用户过滤。
  - 删别人的文档 → 过滤命中 0 条 → `404 NOT_FOUND`，**不是 `403`** —— 403 会泄露「该 `doc_id` 存在但不属于你」，而 404 与「这个 id 根本不存在」不可区分。
  - **检索范围**：`{"$or":[{"kb":"langchain_docs"},{"user_id":<自己>}]}` —— 预置文档全局可见，上传件仅本人可见。CLI（`run.py`）没有登录态 → `user_id=None` → **只检索预置文档**，这是刻意行为而非缺陷。
  - **同名替换只在本人范围内检测**：A 传 `笔记.md` 不会影响 B 的同名文档。（P3 之前只按 `filename + origin` 找旧件，A 的同名上传会把 B 的向量**与落盘原件**一起删掉，属数据丢失级缺陷。）

---

## POST /api/chat/stream

提交一个问题，以 SSE（Server-Sent Events）流式返回推理步骤、答案 token、来源与结束标记。

**请求体**（`Content-Type: application/json`）：

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `question` | string | 必填，长度 1..4000 | 用户问题 |
| `thread_id` | string(uuid) | 必填 | 会话 ID，前端 `crypto.randomUUID()` 生成；后端作为 checkpointer 的 thread key |

```json
{ "question": "如何切分 markdown?", "thread_id": "550e8400-e29b-41d4-a716-446655440000" }
```

**响应**：`200`，`Content-Type: text/event-stream`，事件见下一节。

**流开始前的错误**（尚未发出 200 响应头时）：
- `422`：请求体不合法（Pydantic 校验失败）。
- `503`：知识库未就绪。
- 错误体统一为 `{ "code": "...", "message": "..." }`。

**curl 自测**：
```bash
curl -N -X POST http://localhost:8000/api/chat/stream \
  -H "Content-Type: application/json" \
  -d "{\"question\":\"如何切分 markdown?\",\"thread_id\":\"550e8400-e29b-41d4-a716-446655440000\"}"
```
（`-N` 关闭 curl 缓冲，才能看到逐条事件。）

---

## SSE 事件协议

**帧格式**：每条事件由 `event:` 行 + `data:` 行组成，以**空行**结束：
```
event: <type>
data: <json>

```

**5 类事件**：

| event | 时机 | data 载荷 |
|---|---|---|
| `step` | 每个 CRAG 节点完成时 | `{"node","label","detail"}` |
| `token` | `generate` 节点每吐一段 | `{"text"}` |
| `sources` | 生成结束前，一次性 | `{"sources":[{"title","snippet","score"}]}` |
| `done` | 正常结束 | `{"thread_id","rewrites","grounded"}` |
| `error` | 流中途出错 | `{"code","message"}` |

**字段定义**：
- `step.node` ∈ `{retrieve, grade_documents, rewrite_query, generate}`（与 `graph.py` 节点名一致）。
- `step.label`：中文短标签（检索 / 评分 / 重写 / 生成）。
- `step.detail`：可选摘要（如“召回 8 段”“相关 6/8”）。
- `token.text`：字符串，**仅来自 `generate` 节点**——后端必须过滤掉 `grade_documents` / `rewrite_query` 的 JSON 结构化输出，否则会污染答案流。
- `sources[].title`：文档名/标题；`snippet`：可选摘录；`score`：可选相关性（`number` 或 `null`）。
- `done.rewrites`：本次发生的查询重写次数（整数 ≥ 0）。
- `done.grounded`：**布尔，必填**。`true` = 本轮答案有知识库依据（检索并保留了至少一段资料）；`false` = **未命中知识库，答案完全来自模型通用知识**，前端必须渲染醒目的未验证标识。后端用 `bool(final_docs)` 确定性得出，**不依赖模型自述**。
- ❗ `grounded=true` **不代表整段答案都有出处**：资料只够答一半时，模型允许用通用知识补充，但必须先用「以下基于通用知识，未经本知识库验证：」分隔。所以前端除了看 `grounded`，还要正常渲染 Markdown。
- `error.code` ∈ `{VALIDATION_ERROR, RETRIEVAL_ERROR, LLM_ERROR, INTERNAL_ERROR}`；`error.message`：可读中文。

**事件时序**（重要）：
- `retrieve` / `grade_documents` / `rewrite_query` 的 `step` 由 LangGraph `updates` 在**节点完成**时触发，**均在答案 `token` 之前**。
- `generate` 阶段的 `step` **可选**：后端可在转发**首个 `token` 之前**补发一次（标记“开始作答”，下方样例即此写法）；若不发，前端把首个 `token` 视为生成开始。
- 一次成功响应的事件序：若干 `step` → 若干 `token` → `sources` → `done`。
- 出错：发 `error` 后关闭流（**不再有** `done`）。
- 若发生查询重写循环，`step` 会重复出现（如 retrieve→grade→rewrite→retrieve→grade→generate）。

**样例流**（同 `sse-example.txt`）：
```
event: step
data: {"node":"retrieve","label":"检索","detail":"召回 8 段"}

event: step
data: {"node":"grade_documents","label":"评分","detail":"相关 6/8"}

event: step
data: {"node":"generate","label":"生成","detail":"开始作答"}

event: token
data: {"text":"要切分 markdown，"}

event: token
data: {"text":"用 RecursiveCharacterTextSplitter.from_language(Language.MARKDOWN, ...)。"}

event: sources
data: {"sources":[{"title":"01_文档加载与文本分割.md","snippet":"RecursiveCharacterTextSplitter.from_language(...)","score":0.82}]}

event: done
data: {"thread_id":"550e8400-e29b-41d4-a716-446655440000","rewrites":0,"grounded":true}
```

**未命中知识库时的样例流**（`grounded=false`，`sources` 为空，但仍有 token 流）：
```
event: step
data: {"node":"retrieve","label":"检索","detail":"召回 4 段"}

event: step
data: {"node":"grade_documents","label":"评分","detail":"保留 0 段相关"}

event: step
data: {"node":"rewrite_query","label":"重写","detail":"重写为: LangGraph checkpointer persistence"}

event: step
data: {"node":"generate","label":"生成","detail":"开始作答"}

event: token
data: {"text":"⚠ 本回答未命中知识库，基于模型通用知识，可能过时或有误，请自行核实。\n\n"}

event: token
data: {"text":"LangGraph 通过 checkpointer 在每个 super-step 后保存状态…"}

event: sources
data: {"sources":[]}

event: done
data: {"thread_id":"550e8400-e29b-41d4-a716-446655440000","rewrites":2,"grounded":false}
```

---

## GET /api/chat/history

拉取某个会话的历史消息，用于前端刷新后回填（后端 checkpointer 为单一真相源）。

**Query 参数**：`thread_id`（必填，uuid）。

**响应** `200 application/json`：
```json
{
  "thread_id": "550e8400-e29b-41d4-a716-446655440000",
  "messages": [
    { "role": "user",      "content": "如何切分 markdown?" },
    { "role": "assistant", "content": "答案全文…", "sources": [ {"title":"01_文档加载.md","snippet":"…","score":0.82} ], "grounded": true }
  ]
}
```
- `role` ∈ `{user, assistant}`。
- `assistant.sources` **可选**：需后端把来源随 `AIMessage`（如 `additional_kwargs`）持久化才返回；否则省略，刷新后不显示来源芯片。
- `assistant.grounded` **可选但强烈建议持久化**：`false` 表示该回答无知识库依据。**不存就会造成安全风险**——用户刷新页面后，一条无依据的答案看上去和有依据的一样，警示标识丢了。后端把它写进 `AIMessage.additional_kwargs["grounded"]` 即可（与 `sources` 同一套路）。
- `step`（推理步骤）是瞬时的，**不进 history**。
- **新 / 未知 `thread_id` → `200` + 空 `messages`**（不报 404，新建会话本就无历史）。
- 后端实现提示：`graph.get_state(config={"configurable": {"thread_id": ...}}).values["messages"]` 映射为上述结构。

---

## DELETE /api/chat/threads/{thread_id}

删除一个会话在服务端的全部状态：LangGraph checkpointer 里该 `thread_id` 的**所有 checkpoint 与 write 行**。**不可恢复**。

**Path 参数**：`thread_id`（必填，uuid）。

**响应** `200 application/json`：
```json
{ "thread_id": "550e8400-e29b-41d4-a716-446655440000", "deleted": true }
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `thread_id` | string | ✅ | 回显请求路径中的 id |
| `deleted` | boolean | ✅ | **删除前**该 thread 是否存在至少一个 checkpoint。`false` = 本来就不存在 |

**幂等语义（与 KB 的 DELETE 故意不同）**：

- 未知 / 已删过的 `thread_id` → **`200` + `{"deleted": false}`**，**不是 `404`**。
- 为什么与 `DELETE /api/kb/documents/{doc_id}`（返回 404）不同：**id 的来源不同**。KB 的 `doc_id` 来自**服务端列表**，列表里有却删不到 = 真异常，404 有意义；而 `thread_id` 由**前端本地随机生成**（`crypto.randomUUID()`），清 localStorage、换浏览器、多标签页重复点都会导致「不存在」，404 会变成噪音。DELETE 本身也是幂等方法。

**错误**：

| HTTP | code | 触发条件 |
|---|---|---|
| `422` | `VALIDATION_ERROR` | 仅当后端把 `thread_id` 声明为 `UUID` 类型且格式非法时。**建议后端用 `str`**（与 `GET /api/chat/history` 一致），此时 422 实际不会发生 |
| `500` | `INTERNAL_ERROR` | 数据库异常等服务端错误 |

**副作用与既有约定的关系**：

- 删除后 `GET /api/chat/history?thread_id=<已删的id>` 仍返回 `200 {"messages": []}`，**不是 404**（延续「新 / 未知 `thread_id` → 200 + 空 `messages`」的既有约定）。
- 同一个 `thread_id` 删除后**可以继续使用**：再次提问时 LangGraph 会自动重建该 thread 的 checkpoint。因此前端删除后**不需要换 `thread_id`**。

**后端实现提示**：

- `checkpointer.delete_thread(thread_id)` 返回 `None`，拿不到「是否真删了」，所以要**先探测再删**：`existed = checkpointer.get_tuple(config) is not None`，然后 `delete_thread(...)`，`deleted = existed`。
- SQLite 操作是阻塞的，async 路由里用 `asyncio.to_thread(...)` 包一层，别卡住事件循环。
- 官方依据：`data/langchain_docs/langgraph/add-memory.md:1691-1696`「Delete all checkpoints for a thread」；接口规范见 `checkpointers.md:544-546`（checkpoint 行与 write 行都必须删）。
- ⚠ 别调 `prune` / `copy_thread` / `delete_for_runs`：`InMemorySaver` 4.2.0 **没有实现**它们，基类直接 `raise NotImplementedError`。

**curl 自测**：
```bash
# 1) 删除一个有历史的会话
curl -i -X DELETE http://localhost:8000/api/chat/threads/550e8400-e29b-41d4-a716-446655440000
# HTTP/1.1 200 OK
# {"thread_id":"550e8400-e29b-41d4-a716-446655440000","deleted":true}

# 2) 再删一次 —— 幂等，仍是 200
curl -s -X DELETE http://localhost:8000/api/chat/threads/550e8400-e29b-41d4-a716-446655440000
# {"thread_id":"550e8400-e29b-41d4-a716-446655440000","deleted":false}

# 3) 删除后查历史 —— 200 + 空数组，不是 404
curl -s "http://localhost:8000/api/chat/history?thread_id=550e8400-e29b-41d4-a716-446655440000"
# {"thread_id":"550e8400-e29b-41d4-a716-446655440000","messages":[]}
```

---

## GET /api/health

健康检查 / 就绪探针。

**响应** `200 application/json`：
```json
{ "status": "ok", "kb_count": 2885, "model": "qwen3.7-text-embedding" }
```
- 至少含 `status`；`kb_count`（向量条数）、`model`（嵌入/LLM 标识）便于排障，可选。

---

## 会话列表端点（P3）

侧栏用它列出当前用户的会话，支持切换、重命名、删除。

### GET /api/chat/threads

```json
{ "threads": [{ "thread_id": "2f1c9a04-6b7e-4d21-9c33-1ab2cd34ef56",
                "title": "checkpointer 怎么删会话",
                "created_at": "2026-09-13T06:12:44Z",
                "updated_at": "2026-09-13T06:20:10Z" }],
  "total": 1 }
```

- 只返回**本人**的会话，按 `updated_at` **倒序**，**最多 50 条**。
- `total` 是**真实总数**：`total > threads.length` 即表示被截断，前端据此显示「仅显示最近 50 条」。只回 50 条而不给 `total`，前端无从判断是被截断还是真的只有这么多。
- **空列表 → `200` + `"threads": []`**，不报 404（新用户进来就是这个状态，不能当异常）。
- ⚠ `created_at` / `updated_at` 是**带 `Z` 的** ISO 8601。前端必须按 UTC 解析：`new Date('2026-09-19T06:00:00')`（无 `Z`）按 ECMAScript 规范会被当成**本地时间**，相对时间整体偏掉一个时区（UTC+8 下「刚刚」显示成「8 小时前」）。

### PATCH /api/chat/threads/{thread_id}

请求 `{ "title": "新标题" }`，成功 `200 { "thread_id": "...", "title": "新标题" }`。

| 情况 | 响应 |
|---|---|
| 标题去空白后为空，或超 60 字（DB 列宽 `varchar(60)`） | `422 VALIDATION_ERROR` |
| 会话不属于当前用户 | `403 FORBIDDEN` |
| 会话不存在 | `404 NOT_FOUND` |

- 标题两端的空白会被去掉后再存（`"  x  "` → `"x"`）。
- **重命名不改 `updated_at`**：它不是一次新活动，不该把会话顶到列表最前。

> ⚠ 这里用 `403` 而不是 `404` 是有意的：会话 id 由前端生成、就写在 URL 里，用户本来就知道它存在，403 不构成额外信息泄露。这与「删别人的**文档** → `404`」相反 —— 那个 404 是为了不泄露文档的**存在性**。

### 与删除的关系

`DELETE /api/chat/threads/{thread_id}` 的语义**完全不变**（P1 的幂等 + P2 的归属校验都保留）。唯一联动：删完要重新 `GET /api/chat/threads` 才能拿准 `total` —— 前端本地乐观移除改不了 `total`。

---

## 知识库端点

用户在网页上传自己的文档进知识库。上传写进**与预置文档同一个 Chroma collection**（`langchain_docs`），靠 metadata `origin="upload"` 区分，因此**服务无需重启、图无需重建**，上传完立即可被问答检索到。

真实 SSE 样例见同目录 `kb-sse-example.txt`。

### POST /api/kb/documents

上传单个文件并入库，以 SSE 流式返回处理进度。

**请求**：`Content-Type: multipart/form-data`，单字段 `file`（binary）。

| 约束 | 值 |
|---|---|
| 扩展名白名单 | `.md`、`.txt`、`.pdf`（大小写不敏感） |
| 单文件大小上限 | 20 MB = `20971520` 字节 |

**响应**：`200`，`Content-Type: text/event-stream`，响应头与 `/api/chat/stream` 完全一致（含 `X-Accel-Buffering: no`）。

**3 类事件**：

| event | 时机 | data 载荷 |
|---|---|---|
| `progress` | 每阶段完成 + 每批嵌入完成 | `{"stage","current","total","message"}` |
| `done` | 入库完成 | `{"doc_id","filename","chunks","replaced","uploaded_at"}` |
| `error` | 流中途出错 | `{"code","message"}` |

**字段定义**：
- `progress.stage` ∈ `{saved, parsed, split, embedding}`。**后端只报事实，百分比映射由前端决定**（前端把 `saved`/`parsed` 显示为不定进度，`split` 当 20% 基线，`embedding` 按 `current/total` 在 20%→100% 插值）。
- `progress.current` / `progress.total`：整数。**`embedding` 阶段必须提供**（已嵌入段数 / 总段数）；`split` 阶段**可以**用 `current = total = 总段数` 一次性给出；`saved`/`parsed` 可省略或为 `null`。
- `progress.message`：可读中文短句，前端直接显示（如 `"解析出 48210 字符"`、`"嵌入 60/100"`）。
- `done.doc_id`：uuid，后续删除用。
- `done.chunks`：实际入库段数（整数 ≥ 1）。
- `done.replaced`：布尔，`true` 表示同名旧文档已被替换（前端提示“已更新 xxx”）。
- `done.uploaded_at`：ISO 8601 UTC（如 `2026-09-11T10:23:41Z`）。

**事件时序**：
- 成功：`progress(saved)` → `progress(parsed)` → `progress(split)` → `progress(embedding)` × N → `done`。
- 失败：发 `error` 后关闭流，**不再有 `done`**。
- 前端把 `done` 视为流结束信号；若流在无 `done` 也无 `error` 的情况下关闭（异常断连），前端按“网络中断”处理。

**同名文件视为替换（仅限本人）**：后端按 `{"$and":[{"filename":X},{"origin":"upload"},{"user_id":<自己>}]}` 检测，命中则先删旧向量与旧落盘原件，再按新 `doc_id` 入库，`done.replaced = true`。`user_id` 这个条件不可省 —— 缺了它，A 上传同名文件会把 B 的文档连向量带原件一起删掉。

**中途取消必须回滚**：前端提供取消按钮，用户取消 → fetch abort → 后端异步生成器收到 `asyncio.CancelledError`。此时该文档已写入的向量是“半截”的，会污染检索。**后端必须在 `except asyncio.CancelledError` / `finally` 中按 `doc_id` 回滚已写入向量**，保证“要么整个文档入库成功，要么库里干干净净”。

**前端配合**：上传状态挂在 `App` 层，不随抽屉关闭而卸载——关掉抽屉上传继续在后台跑，重开还能看到进度条。因此正常操作不会误触发 abort。

**curl 自测**：
```bash
curl -N -X POST http://localhost:8000/api/kb/documents -F "file=@/path/to/notes.md"
```

### GET /api/kb/documents

列出用户上传的文档，用于抽屉的文件列表。

**响应** `200 application/json`：
```json
{
  "documents": [
    { "doc_id": "0f0a1b2c-3d4e-4f50-8a6b-7c8d9e0f1a2b", "filename": "report.pdf",
      "title": "Q3 报告", "chunks": 100, "size_bytes": 1258291,
      "uploaded_at": "2026-09-11T10:23:41Z" }
  ],
  "builtin": { "docs": 88, "chunks": 2885 }
}
```
- `documents`：**只含本人的 `origin="upload"`**（P3 起按 `user_id` 过滤），按 `uploaded_at` **倒序**。
- 必填字段：`doc_id`、`filename`、`chunks`、`uploaded_at`；可选：`title`（md 取首个 `# 标题`，pdf/txt 可回退为 filename）、`size_bytes`。
- `builtin`：**整个对象可选**——统计不到就省略，前端自动隐藏那行摘要，不报错。
- **空库 → `200` + `"documents": []`**，不报 404（与 `/api/chat/history` 对未知 `thread_id` 返回空列表的语义一致）。

> ⚠ **后端实现陷阱**：`get_vectorstore()._collection.get(where={"origin":"upload"}, include=["metadatas"])` —— **必须带 `include=["metadatas"]`**，否则 Chroma 会把所有分块的 `page_content` 全拉出来（上传几百段即数 MB 无用数据）。取回后在 Python 里按 `doc_id` 分组计数得 `chunks`。

### DELETE /api/kb/documents/{doc_id}

删除某上传文档的全部向量块。

**路径参数**：`doc_id`（uuid）。

**响应** `200 application/json`：
```json
{ "doc_id": "0f0a1b2c-3d4e-4f50-8a6b-7c8d9e0f1a2b", "filename": "report.pdf", "deleted_chunks": 100 }
```
- **删除过滤条件必须为** `{"$and":[{"doc_id":X},{"origin":"upload"},{"user_id":<自己>}]}`。这一条 where 同时实现三件事：① “预置文档不可删” —— 若 `doc_id` 命中 88 篇官方文档（它们没有 `origin` 字段），过滤结果为空；② “别人的文档删不掉” —— `user_id` 不匹配同样命中 0 条；③ 两者都直接落到 `404`，**无需额外的 403 分支**，也不泄露存在性。
- `doc_id` 不存在 → `404` + `{"code":"NOT_FOUND","message":"文档不存在: <doc_id>"}`。
- **幂等**：重复删同一 `doc_id`，第二次返回 `404`（已不存在），不是 `500`。
- 同时删除 `data/uploads/` 里的落盘原件（后端内部行为，契约不约束）。

### 知识库 metadata 约定

上传文档的每个向量块必须携带：

| 字段 | 必填 | 值 | 用途 |
|---|---|---|---|
| `doc_id` | ✅ | `uuid4()` 字符串 | 删除 / 替换的定位键 |
| `filename` | ✅ | 原始文件名 | 列表展示、同名检测 |
| `origin` | ✅ | 固定 `"upload"` | 与预置文档区分；列表/删除过滤 |
| `user_id` | ✅ | 整数（上传者） | **归属隔离**：列表/删除/同名替换/检索都按它过滤（P3 新增） |
| `uploaded_at` | ✅ | ISO 8601 UTC | 列表排序与展示 |
| `title` | 可选 | md 首个 `# 标题`，否则同 `filename` | 列表展示、来源芯片 |
| `source` | 建议 | `uploads/<doc_id>__<filename>` | 与现有 `source` 语义一致 |
| `size_bytes` | 可选 | 整数 | 列表展示 |

预置的 88 篇文档**保持现状不带 `origin`、也不带 `user_id`**（无需重建），靠 `kb="langchain_docs"` 标记。它们对所有用户可见，因此：

- 列表/删除的过滤条件带 `origin` → 天然只匹配上传项；
- 检索的过滤条件用 `{"$or":[{"kb":"langchain_docs"},{"user_id":uid}]}` → 预置文档全局共享。

> ⚠ **写过滤条件时不要用 `origin` 去找预置文档**：它们根本没有这个字段。写成 `{"$or":[{"origin":"builtin"},...]}` 会让 88 篇官方文档全部从检索结果里消失，**而且不报任何错** —— 问答质量断崖下跌但无人察觉。正确的标记字段是 `kb`。

> ⚠ **P3 之前的旧上传件没有 `user_id`**，在本契约下对所有人不可见。上线时必须先跑 `scripts/migrate_kb_user_id.py` 补迁移（`--dry-run` → `--yes` → 再 `--dry-run` 验证幂等）。

### 限额汇总

| 项 | 值 | 由谁强制 |
|---|---|---|
| 扩展名白名单 | `.md` / `.txt` / `.pdf` | 前端预校验 + 后端 `415` |
| 单文件大小 | ≤ 20 MB（20971520 字节） | 前端预校验 + 后端 `413` |
| 单次选择文件数 | ≤ 5 | 仅前端（契约不限制，每请求本就单文件） |
| 并发上传数 | 1（串行） | 仅前端 |
| 嵌入批大小 | 10 段/批 | 后端（DashScope 上限 20，留余量） |
| 预期耗时 | 100 段约 10–30 秒 | 前端 fetch **不设 timeout**，靠 `AbortController` 让用户取消 |

---

## 错误处理

**两段式**：
- **流开始前**（还没发 200 响应头）：用标准 HTTP 状态码——`422`（校验）、`503`（KB 未就绪）、`500`（内部）。错误体统一 `{ "code", "message" }`。
- **流开始后**（已发 200 + 响应头，无法再改状态码）：发 `event: error`（`code` 同上枚举）再关闭流。

**禁止**返回 `200` 却在 body 里塞错误文本（反模式，前端无法区分成功与失败）。

统一错误码（共 14 个）：

| 归属 | code | 触发方式 |
|---|---|---|
| 鉴权 | `UNAUTHORIZED` | HTTP `401`（token 缺失 / 非 Bearer / 过期 / 被篡改） |
| 鉴权 | `INVALID_CREDENTIALS` | HTTP `401`（登录的用户名或密码错，**故意不区分**两种原因） |
| 鉴权 | `FORBIDDEN` | HTTP `403`（已登录，但 thread 不属于本人） |
| 鉴权 | `USERNAME_TAKEN` | HTTP `409`（注册时用户名已存在） |
| 聊天 | `VALIDATION_ERROR` | HTTP `422` |
| 聊天 | `RETRIEVAL_ERROR` | SSE `error` |
| 聊天 | `LLM_ERROR` | SSE `error` |
| 知识库 | `UNSUPPORTED_FILE_TYPE` | HTTP `415`（流开始前） |
| 知识库 | `FILE_TOO_LARGE` | HTTP `413`（流开始前） |
| 知识库 | `NOT_FOUND` | HTTP `404`（DELETE 的 `doc_id` 不存在） |
| 知识库 | `PARSE_ERROR` | SSE `error`（PDF 加密/损坏、编码无法识别） |
| 知识库 | `EMPTY_DOCUMENT` | SSE `error`（解析后无有效文本，如纯扫描版 PDF） |
| 知识库 | `EMBEDDING_ERROR` | SSE `error`（DashScope 限额/网络/key 失败） |
| 通用 | `INTERNAL_ERROR` | HTTP `500` 或 SSE `error` |

> 上传端点的校验**必须在返回 `StreamingResponse` 之前**完成（先 `await` 读 `UploadFile` 校验类型/大小，再返回流），否则 `415`/`413` 只能降级成 SSE `error` 事件，前端要写两套处理。可先看 `Content-Length` 头快速拒超大请求，再读实际字节数二次确认。

---

## 响应头（stream 端点）

```
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive
X-Accel-Buffering: no
```
> `X-Accel-Buffering: no` 关掉 nginx / 反向代理的缓冲，否则 token 会被憋住、失去流式效果。

---

## 前端消费示例

因为提问是 **POST**，浏览器原生 `EventSource`（只支持 GET）用不了，前端改用 `fetch` + `ReadableStream` 手动解析 SSE，核心是处理**粘包 / 半包**（按 `\n\n` 切帧、残帧留到下次）。完整实现见 `frontend/src/api/client.ts` 的 `streamChat()`：

```ts
const res = await fetch('/api/chat/stream', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
  body: JSON.stringify({ question, thread_id }),
  signal, // AbortController.signal，用于「停止生成」
});
const reader = res.body!.getReader();
const decoder = new TextDecoder();
let buffer = '';
for (;;) {
  const { value, done } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });
  const parts = buffer.split('\n\n');
  buffer = parts.pop() ?? '';           // 残帧留到下次
  for (const frame of parts) dispatch(frame); // 解析 event:/data: 并回调
}
```

---

## 变更约定

本文档 + `openapi.yaml` 是前后端**唯一真相源**。要增删字段或事件类型：**先改这两份契约，再改后端实现与前端 `types.ts` / `client.ts`**，保持三者一致。
