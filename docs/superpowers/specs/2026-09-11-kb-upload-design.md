# 知识库文件上传功能 · 设计文档（Spec）

- **日期**：2026-09-11
- **项目**：`advanced_tutorial/rag_qa_project`
- **本次范围**：接口契约扩展（`docs/api/`）+ 前端知识库 UI（**后端由用户实现**）
- **状态**：设计已与用户逐节确认（架构与数据流 / 接口契约 / 前端结构 / 错误处理·测试·交付物）

---

## 1. 背景与目标

### 1.1 现状

`rag_qa_project` 已完成从 CLI 到 Web 的重构：

- **后端**（用户实现）：`backend/app/api/main.py`（FastAPI + CORS）、`chat_router.py`（`POST /api/chat/stream`、`GET /api/chat/history`）、`services/chat_service.py`（SSE 映射 + `AIMessageChunk` 节点过滤）、`agent/graph.py`（CRAG 图 + `InMemorySaver` checkpointer + `get_graph()` 的 `lru_cache` 单例）。
- **前端**（已交付）：Vite + React 18 + TS + Tailwind 的单栏聊天 SPA，`fetch + ReadableStream` 消费 SSE，23 个 Vitest 用例全绿，另有 `mock/server.mjs` 可在后端未就绪时联调。
- **契约**（已交付）：`docs/api/README.md`（205 行）+ `openapi.yaml` + `sse-example.txt`，三者为前后端唯一真相源。
- **知识库**：88 篇 langchain 官方文档 markdown，经 `ingest.py` 用 DashScope 嵌入（`qwen3.7-text-embedding`，dim=1024）分批写入 Chroma（`data/chroma_db`，collection `langchain_docs`），共 2885 段。

**当前缺口**：知识库只能通过命令行 `python ingest.py` 从 `data/langchain_docs/` 目录批量灌入，**用户无法在网页上传自己的文档**。

### 1.2 目标

让用户在网页上把自己的文件（`.md` / `.txt` / `.pdf`）上传进知识库，上传完成后**无需重启服务**即可在问答中检索到，并能查看已上传列表、删除不需要的文档。

### 1.3 非目标

见 §11 遗留项。本次不做 OCR、鉴权、分片上传、列表分页、多知识库分组。

---

## 2. 决策记录

| # | 决策点 | 选择 | 理由 |
|---|---|---|---|
| 1 | 分工 | 后端用户写；本次交付 = 契约文档 + 前端 + mock | 沿用上一轮已验证的协作模式，双方可并行 |
| 2 | 文件格式 | `.md` / `.txt` / `.pdf` | md/txt 零新依赖、复用现有 markdown-aware 切分；pdf 覆盖真实用户最常传的类型 |
| 3 | 管理范围 | 上传 + 列表 + 删除（3 端点） | 标准知识库面板闭环；不做"清空整库"（危险操作） |
| 4 | 上传交互 | SSE 进度流 | 50 页 PDF 约 100–200 段，DashScope 分批串行需 10–30 秒，同步转圈体验差且易撞代理超时；前端已有测试过的 SSE 解析器可复用 |
| 5 | 存储策略 | 同一 collection + metadata 区分 | `get_retriever()` 零改动，上传完立即可检索；独立 collection 需改图工厂 + 双路检索，代价过高 |
| 6 | UI 布局 | 右侧抽屉（Drawer） | 对现有单栏三段式 `App.tsx` 是纯增量（Header 加按钮 + 一个覆盖层）；侧栏方案要改双栏并重排所有 `max-w-3xl` 容器 |
| 7 | 列表范围 | 仅上传项 + 预置摘要一行 | 88 篇预置文档全列会淹没列表；完全不提又会让用户困惑"为何能答 LangGraph" |
| 8 | 同名文件 | 视为替换 | 用户心智最自然，列表不堆重复项；可复用删除端点的 metadata 过滤逻辑，边际成本低 |

---

## 3. 架构与数据流

### 3.1 分工边界

| 角色 | 内容 |
|---|---|
| **本次交付（前端 + 契约）** | 抽屉 UI 3 个新组件、`useKnowledgeBase` hook、`client.ts` 3 个新函数、`types.ts` 8 个新类型、mock 3 端点、4 个测试文件、`docs/api/` 三份契约文件扩展、`frontend/README.md` |
| **用户实现（后端）** | `kb_router.py`、`kb_service.py`、`schemas.py` 补 Pydantic 模型、`graph.py` 共享 vectorstore 单例、`main.py` 挂路由、`requirements.txt` 补依赖、`config.py` 加 `uploads_dir` |
| **复用不动** | `chat_service.py`、CRAG 图节点、`useChat.ts`、`useThreadId.ts`、`ChatWindow.tsx`、`Composer.tsx`、`MessageBubble.tsx`、`StepTrace.tsx`、`AnswerMarkdown.tsx`、`SourceChips.tsx` |

### 3.2 上传数据流

1. 用户在抽屉里点选或拖拽文件（可多选）→ **前端先校验**扩展名白名单与单文件 ≤ 20 MB，不合格的**照样进队列并直接标 `error`**（不发请求、不静默丢弃）。
2. 合格文件进入**串行队列**，同一时刻只有一个 `POST /api/kb/documents`（`multipart/form-data`，单字段 `file`）在跑。
3. 后端校验类型/大小 → 不合法则在**返回 `StreamingResponse` 之前**抛 HTTP 4xx（`415`/`413`/`422`）。
4. 生成 `doc_id = uuid4()`，原件落盘 `data/uploads/<doc_id>__<filename>`。
5. **同名检测**：按 `filename` + `origin="upload"` 查 Chroma；命中则先删旧向量与旧文件，标记 `replaced=true`。
6. **解析**：`.md`/`.txt` 直读文本（UTF-8）；`.pdf` 走 `pypdf`（或 PyMuPDF）抽文本。
7. **切分**：`.md`/`.txt` 复用 `ingest.split_documents()`（markdown-aware 分隔符）；`.pdf` 用通用 `RecursiveCharacterTextSplitter`（不带 `from_language`），并**先做换行归一化**（见 §6.3）。
8. **嵌入入库**：每段写入 §4.10 约定的 metadata，**每批 10 条** `add_documents()`（DashScope 单次上限 20），每批 yield 一个 `progress` 事件。
9. 结束 yield `done`。因为写入的是**同一个 collection + 同一 `persist_directory`**，`get_graph()` 的 `lru_cache` 单例无需失效，下一轮提问的 `retrieve` 立即可命中新向量。

### 3.3 架构约束：vectorstore 共享单例

**必须**把 Chroma 实例提取为共享单例，让图的 retriever 与知识库服务用**同一个对象**：

```python
# graph.py
from functools import lru_cache

@lru_cache(maxsize=1)
def get_vectorstore():
    from ingest import build_embeddings
    from langchain_chroma import Chroma
    return Chroma(collection_name=settings.collection_name,
                  embedding_function=build_embeddings(),
                  persist_directory=str(settings.chroma_dir))

def get_retriever():
    return get_vectorstore().as_retriever(search_kwargs={"k": settings.top_k})
```

`kb_service.py` 一律 `from backend.app.agent.graph import get_vectorstore`，**不得**自行 `Chroma(persist_directory=...)` 另建实例。

**原因**：chromadb 对同一路径通常会复用底层 client，但显式共享能彻底消除"新写入的向量在已编译的图里查不到"的缓存不一致风险，同时避免重复加载嵌入模型与索引的开销。这是本次唯一需要改动 `graph.py` 的地方。

### 3.4 原件落盘与 `--force` 重建

上传原件保留在 `data/uploads/`，**不是为了提供下载**，而是为了让 `python ingest.py --force` 重建整库时能把用户上传的一并重新入库。

**理由**：`ingest.py` 的 `--force` 走 `delete_collection()`，会连带删掉用户上传的向量。若不落盘，重建就等于用户文件全部丢失——这是"同一 collection"方案唯一的真实风险，落盘即可化解。

**后端建议**：`ingest.py` 增加对 `settings.uploads_dir` 的重新入库（按文件名里的 `<doc_id>__` 前缀还原 metadata），列为可选但推荐项（§9 交付物 #8）。

---

## 4. 接口契约

### 4.1 端点清单（新增 3 个，`tags=["kb"]`，`prefix="/api/kb"`）

| 方法 | 路径 | 用途 | 请求 | 响应 |
|---|---|---|---|---|
| `POST` | `/api/kb/documents` | 上传单文件并入库 | `multipart/form-data` | `text/event-stream` |
| `GET` | `/api/kb/documents` | 列出上传项 + 预置摘要 | — | `application/json` |
| `DELETE` | `/api/kb/documents/{doc_id}` | 删除某文档全部向量块 | — | `application/json` |

加上现有 3 个（`/api/chat/stream`、`/api/chat/history`、`/api/health`），契约共 **6 个端点**。

> **POST 为单文件而非多文件**：每个文件一条独立 SSE 流，进度互不干扰、错误互相隔离（第 2 个文件解析失败不影响第 1 个已入库）。前端多选时串行发 N 个请求。

### 4.2 `POST /api/kb/documents`

**请求**：`Content-Type: multipart/form-data`，单字段 `file`（binary）。

| 约束 | 值 |
|---|---|
| 扩展名白名单 | `.md`、`.txt`、`.pdf`（大小写不敏感） |
| 单文件大小上限 | 20 MB = `20971520` 字节 |

**响应**：`200`，`Content-Type: text/event-stream`，响应头与 `/api/chat/stream` 完全一致：

```
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive
X-Accel-Buffering: no
```

### 4.3 SSE 事件协议（3 类）

帧格式沿用现有契约：`event:` 行 + `data:` 行 + **空行**结束。

| event | 时机 | data 载荷 |
|---|---|---|
| `progress` | 每阶段完成 + 每批嵌入完成 | `{"stage","current","total","message"}` |
| `done` | 入库完成 | `{"doc_id","filename","chunks","replaced","uploaded_at"}` |
| `error` | 流中途失败 | `{"code","message"}` |

**字段定义**：

- `progress.stage` ∈ `{saved, parsed, split, embedding}`。**后端只报事实，百分比映射由前端决定**（见 §5.6）。
- `progress.current` / `progress.total`：整数。**`embedding` 阶段必须提供**（已嵌入段数 / 总段数，前端据此插值进度）；`split` 阶段**可以**用 `current = total = 总段数` 一次性给出（§4.4 样例即此写法，前端把 `split` 当 20% 基线）；`saved` / `parsed` 阶段可省略或为 `null`。
- `progress.message`：可读中文短句，前端直接显示（如 `"解析出 48210 字符"`、`"嵌入 60/100"`）。
- `done.doc_id`：uuid 字符串，后续删除用。
- `done.filename`：原始文件名。
- `done.chunks`：实际入库段数（整数 ≥ 1）。
- `done.replaced`：布尔。`true` 表示同名旧文档已被替换，前端提示"已更新 xxx"。
- `done.uploaded_at`：ISO 8601 UTC 字符串（如 `2026-09-11T10:23:41Z`）。
- `error.code` / `error.message`：见 §4.7。

**事件时序**：

- 成功：`progress(saved)` → `progress(parsed)` → `progress(split)` → `progress(embedding)` × N → `done`。
- 失败：发 `error` 后关闭流，**不再有 `done`**。
- 前端把 `done` 视为流结束信号；若流在无 `done` 也无 `error` 的情况下关闭（异常断连），前端按"网络中断"标 error。

### 4.4 样例流（将写入 `docs/api/kb-sse-example.txt`）

```
event: progress
data: {"stage":"saved","message":"已接收 report.pdf (1.2 MB)"}

event: progress
data: {"stage":"parsed","message":"解析出 48210 字符"}

event: progress
data: {"stage":"split","current":100,"total":100,"message":"切分为 100 段"}

event: progress
data: {"stage":"embedding","current":10,"total":100,"message":"嵌入 10/100"}

event: progress
data: {"stage":"embedding","current":100,"total":100,"message":"嵌入 100/100"}

event: done
data: {"doc_id":"0f0a1b2c-3d4e-4f50-8a6b-7c8d9e0f1a2b","filename":"report.pdf","chunks":100,"replaced":false,"uploaded_at":"2026-09-11T10:23:41Z"}
```

### 4.5 `GET /api/kb/documents`

**响应** `200 application/json`：

```json
{
  "documents": [
    {
      "doc_id": "0f0a1b2c-3d4e-4f50-8a6b-7c8d9e0f1a2b",
      "filename": "report.pdf",
      "title": "Q3 报告",
      "chunks": 100,
      "size_bytes": 1258291,
      "uploaded_at": "2026-09-11T10:23:41Z"
    }
  ],
  "builtin": { "docs": 88, "chunks": 2885 }
}
```

- `documents`：**只含 `origin="upload"`** 的文档，按 `uploaded_at` **倒序**。
- `documents[].doc_id` / `filename` / `chunks` / `uploaded_at`：**必填**。
- `documents[].title`：可选。`.md` 取首个 `# 标题`；`.pdf`/`.txt` 可回退为 `filename`。
- `documents[].size_bytes`：可选，落盘原件字节数。
- `builtin`：**整个对象可选**——后端统计不到就省略，前端自动隐藏那行摘要，不报错。
- **空库 → `200` + `"documents": []`**，不报 404（与现有 `/api/chat/history` 对未知 `thread_id` 返回空列表的语义保持一致）。

### 4.6 `DELETE /api/kb/documents/{doc_id}`

**路径参数**：`doc_id`（uuid 字符串）。

**响应** `200 application/json`：

```json
{ "doc_id": "0f0a1b2c-3d4e-4f50-8a6b-7c8d9e0f1a2b", "filename": "report.pdf", "deleted_chunks": 100 }
```

- **删除过滤条件必须为** `{"$and": [{"doc_id": X}, {"origin": "upload"}]}`。这一条同时实现了"预置文档不可删"：若 `doc_id` 命中的是 88 篇官方文档（它们没有 `origin` 字段），过滤结果为空 → 直接 `404`，无需额外的 `403` 分支。
- `doc_id` 不存在 → `404` + `{"code": "NOT_FOUND", "message": "文档不存在: <doc_id>"}`。
- **幂等**：重复删同一 `doc_id`，第二次返回 `404`（已不存在），不是 `500`。
- 同时删除 `data/uploads/` 里的原件（后端内部行为，契约不约束）。

### 4.7 错误码

沿用现有**两段式**约定。

**流开始前**（尚未发出 200 响应头）→ 标准 HTTP 状态码 + JSON body `{"code","message"}`：

| HTTP | `code` | 场景 |
|---|---|---|
| `415` | `UNSUPPORTED_FILE_TYPE` | 扩展名不在白名单 |
| `413` | `FILE_TOO_LARGE` | 超过 20 MB |
| `422` | `VALIDATION_ERROR` | 缺 `file` 字段 / 空文件 |
| `404` | `NOT_FOUND` | DELETE 的 `doc_id` 不存在 |
| `500` | `INTERNAL_ERROR` | 未预期异常 |

**流开始后**（已发 200 + 响应头，无法再改状态码）→ `event: error`：

| `code` | 场景 |
|---|---|
| `PARSE_ERROR` | PDF 加密/损坏、文本编码无法识别 |
| `EMPTY_DOCUMENT` | 解析后无有效文本（如纯扫描版 PDF，本次不含 OCR） |
| `EMBEDDING_ERROR` | DashScope 调用失败（限额 / 网络 / key） |
| `INTERNAL_ERROR` | 未预期异常 |

`ErrorCode` 枚举因此扩展为 10 个值：现有 `VALIDATION_ERROR` / `RETRIEVAL_ERROR` / `LLM_ERROR` / `INTERNAL_ERROR` + 新增 `UNSUPPORTED_FILE_TYPE` / `FILE_TOO_LARGE` / `PARSE_ERROR` / `EMPTY_DOCUMENT` / `EMBEDDING_ERROR` / `NOT_FOUND`。

**禁止**返回 `200` 却在 body 里塞错误文本（反模式，前端无法区分成功与失败）。

### 4.8 限额汇总

| 项 | 值 | 由谁强制 |
|---|---|---|
| 扩展名白名单 | `.md` / `.txt` / `.pdf` | 前端预校验 + 后端 415 |
| 单文件大小 | ≤ 20 MB（20971520 字节） | 前端预校验 + 后端 413 |
| 单次选择文件数 | ≤ 5 | 仅前端（契约不限制，每请求本就单文件） |
| 并发上传数 | 1（串行） | 仅前端 |
| 嵌入批大小 | 10 段/批 | 后端（DashScope 上限 20，留余量） |
| 预期耗时 | 100 段约 10–30 秒 | 前端 fetch **不设 timeout**，靠 `AbortController` 让用户取消 |

### 4.9 中途取消与回滚（必须处理的边界）

前端提供取消按钮（否则 30 秒卡死无法退出）。用户取消 → `fetch` abort → 后端异步生成器收到 `asyncio.CancelledError`。**此时该文档已写入的向量块是"半截"的**，会污染检索（半个 PDF 的内容能被查到，但状态不完整）。

**契约要求**：后端必须在 `except asyncio.CancelledError` / `finally` 中**按 `doc_id` 回滚已写入的向量**（复用 §4.6 的过滤删除逻辑），保证"要么整个文档入库成功，要么库里干干净净"。

**前端配合**：`useKnowledgeBase` 的状态**挂在 `App` 层**，不随抽屉关闭而卸载——用户关掉抽屉，上传继续在后台跑，重新打开还能看到进度条。因此正常操作不会误触发 abort，只有显式点"取消"才断流。

### 4.10 metadata 约定

上传文档的每个向量块必须携带：

| 字段 | 必填 | 值 | 用途 |
|---|---|---|---|
| `doc_id` | ✅ | `uuid4()` 字符串 | 删除 / 替换的定位键 |
| `filename` | ✅ | 原始文件名 | 列表展示、同名检测 |
| `origin` | ✅ | 固定 `"upload"` | 与预置文档区分；列表/删除过滤 |
| `uploaded_at` | ✅ | ISO 8601 UTC | 列表排序与展示 |
| `title` | 可选 | md 首个 `# 标题`，否则同 `filename` | 列表展示、来源芯片 |
| `source` | 建议 | `uploads/<doc_id>__<filename>` | 与现有 `source` 语义一致 |
| `size_bytes` | 可选 | 整数 | 列表展示 |

预置的 88 篇文档**保持现状不带 `origin` 字段**（无需重建），因此 `where={"origin":"upload"}` 天然只匹配上传项。

---

## 5. 前端设计

### 5.1 改动清单

```
frontend/src/
├── App.tsx                     改：接 useKnowledgeBase + 挂抽屉 + 给 Header 传 onOpenKb
├── api/
│   ├── types.ts                改：加 8 个 KB 类型 + 扩 ErrorCode 枚举
│   └── client.ts               改：抽出 readSSE()/emitHttpError()，加 3 个新函数
├── hooks/
│   ├── useChat.ts              不动
│   ├── useThreadId.ts          不动
│   └── useKnowledgeBase.ts     新：上传队列状态机（useReducer）
└── components/
    ├── Header.tsx              改：加「📚 知识库」按钮 + 进行中角标
    ├── ChatWindow.tsx          不动
    ├── Composer.tsx            不动
    ├── MessageBubble.tsx       不动
    ├── StepTrace.tsx           不动
    ├── AnswerMarkdown.tsx      不动
    ├── SourceChips.tsx         不动
    ├── KnowledgeBaseDrawer.tsx 新：遮罩 + 右侧面板 + Esc/点击外部关闭
    ├── UploadZone.tsx          新：拖拽/点选 + 前端校验 + 队列进度条
    └── DocList.tsx             新：builtin 摘要行 + 上传项列表 + 删除
frontend/mock/server.mjs        改：加 3 个 KB 端点（内存 documents 数组，可真增真删）
frontend/README.md              改：加「知识库」小节
```

**聊天链路零改动**：`useChat`、`ChatWindow`、`Composer`、`MessageBubble` 及其子组件一行都不动——这是选布局 A（抽屉）的直接红利。

### 5.2 `client.ts` 提炼（复用已验证的 SSE 解析）

现有 `streamChat()` 内部有一段通用的"读流 → `splitFrames` 切帧 → dispatch"逻辑，且被 8 个测试覆盖（粘包/半包/error/非 2xx）。上传端点需要同样逻辑，故提炼两个内部函数：

| 函数 | 职责 | 调用方 |
|---|---|---|
| `readSSE(res, dispatch)` | 读 `ReadableStream` + 按 `\n\n` 切帧 + 收尾 flush 残帧 | `streamChat`、`uploadDocument` |
| `emitHttpError(res, onError)` | 非 2xx → 解析 `{code,message}` → 回调（解析失败则回退 `INTERNAL_ERROR` + `HTTP <status>`） | 同上 |

**约束**：`streamChat` 的外部签名与行为**完全不变**，现有 8 个测试必须继续全绿（提炼后立即跑回归）。

**新增导出函数**：

```ts
export async function uploadDocument(
  file: File,
  handlers: KbStreamHandlers & { signal?: AbortSignal },
): Promise<void>

export async function fetchDocuments(): Promise<KbListResponse>

export async function deleteDocument(docId: string): Promise<KbDeleteResponse>
```

`uploadDocument` 用 `FormData` 提交（**不得**手动设 `Content-Type`，否则丢失 boundary）；`fetchDocuments` / `deleteDocument` 非 2xx 时 `throw new Error`，由 hook 转成 UI 状态。

### 5.3 `types.ts` 新增

```ts
export type KbStage = 'saved' | 'parsed' | 'split' | 'embedding';

export interface KbProgressEvent {
  stage: KbStage;
  current?: number | null;
  total?: number | null;
  message?: string;
}

export interface KbDoneEvent {
  doc_id: string;
  filename: string;
  chunks: number;
  replaced: boolean;
  uploaded_at: string;
}

export interface KbStreamHandlers {
  onProgress?: (e: KbProgressEvent) => void;
  onDone?: (e: KbDoneEvent) => void;
  onError?: (e: ErrorEvent) => void;      // 复用现有 ErrorEvent
}

export interface KbDocument {
  doc_id: string;
  filename: string;
  title?: string;
  chunks: number;
  size_bytes?: number;
  uploaded_at: string;
}

export interface KbBuiltinSummary { docs: number; chunks: number; }

export interface KbListResponse {
  documents: KbDocument[];
  builtin?: KbBuiltinSummary;             // 可选，对齐契约 §4.5
}

export interface KbDeleteResponse {
  doc_id: string;
  filename: string;
  deleted_chunks: number;
}
```

`ErrorCode` 扩展为 10 个值（见 §4.7）。

### 5.4 `useKnowledgeBase` 状态机

```ts
export type UploadStatus = 'pending' | 'uploading' | 'done' | 'error';

export interface UploadItem {
  id: string;              // 前端本地 id（crypto.randomUUID），≠ 后端 doc_id
  file: File;
  status: UploadStatus;
  stage?: KbStage;
  current?: number;
  total?: number;
  message?: string;
  error?: string;
  cancelled?: boolean;     // 用户主动取消，UI 用灰色区别于红色失败
  docId?: string;          // done 后填入
  replaced?: boolean;
}
```

**reducer actions**：`OPEN` / `CLOSE` / `LIST_START` / `LIST_OK` / `LIST_FAIL` / `ENQUEUE` / `PROGRESS` / `UPLOAD_DONE` / `UPLOAD_FAIL` / `CANCELLED` / `DISMISS` / `REMOVE_DOC` / `RESTORE_DOC`

**暴露接口**：

```ts
{
  open: boolean;
  documents: KbDocument[];
  builtin?: KbBuiltinSummary;
  uploads: UploadItem[];
  loadingList: boolean;
  listError?: string;
  openKb(): void;
  closeKb(): void;
  refresh(): Promise<void>;
  addFiles(files: File[]): void;
  cancelUpload(id: string): void;
  removeDoc(docId: string): Promise<void>;
  dismiss(id: string): void;
}
```

**三个必须做对的点**：

1. **串行队列**：用 `useRef` 存待传队列 + 一个 `pump()` 自驱函数，保证同一时刻只有一个 fetch。多文件时后续项显示 `pending`。单个文件失败**不阻断队列**——`pump()` 在 `finally` 里继续下一个。
2. **每文件独立 `AbortController`**（存 ref map），`cancelUpload(id)` 只 abort 指定项，不误伤队列中其他文件。
3. **状态挂在 `App` 层**，不随抽屉卸载（契约 §4.9）：关抽屉上传继续跑，重开可见进度。

**加载时机**：`useEffect` 挂载即 `refresh()`；每次 `openKb()` 也 `refresh()`（拿最新列表）。`refresh()` 失败只设 `listError`，不影响上传能力。

**队列条目生命周期**：`done` / `error` / `cancelled` 的条目都**保留**在队列区，由用户点 × （`dismiss`）移除；抽屉关闭再打开**不清空**队列（状态在 `App` 层，上传可能仍在跑）。队列区与列表区职责分明：队列区回答“**这次传得怎么样**”，列表区（`documents`，来自 `refresh()`）回答“**库里现在有什么**”——两者独立展示，不互相覆盖。

### 5.5 前端校验

```ts
const ALLOWED_EXT = ['.md', '.txt', '.pdf'];
const MAX_BYTES = 20 * 1024 * 1024;      // 20 MB，对齐契约 413
const MAX_FILES_PER_PICK = 5;            // 一次最多选 5 个，串行上传
```

不合格文件**不静默丢弃、不用 `alert`**，而是照样进 `uploads` 队列并直接标 `status:'error'` + 中文原因（如 `virus.exe — 不支持的格式，仅 md/txt/pdf`、`huge.pdf — 超过 20 MB`），用户可点 × 移除（`dismiss`）。超出 5 个的部分同样进队列标 error（`一次最多上传 5 个文件`）。

### 5.6 进度条映射（前端职责）

```ts
function toPercent(u: UploadItem): number | null {
  if (u.status === 'done') return 100;
  if (u.stage === 'embedding' && u.current != null && u.total)
    return Math.round(20 + 80 * (u.current / u.total));   // 嵌入占 20% → 100%
  if (u.stage === 'split')  return 20;
  if (u.stage === 'parsed') return 12;
  if (u.stage === 'saved')  return 6;
  return null;    // null → 渲染不定进度（条纹动画），避免假百分比
}
```

### 5.7 抽屉 UI（Tailwind，沿用现有风格、不引新依赖）

- **遮罩**：`fixed inset-0 bg-slate-900/30`，点击关闭。
- **面板**：`fixed right-0 top-0 h-full w-full max-w-sm bg-white shadow-xl flex flex-col`；进出用 `translate-x-full ↔ translate-x-0` + `transition-transform` 实现滑入滑出。
- **Esc 关闭**：`useEffect` 挂 `keydown` 监听，卸载时移除。
- 面板内 `overflow-y-auto` 让长列表可滚动。
- **Header 按钮**：`📚 知识库`；有进行中上传（`pending`/`uploading`）时显示角标 `↑N`，一眼知道后台还在传。
- **删除**：点击后**乐观更新**（先从 `documents` 移除，`REMOVE_DOC`），失败则 `RESTORE_DOC` 回滚 + 行内错误提示。
- **DocList**：`builtin` 存在时顶部渲染一行只读摘要（`预置文档 88 篇 / 2885 段`），缺失时不渲染该行。
- **UploadZone**：虚线框拖拽区（`border-dashed`），拖入时高亮；下方是队列条目（文件名 + 进度条/状态 + × 按钮）。

### 5.8 mock 服务扩展

`mock/server.mjs` 维护一个**内存 `documents` 数组**，使增删查全流程可跑通（而非只返回静态假数据）：

- `GET /api/kb/documents` → 返回内存数组 + `builtin: {docs: 88, chunks: 2885}`。
- `POST /api/kb/documents` → 读掉 multipart body（不真正解析，从 `Content-Disposition` 取 filename），按契约吐 `progress` × 5（saved/parsed/split/embedding×2）+ `done` 并 push 进数组；**若 filename 以 `.exe` 结尾 → 返回 `415`**，专门验证前端错误路径；同名 → `replaced: true`。
- `DELETE /api/kb/documents/:id` → 从数组删除并返回 `deleted_chunks`；未知 id → `404 NOT_FOUND`。
- CORS 的 `Allow-Methods` 需补 `DELETE`。

---

## 6. 后端要求（用户实现，契约约束）

### 6.1 文件与职责

| 文件 | 动作 | 内容 |
|---|---|---|
| `backend/app/api/kb_router.py` | 新增 | `APIRouter(prefix="/api/kb", tags=["kb"])`；3 端点；POST 返回 `StreamingResponse`（响应头同 §4.2）；**校验在返回 StreamingResponse 之前完成** |
| `backend/app/services/kb_service.py` | 新增 | `upload_document(file)` 异步生成器（yield SSE 帧，复用 `chat_service.sse()`）、`list_documents()`、`delete_document(doc_id)`、取消回滚 |
| `backend/app/agent/schemas.py` | 改 | 加 `KbDocument`、`KbBuiltinSummary`、`KbListResponse`、`KbDeleteResponse` Pydantic 模型，挂 `response_model`（Swagger 才有 schema） |
| `backend/app/agent/graph.py` | 改 | 提取 `get_vectorstore()` 共享单例（§3.3） |
| `backend/app/api/main.py` | 改 | `include_router(kb_router.router)` |
| `config.py` | 改 | 加 `uploads_dir: Path = data_dir / "uploads"` |
| `requirements.txt` | 改 | 加 `python-multipart`、`pypdf` |
| `ingest.py` | 改（可选，推荐） | `--force` 重建时一并重新入库 `settings.uploads_dir`（§3.4） |

### 6.2 依赖

实测当前环境状态：

| 包 | 已安装 | 已声明 | 处理 |
|---|---|---|---|
| `python-multipart` | ✅ | ❌ | **必须在 `requirements.txt` 显式声明**——FastAPI 解析 `multipart/form-data` 依赖它；现在是传递依赖，属"直接依赖未声明"（与之前 `langchain-core` 同类问题） |
| `pypdf` | ❌ | ❌ | **必须安装并声明**，否则 `.pdf` 上传必然失败（`uv add pypdf`）；或改用 PyMuPDF（`fitz`，同样未装） |

### 6.3 实现要点与陷阱

1. **DashScope 嵌入单次上限 20 条**，必须分批（沿用 `ingest.py` 的 `EMBED_BATCH = 10`），每批后 yield `progress(stage="embedding", current=…, total=…)`。
2. **列表查询必须带 `include=["metadatas"]`**：`get_vectorstore()._collection.get(where={"origin":"upload"}, include=["metadatas"])`。否则 Chroma 会把所有分块的 `page_content` 全拉出来（上传几百段即数 MB 无用数据）。取回后在 Python 里按 `doc_id` 分组计数得 `chunks`。
3. **PDF 换行归一化**：PDF 抽出的文本每个视觉行都带换行，直接切分会把句子切碎。建议先合并非段落级换行（如把单个 `\n` 替换为空格、保留 `\n\n` 作为段落边界），再交给通用 `RecursiveCharacterTextSplitter(chunk_size=settings.chunk_size, chunk_overlap=settings.chunk_overlap)`。
4. **切分器选择**：`.md`/`.txt` 复用 `ingest.split_documents()`（markdown-aware）；`.pdf` **不要**用 `from_language(Language.MARKDOWN)`（无结构文本用不上那些分隔符）。
5. **阻塞操作别卡事件循环**：解析、切分、`add_documents()` 都是同步阻塞的。在异步生成器里应放到线程池（`await asyncio.to_thread(...)`），否则会卡住整个 uvicorn worker，连带聊天流一起顿。
6. **`EMPTY_DOCUMENT` 判定**：解析后 `strip()` 为空、或切分结果为 0 段 → 发 `error` 并回滚（此时尚未写入向量，只需删落盘原件）。
7. **同名检测**：按 `{"$and":[{"filename": X},{"origin":"upload"}]}` 查；命中则取其 `doc_id`，先删向量再删旧原件，然后按新 `doc_id` 入库，`done.replaced = true`。
8. **取消回滚**：见 §4.9，`except asyncio.CancelledError` 中按 `doc_id` 删除已写入向量，然后 `raise`（不要吞掉取消信号）。

---

## 7. 错误处理（前端侧，分层兜住）

| 层 | 场景 | 处理 |
|---|---|---|
| 选文件时 | 格式不符 / 超 20 MB / 超 5 个 | 进队列标 `error`，**不发请求**，显示中文原因 |
| 流开始前 | `415`/`413`/`422`/`500` + JSON body | `emitHttpError` → `onError` → 标 error，直接显示后端 `message` |
| 流开始后 | `event: error`（`PARSE_ERROR`/`EMPTY_DOCUMENT`/`EMBEDDING_ERROR`） | 标 error，**保留已收到的 `progress.message`** 当上下文 |
| 网络中断 | fetch reject（`Failed to fetch`） | 标 error"网络中断，请重试"；**不影响队列后续文件** |
| 用户取消 | abort | 标 `cancelled`（灰色"已取消"，与红色失败视觉区分） |
| 流异常关闭 | 无 `done` 也无 `error` | 按"网络中断"标 error |
| 列表加载失败 | GET 非 2xx | 抽屉顶部 `listError` 横幅 +「重试」按钮，**不阻塞上传** |
| 删除失败 | DELETE 非 2xx | `RESTORE_DOC` 回滚乐观更新 + 行内错误提示 |

---

## 8. 测试策略

沿用现有 Vitest + Testing Library + TDD（先写失败测试）。

| 文件 | 关键用例 |
|---|---|
| `src/test/client.test.ts`（扩） | **回归：原 8 用例全绿**（证明 `readSSE` 提炼未改行为）；新增 `uploadDocument` 收 `progress` × N + `done`、`415` → `onError(UNSUPPORTED_FILE_TYPE)`、流中 `error` 事件、`fetchDocuments`/`deleteDocument` 非 2xx 抛错 |
| `src/test/useKnowledgeBase.test.ts`（新） | **串行**（2 文件时断言第 2 个在第 1 个 `done` 前仍是 `pending`）、`.exe` 被拦截不发 fetch、`cancelUpload` 只 abort 指定项、`REMOVE_DOC` 乐观更新 + 失败 `RESTORE_DOC` 回滚、`LIST_FAIL`、**关抽屉后 `uploads` 保留** |
| `src/test/kb-components.test.tsx`（新） | `UploadZone` 选择文件/校验提示、`DocList` 渲染（**`builtin` 缺失时摘要行不渲染**）、删除按钮回调、`toPercent` 各 stage 映射 |
| `src/test/app.test.tsx`（扩） | 点「📚 知识库」→ 抽屉出现；Esc / 点遮罩 → 关闭；上传中 Header 角标显示 |

**已知 jsdom 缺口**：jsdom 构造拖拽事件较别扭 → 用 `new File([...], 'a.md', { type: 'text/markdown' })` 配合 `fireEvent.drop(zone, { dataTransfer: { files } })`；若该路径不稳，退化为只测 `<input type="file">` 的 `change` 事件（实现计划里写明，不留悬念）。沿用上轮已加的 `Element.prototype.scrollIntoView` 桩。

**验收命令**：`npm run test`（全绿）+ `npm run build`（`tsc --noEmit` 零错误）+ mock 端到端 `curl` 验证 3 端点（含 415 路径）。

---

## 9. 交付物清单

### 9.1 本次交付（前端 + 契约，12 项）

| # | 文件 | 动作 |
|---|---|---|
| 1 | `docs/api/README.md` | 加「知识库端点」章节；端点清单表补 3 行；错误码表扩展；限额表 |
| 2 | `docs/api/openapi.yaml` | 加 3 path + 6 schema；`ErrorCode` 枚举扩展；multipart requestBody 用 `type: string, format: binary` |
| 3 | `docs/api/kb-sse-example.txt` | **新增**真实样例流 |
| 4 | `frontend/src/api/types.ts` | 8 个新类型 + `ErrorCode` 扩展 |
| 5 | `frontend/src/api/client.ts` | 提炼 `readSSE`/`emitHttpError` + 3 个新函数 |
| 6 | `frontend/src/hooks/useKnowledgeBase.ts` | **新增** |
| 7 | `frontend/src/components/KnowledgeBaseDrawer.tsx` | **新增** |
| 8 | `frontend/src/components/UploadZone.tsx` | **新增** |
| 9 | `frontend/src/components/DocList.tsx` | **新增** |
| 10 | `frontend/src/components/Header.tsx`、`frontend/src/App.tsx` | 改 |
| 11 | `frontend/mock/server.mjs` + 4 个测试文件 | 改 1 + 扩 2 + 新 2 |
| 12 | `frontend/README.md` | 加「知识库」小节（格式 / 限额 / 串行上传 / mock 端点） |

### 9.2 用户交付（后端 8 项）

见 §6.1 表格。

---

## 10. 运行与验证

```bash
# 后端（用户实现后）
cd advanced_tutorial/rag_qa_project
uv run python -m uvicorn backend.app.api.main:app --host 127.0.0.1 --port 8000

# 前端
cd advanced_tutorial/rag_qa_project/frontend
npm run dev          # http://localhost:5173，/api 经 Vite proxy 转发到 :8000

# 后端未就绪时用 mock
npm run mock         # :8000 假后端，含 3 个 KB 端点
```

**端到端验证清单**：

1. 上传 `.md` → 进度条走完 → 列表出现该文件 → 提问其内容能被检索到（`step(retrieve)` 的 detail 段数变化 / 来源芯片显示该文件名）。
2. 上传同名 `.md`（改过内容）→ `done.replaced=true` → 列表仍只有一条 → 提问命中新内容。
3. 上传 `.exe` → 前端直接标 error，**不发请求**。
4. 上传超大文件（>20 MB）→ 前端拦截；绕过前端直接 curl → 后端 `413`。
5. 上传中点取消 → 该项标"已取消" → **列表里不出现该文件**（后端已回滚）。
6. 上传中关闭抽屉 → 重新打开 → 进度条仍在走。
7. 删除某文件 → 列表消失 → 提问不再命中其内容。
8. 删除预置文档的 doc_id（手动 curl）→ `404 NOT_FOUND`（不可删）。

---

## 11. 遗留项（明确不在本次范围）

- 扫描版 PDF 的 **OCR**（纯图片 PDF 会走 `EMPTY_DOCUMENT`）
- **上传鉴权**——当前任何人可上传/删除，生产环境必须加认证与配额
- 大文件分片上传 / 断点续传
- 列表**分页**（上传项 > 100 时）
- 单文档"重新嵌入"
- 多知识库分组 / 按库过滤检索（决策 #5 已明确不做）
- 上一轮的 P2：`sources.snippet` 里的 HTML 注释噪声、`MISSING_CHECKPOINTER` 占位标题
- `sources.score` 仍为 `null`（需改用 `similarity_search_with_score`）

---

## 12. 验收标准

| 项 | 标准 |
|---|---|
| 契约完整性 | `docs/api/README.md` 端点表含 6 个端点；`openapi.yaml` 含 3 个新 path + 6 个新 schema；`kb-sse-example.txt` 所有 `data:` 行均为合法 JSON |
| 契约一致性 | README 事件表、`openapi.yaml` schema、`kb-sse-example.txt` 样例、`frontend/src/api/types.ts` 四者的字段名与枚举值逐一一致 |
| 前端测试 | `npm run test` 全绿（原 23 用例不回归 + 新增用例） |
| 前端构建 | `npm run build` 退出码 0（`tsc --noEmit` 严格模式零错误） |
| mock 联调 | `curl` 验证 3 个 KB 端点：列表 / 上传（SSE 事件序完整）/ 删除，以及 `.exe` → `415` 错误路径 |
| 聊天链路无回归 | `useChat`、`ChatWindow`、`Composer`、`MessageBubble` 未被修改；`npm run test` 中原有 chat 相关用例全绿 |
