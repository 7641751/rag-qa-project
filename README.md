# LangChain 智能问答助手

基于 **CRAG（Corrective RAG）** 简化模式的全栈问答系统：LangGraph 编排「检索 → 评估 → 重写 → 生成」工作流，
FastAPI 以 SSE 流式吐出推理步骤与答案 token，React 前端渲染 Markdown 答案并支持**用户自助上传文档进知识库**。

知识库内容是 **LangChain / LangGraph 官方文档**（88 篇 md，切分后 2885 段向量），所以它同时也是个「问框架文档」的实用工具。

> 接口契约的唯一真相源是 [`docs/api/README.md`](docs/api/README.md) + [`docs/api/openapi.yaml`](docs/api/openapi.yaml)。
> 要增删字段或事件类型：**先改契约，再改后端实现与前端 `types.ts` / `client.ts`**，保持三者一致。

---

## 技术栈

| 层 | 选型 | 说明 |
|---|---|---|
| 工作流编排 | LangGraph `StateGraph` | 4 节点 + 条件路由 + 重写回环，`InMemorySaver` 做服务端会话记忆 |
| 生成 / 评估 / 重写 | DeepSeek `deepseek-chat` | `temperature=0.1`，`streaming=True`；评估与重写走 `with_structured_output` |
| 嵌入 | DashScope `qwen3.7-text-embedding` | 云端多语言模型，**单次批量上限 20 条**，全项目统一按 10 条/批写入 |
| 向量库 | Chroma（本地持久化） | 单集合 `langchain_docs`，预置文档与用户上传**同集合**，靠 metadata 区分 |
| 后端 | FastAPI + uvicorn | 6 个端点，其中 2 个是 SSE 流 |
| 前端 | React 18 + Vite 5 + TypeScript 5.5 + Tailwind 3 | `react-markdown` + `highlight.js` 渲染答案，`fetch` + `ReadableStream` 消费 SSE |
| 可观测 | LangSmith | `.env` 里 `LANGSMITH_TRACING=true` 即自动上报完整调用树与 token 消耗 |
| 测试 | pytest（后端）/ vitest（前端） | 全部离线跑，零 API 额度 |

---

## 架构

### CRAG 工作流

```
                 ┌──────────┐
                 │  START   │
                 └────┬─────┘
                      ▼
               ┌────────────┐
        ┌─────▶│  retrieve  │  向量检索 top_k 段
        │      └─────┬──────┘
        │            ▼
        │      ┌───────────────┐
        │      │ grade_documents│  LLM 逐段判断相关性（结构化输出）
        │      └─────┬─────────┘
        │            ▼
        │      ◇ 有相关文档? ◇──否，且 rewrites < max_rewrites──┐
        │            │是                                       ▼
        │            │                              ┌──────────────┐
        │            │                              │ rewrite_query │  LLM 改写为更完整的检索式
        │            │                              └──────┬───────┘
        └───────────────────────────────────────────────────┘
                     ▼
               ┌──────────┐
               │ generate │  仅依据检索到的资料作答，禁止编造
               └────┬─────┘
                    ▼
               ┌────────┐
               │  END   │
               └────────┘

  重写次数用尽 → 同样走 generate，输出「知识库中未找到相关信息」兜底
```

### 全栈数据流

```
 React (Vite :5173)                          FastAPI (:8000)                     Chroma
 ─────────────────                          ───────────────                     ──────
 ChatWindow ──POST /api/chat/stream──────▶ chat_service.stream_chat
              ◀── SSE: step/token/           └─ graph.astream(stream_mode=
                       sources/done ────┘         ["updates","messages"])
                                                 ├─ retrieve ──as_retriever──▶ langchain_docs
                                                 ├─ grade_documents  (LLM)       2885 段
                                                 ├─ rewrite_query    (LLM)
                                                 └─ generate         (LLM)

 KnowledgeBaseDrawer ─POST /api/kb/documents─▶ upload_service.sse_upload
   UploadZone          ◀── SSE: progress×N ──┘   ├─ parse_and_split  (pypdf / 切分器)
   DocList                   → done ────────┘    ├─ embed_batch ────────────▶ 同一集合
                                                 └─ 原件落盘 data/uploads/     origin="upload"
```

**关键设计：单集合 + metadata 过滤。** 用户上传的文档写进和预置文档**同一个** Chroma collection，
靠 `metadata["origin"]="upload"` 区分。好处是新向量落库后 retriever 立刻能检索到——
**不需要重启服务，也不需要重建/重编译 LangGraph 图**（`get_graph()` 是 `@lru_cache` 单例）。

---

## 项目结构

```
rag_qa_project/                      ← 唯一 Python 源根（见「开发约定 §1」）
├── config.py                        # 配置中心（pydantic-settings，RAGQA_ 前缀覆盖）+ .env 加载
├── ingest.py                        # 预置知识库入库：加载 → 切分 → 嵌入 → Chroma
├── run.py                           # CLI 入口：普通 / 流式 / 调试 / 打印图结构
├── requirements.txt                 # 可读依赖清单（实际安装以仓库根 pyproject.toml + uv 为准）
│
├── backend/
│   ├── _sse_upload_demo.py          # SSE 上传最小教学 demo（不碰 LangChain，可单独跑）
│   └── app/
│       ├── function_tools.py        # 通用原语：SSE 帧 / 错误响应 / 解析切分 / 嵌入 / metadata 查询
│       ├── agent/
│       │   ├── graph.py             # LangGraph 工作流 + 单例工厂（get_model/get_vectorstore/get_graph）
│       │   └── schemas.py           # RAGState、结构化输出 schema、请求/响应模型
│       ├── api/
│       │   ├── main.py              # FastAPI 装配：CORS、/api/health、挂载两个 router
│       │   ├── chat_router.py       # /api/chat/*
│       │   └── kb_router.py         # /api/kb/*
│       └── services/
│           ├── chat_service.py      # 双模式流式（updates + messages）、节点过滤、历史读取
│           └── upload_service.py    # 上传 SSE 流：逐批进度、取消回滚、同名替换
│
├── frontend/
│   ├── src/api/                     # client.ts（SSE 解析）+ types.ts（对齐 openapi.yaml）
│   ├── src/components/              # ChatWindow / MessageBubble / StepTrace / SourceChips /
│   │                                # AnswerMarkdown / Composer / Header /
│   │                                # KnowledgeBaseDrawer / UploadZone / DocList
│   ├── src/hooks/                   # useChat / useKnowledgeBase / useThreadId
│   ├── src/test/                    # vitest（jsdom）7 个测试文件
│   └── mock/server.mjs              # 无后端时联调用：npm run mock 监听 :8000，实现全部 6 个端点
│
├── data/
│   ├── langchain_docs/              # 88 篇清洗后的官方文档 md（+ INDEX.md 目录，入库时跳过）
│   ├── chroma_db/                   # 向量库持久化目录（ingest 后生成）
│   └── uploads/                     # 用户上传的原件（自动生成；向量库的真相源）
│
├── docs/
│   ├── api/                         # README.md（契约）/ openapi.yaml / sse-example.txt / kb-sse-example.txt
│   └── superpowers/                 # 设计 spec 与实施 plan
│
└── tests/
    ├── test_graph.py                # 11 个用例：图结构、端到端路由、节点单元、路由分支
    └── test_kb_upload.py            # 14 个用例：metadata 落地、SSE 时序、错误码、回滚、幂等
```

---

## 快速开始

### 0. 前置：`.env`

仓库根目录（`my_langchain_demo/.env`）至少需要：

```ini
DEEPSEEK_API_KEY=sk-...          # 生成 / 评估 / 重写
DASHSCOPE_API_KEY=sk-...         # 文本嵌入
LANGSMITH_TRACING=true           # 可选：开启调用链追踪
LANGSMITH_API_KEY=...
LANGSMITH_PROJECT_NAME=...
```

`.env` 由 `config.py` 统一加载。**注意 pydantic-settings 的 `env_file` 只喂给 `Settings` 模型、不会写进
`os.environ`**，而 `DashScopeEmbeddings` / `ChatDeepSeek` 是用 `os.getenv` 取 key 的——所以 `config.py`
里显式调了一次 `load_dotenv`，别删。

### 1. 安装依赖

依赖由**仓库根**的 `pyproject.toml` + `uv` 管理（`rag_qa_project/requirements.txt` 只是可读清单）：

```bash
cd my_langchain_demo
uv sync                                    # 或：.venv\Scripts\activate
```

前端另装：

```bash
cd advanced_tutorial/rag_qa_project/frontend
npm install
```

### 2. 入库（首次必跑）

```bash
cd advanced_tutorial/rag_qa_project
python ingest.py                # 幂等：库已存在则跳过
python ingest.py --force        # 重建：先删 collection 再重新嵌入
```

预期输出：`加载文档: 88 篇` → `切分 chunks: 2885 段` → `入库完成: 2885 条向量`。
按 10 段/批写入（DashScope 上限 20，留余量），全量入库约需数分钟。

### 3. 起后端

```bash
cd advanced_tutorial/rag_qa_project        # ← 必须在这个目录下启动，见「开发约定 §1」
python -m uvicorn backend.app.api.main:app --port 8000 --reload
```

自检（实测输出）：

```bash
curl -s http://127.0.0.1:8000/api/health
# {"status":"ok","kb_count":2885,"model":"qwen3.7-text-embedding"}

curl -s http://127.0.0.1:8000/api/kb/documents
# {"documents":[],"builtin":{"docs":88,"chunks":2885}}
```

交互式文档：<http://127.0.0.1:8000/docs>

### 4. 起前端

```bash
cd advanced_tutorial/rag_qa_project/frontend
npm run dev                     # http://localhost:5173
```

Vite 已配好 proxy，把 `/api` 转发到 `:8000`，所以前端代码里一律用相对路径，不存在 CORS 问题。

**后端还没写好时**，可以用 mock 顶替（同样监听 `:8000`，实现全部 6 个端点）：

```bash
npm run mock
```

---

## CLI 用法

不想起 Web 服务时，`run.py` 提供三种模式：

```bash
python run.py --graph                                     # 打印 ASCII 工作流结构图
python run.py "RecursiveCharacterTextSplitter 怎么用？"     # 普通：一次性输出答案
python run.py "LangGraph 怎么做持久化？" --stream           # 流式：token 实时打印
python run.py "如何构建 RAG agent？" --debug                # 调试：打印每个节点执行后的状态增量
```

---

## HTTP 接口

| 方法 | 路径 | 用途 | 响应 |
|---|---|---|---|
| `POST` | `/api/chat/stream` | 提问并流式作答 | `text/event-stream` |
| `GET` | `/api/chat/history?thread_id=` | 拉取会话历史（checkpointer 是真相源） | `application/json` |
| `POST` | `/api/kb/documents` | 上传文档并入库（SSE 进度） | `text/event-stream` |
| `GET` | `/api/kb/documents` | 列出用户上传的文档 | `application/json` |
| `DELETE` | `/api/kb/documents/{doc_id}` | 删除上传文档的全部向量块 | `application/json` |
| `GET` | `/api/health` | 健康检查 / 就绪探针 | `application/json` |

### SSE 事件

**聊天**（`/api/chat/stream`）：`step` × N → `token` × N → `sources` → `done`；出错发 `error` 后关流。

- `step`：`{"node","label","detail"}`，`node ∈ {retrieve, grade_documents, rewrite_query, generate}`
- `token`：`{"text"}`，**只来自 `generate` 节点**——必须过滤掉 `grade` / `rewrite` 的 JSON 结构化输出，否则答案流里会混进垃圾
- `sources`：`{"sources":[{"title","snippet","score"}]}`，生成结束前一次性给出
- `done`：`{"thread_id","rewrites"}`

**上传**（`/api/kb/documents`）：`progress(saved)` → `progress(parsed)` → `progress(split)` → `progress(embedding)` × N → `done`。

- `progress`：`{"stage","current","total","message"}`。后端只报事实，**百分比映射由前端决定**
  （`split` 当 20% 基线，`embedding` 按 `current/total` 在 20%→100% 插值）
- `current` 是**累计已嵌入段数**，不是批次起点；最后一帧必然等于 `total`
- `done`：`{"doc_id","filename","chunks","replaced","uploaded_at"}`

### 错误处理：两段式

- **流开始前**（还没发 200 响应头）→ 用标准 HTTP 状态码，错误体统一 `{"code","message"}`：
  `415 UNSUPPORTED_FILE_TYPE` / `413 FILE_TOO_LARGE` / `422 VALIDATION_ERROR` / `404 NOT_FOUND`
- **流开始后**（响应头已出去，改不了状态码）→ 发 `event: error`：
  `PARSE_ERROR` / `EMPTY_DOCUMENT` / `EMBEDDING_ERROR` / `LLM_ERROR` / `INTERNAL_ERROR`

> 上传端点的校验必须在返回 `StreamingResponse` **之前**做完，否则 415/413 只能降级成 SSE error，前端要写两套处理。
> **禁止**返回 200 却在 body 里塞错误文本。

---

## 知识库上传

### metadata 约定

上传文档的**每个向量块**写入时就必须带齐：

| 字段 | 必填 | 值 | 用途 |
|---|---|---|---|
| `doc_id` | ✅ | `uuid4()` | 删除 / 替换 / 回滚的定位键 |
| `filename` | ✅ | 原始文件名 | 列表展示、同名检测 |
| `origin` | ✅ | 固定 `"upload"` | 与预置文档区分 |
| `uploaded_at` | ✅ | ISO 8601 UTC | 列表排序与展示 |
| `chunk` | ✅ | 段序号（从 0 连续） | 还原原文顺序 |
| `title` | 可选 | md 取首个 `# 标题`，否则回退文件名 | 列表展示、来源芯片 |
| `source` | 建议 | `uploads/<doc_id>__<filename>` | 与预置文档的 `source` 语义一致 |
| `size_bytes` | 可选 | 整数 | 列表展示 |

预置的 88 篇文档**保持现状不带 `origin` 字段**（无需重建），因此 `where={"origin":"upload"}` 天然只匹配上传项，
删除时也不需要额外的 403 分支——命中 0 条直接 404。

### 三条行为约定

1. **同名视为替换**：按 `filename + origin=upload` 找到旧 `doc_id`，先删旧向量与旧落盘原件，再按新 `doc_id` 入库，`done.replaced=true`。
2. **中途取消必须回滚**：用户点取消 → fetch abort → 后端异步生成器收到 `asyncio.CancelledError`（实测，**不是** `GeneratorExit`）。
   此时已写入的向量是「半截」的，会污染检索，必须按 `doc_id` 连向量带原件一起清掉，保证「要么整个文档入库成功，要么库里干干净净」。
3. **原件必须落盘** `data/uploads/`：向量库是可重建的派生数据，`ingest.py --force` 重建时靠这些原件把用户文档捞回来，否则一次重建 = 用户资料永久丢失。

### curl 自测

```bash
# 上传（-N 关闭 curl 缓冲，才能看到逐帧进度）
curl -N -X POST http://localhost:8000/api/kb/documents -F "file=@/path/to/notes.md"

# 强制中断，验证回滚（服务端日志会打印「收到 asyncio.CancelledError」+ 清除段数）
curl -N -m 1 -X POST http://localhost:8000/api/kb/documents -F "file=@/path/to/big.pdf"

# 列表 / 删除
curl -s http://localhost:8000/api/kb/documents
curl -s -X DELETE http://localhost:8000/api/kb/documents/<doc_id>
```

实测一次成功上传的完整事件流：

```
event: progress
data: {"stage": "saved", "message": "已接收 notes.md (0.1 KB)"}

event: progress
data: {"stage": "parsed", "message": "解析出 23 字符"}

event: progress
data: {"stage": "split", "current": 1, "total": 1, "message": "切分为 1 段"}

event: progress
data: {"stage": "embedding", "current": 1, "total": 1, "message": "嵌入 1/1"}

event: done
data: {"doc_id": "d68ae53c-...", "filename": "notes.md", "chunks": 1,
       "replaced": false, "uploaded_at": "2026-09-13T04:42:36Z"}
```

### 限额

| 项 | 值 | 由谁强制 |
|---|---|---|
| 扩展名白名单 | `.md` / `.txt` / `.pdf`（大小写不敏感） | 前端预校验 + 后端 `415` |
| 单文件大小 | ≤ 20 MB（20971520 字节） | 前端预校验 + 后端 `413` |
| 单次选择文件数 / 并发上传数 | ≤ 5 / 1（串行） | 仅前端 |
| 嵌入批大小 | 10 段/批 | 后端（DashScope 上限 20，留余量） |
| 上传超时 | **不设 timeout** | 前端靠 `AbortController` 让用户取消 |

---

## 配置项

全部集中在 [`config.py`](config.py)，可用 `RAGQA_` 前缀的环境变量覆盖：

| 配置 | 默认值 | 环境变量 | 说明 |
|---|---|---|---|
| `deepseek_model` | `deepseek-chat` | `RAGQA_DEEPSEEK_MODEL` | 生成 / 评估 / 重写用 |
| `temperature` | `0.1` | `RAGQA_TEMPERATURE` | RAG 场景用低温度，减少编造 |
| `embed_model` | `qwen3.7-text-embedding` | `RAGQA_EMBED_MODEL` | 仅用于 `/api/health` 上报 |
| `top_k` | `4` | `RAGQA_TOP_K` | 每次检索返回段数 |
| `chunk_size` | `1000` | `RAGQA_CHUNK_SIZE` | 技术文档适用；200 会切碎语义且嵌入调用暴增 |
| `chunk_overlap` | `150` | `RAGQA_CHUNK_OVERLAP` | 相邻块重叠，避免语义被截断 |
| `max_rewrites` | `2` | `RAGQA_MAX_REWRITES` | 查询重写上限，防死循环 |
| `collection_name` | `langchain_docs` | `RAGQA_COLLECTION_NAME` | 预置与上传共用一个集合 |
| `chroma_dir` | `data/chroma_db` | `RAGQA_CHROMA_DIR` | 向量库持久化目录 |
| `docs_dir` | `data/langchain_docs` | `RAGQA_DOCS_DIR` | 预置文档目录 |
| `uploads_dir` | `data/uploads` | `RAGQA_UPLOADS_DIR` | 用户上传原件落盘处 |
| `mysql_database_url` | `""`（未配置） | `RAGQA_MYSQL_DATABASE_URL` | MySQL 异步连接串（P2 账号体系用）。默认空串 → 构造引擎前快速失败并点名该变量；**口令只放 .env，不进源码** |
| `sql_echo` | `False` | `RAGQA_SQL_ECHO` | 是否打印全部 SQL。SSE 场景下默认关，否则 SQL 日志会淹没应用日志 |

例：`RAGQA_TOP_K=6 python run.py "年假有几天？"`

---

## 测试

```bash
cd advanced_tutorial/rag_qa_project
python -m pytest tests -v          # 25 passed（11 个工作流 + 14 个上传链路），约 6 秒
```

```bash
cd frontend
npm run test                       # 55 passed（7 个文件，jsdom），约 4 秒
npm run build                      # tsc --noEmit + vite build，产物 ~497 KB JS（gzip 155 KB）
```

两个测试文件都是**完全离线**的，靠依赖注入把外部服务换掉：

- `test_graph.py`：`build_graph(model=..., retriever=...)` 接受任意模型与检索器，
  用 `FakeRAGModel`（按 prompt 内容路由到 grade / rewrite / generate 三种行为）+ `FakeRetriever` 验证图结构与路由分支。
- `test_kb_upload.py`：`DeterministicFakeEmbedding` 替换 DashScope、`tmp_path` 里的独立 Chroma 替换真实库、
  `monkeypatch settings.uploads_dir` 隔离落盘目录，用 `TestClient` 跑完整 SSE 时序。

---

## 开发约定（都是踩过的坑）

### 1. import 只用一个根：`rag_qa_project/`

后端内部模块**一律**写全 `backend.app.*` 前缀；只有 `config` 和 `ingest` 允许裸导入
（因为它们物理上就直接躺在源根下）。

```python
# ✅ 正确
from backend.app.agent.graph import get_vectorstore
from backend.tools.upload_function_tools import sse, err
from config import settings

# ❌ 错误 —— 运行时 ModuleNotFoundError（tools 物理在 backend/tools/，源根底下找不到它）
from agent.graph import get_vectorstore
from services.upload_service import sse_upload
from tools.upload_function_tools import sse
```

判别方法：**把 import 的第一段拿去 `rag_qa_project/` 底下找，找得到就合法，找不到就是根错了。**

为什么这条这么重要：Python 的绝对 import 只认 `sys.path`，而 `sys.path[0]` 由启动方式决定
（`python -m uvicorn` / `pytest` 都是当前工作目录）。所以**必须在 `rag_qa_project/` 目录下启动后端**。

⚠ **PyCharm 用户额外注意**：如果 `.iml` 里把 `backend/` 和 `backend/app/` 也标成了 Sources Root，
IDE 会把三个根都当解析路径，于是裸写法**在编辑器里不报红、一到命令行就炸**。更隐蔽的后果是同一个 `.py`
被加载成两个互不相干的模块对象（`sys.modules` 里 `agent.graph` 和 `backend.app.agent.graph` 并存）：

- `get_vectorstore()` 的 `@lru_cache` 单例失效 → 开出两个 Chroma 客户端 + 两个嵌入实例，
  一份代码写进去的向量另一份的 retriever 看不见（症状：上传成功但搜不到）
- 模块级状态双份 → `monkeypatch` 只打中一半，测试假绿/假红

请只保留 `advanced_tutorial/rag_qa_project` 一个 Sources Root。

### 2. 同步阻塞活儿一律丢 `asyncio.to_thread`

解析、切分、嵌入、Chroma 读写全是同步阻塞的。在异步生成器里直接调用会**占住整个事件循环**，
别的请求（包括正在跑的聊天流）全部卡死。

### 3. 不在 import 期碰向量库

`get_vectorstore()` 会加载 Chroma + DashScope 嵌入。写成模块级变量等于把重活提到进程启动，
知识库没入库时整个 app 都起不来。它是 `@lru_cache` 单例，**函数里现取即可，开销只是一次字典查找**。

同理：`@lru_cache` 不能装饰带 `embeddings` 参数的函数——LangChain 的嵌入对象继承自 pydantic
`BaseModel`，不可哈希，会抛 `TypeError: unhashable type`。改为无参、在函数内部构建。

### 4. Chroma 的两条硬约束

- `add_documents()` **只接受 `Document` 对象**。直接喂 `list[str]` 会抛
  `AttributeError: 'str' object has no attribute 'id'`。纯文本必须自己包 `Document(page_content=..., metadata=...)`。
- `where` 里的 `$and` / `$or` **至少要两个子条件**。单条件必须直接写 `{"origin": "upload"}`，
  包一层 `$and` 会抛 `ValueError: ... at least two where expressions`。

另外：`collection.get()` 的 `include` **必须显式给**。省略时 Chroma 会连 `documents` 一起返回，
上传几百段就是数 MB 无用数据。只要计数用 `include=[]`，要分组统计用 `include=["metadatas"]`。
`delete(where=...)` 未命中是静默 no-op，且不返回条数——要拿删除数得先 `get` 数一遍 ids。

### 5. SSE 帧格式与响应头

```python
f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"   # 空行结束一帧
```

响应头必须含 `X-Accel-Buffering: no`，否则 nginx / 反向代理会把帧憋住，失去流式效果。

### 6. 路由函数必须 `return` 响应对象

`return await upload_service.sse_upload(file)` —— 漏掉 `return` 会把 `StreamingResponse` 丢弃，前端只能拿到 `null`。

---

## 调试手段

1. **结构可视化**：`python run.py --graph` 输出 ASCII 架构图。
2. **步骤级调试**：`--debug` 以 `stream_mode="updates"` 打印每个节点执行后的状态增量。
3. **节点内日志**：`graph.py` 各节点内置 `print`（检索命中标题、评分保留数、重写内容），
   `upload_service.py` 打印入库完成 / 回滚 / 各类中断异常。
4. **LangSmith 追踪**：`.env` 里 `LANGSMITH_TRACING=true`，每次运行自动上报，
   可在 <https://smith.langchain.com> 查看完整调用树、token 消耗与每个节点的输入输出。
5. **前端 mock**：`npm run mock` 起一个假后端，把前后端联调和后端开发解耦。
6. **教学 demo**：`backend/_sse_upload_demo.py` 是不依赖 LangChain/Chroma 的最小 SSE 上传实现，
   专门用来观察「客户端中断时异步生成器到底收到哪个异常」：
   `python -m uvicorn backend._sse_upload_demo:app --port 8010`

---

## FAQ

**Q1：`certificate verify failed ... huggingface.co`？**
`HF_ENDPOINT` 镜像必须在任何 langchain / chromadb / huggingface_hub 导入**之前**设置
（`huggingface_hub` 会在首次导入时固化 endpoint）。现已在 `config.py` 顶部处理，
所有入口 import config 时都会生效。当前嵌入走 DashScope 云端 API，一般不会再触发 HF 下载。

**Q2：回答只涵盖资料的一部分？**
切分粒度 + 相关性过滤的典型现象。改进方向：调大 `RAGQA_TOP_K`、调大 `RAGQA_CHUNK_SIZE`，
或放宽 grade 的判定标准（`config.py` 的 `grade_strict`）。

**Q3：一直触发 rewrite 循环？**
`max_rewrites=2` 是防死循环安全阀；耗尽后走 generate 的「未找到」兜底分支，不会无限循环。

**Q4：上传成功但问答搜不到新内容？**
先确认没有踩「开发约定 §1」的重复模块坑（两个 `get_vectorstore` 单例 → 写入和检索用的不是同一个 Chroma 实例）。
用 `curl /api/health` 看 `kb_count` 有没有涨，再用 `GET /api/kb/documents` 确认 `origin=upload` 的块在不在。

**Q5：怎么不花 API 额度做回归测试？**
见「测试」一节。`build_graph(model=..., retriever=...)` 和 `monkeypatch function_tools.get_vectorstore`
是两个注入点，25 个后端用例全部离线跑通。

**Q6：换其他 LLM / 其他向量库？**
`graph.py` 的 `get_model()` / `get_vectorstore()` / `get_retriever()` 是仅有的三处基础设施绑定，替换它们即可。

**Q7：`data/knowledge.json` 是什么？**
早期「公司制度问答」版本的遗留示例数据，**当前代码没有任何地方引用它**，可以忽略或删除。

---

## 已知问题

以下是实测发现、尚未修复的：

1. **兜底分支不产生 `token` 帧。** `graph.py` 的 `generate` 节点在 `documents` 为空时直接
   `return {"generation": "知识库中未找到…"}`，**没有调用 model**，因此 `stream_mode="messages"`
   一个 token 都不推。前端收到「8 个 step + 空 sources + done」，答案气泡是空白的，
   而不是显示那句兜底提示。
   实测事件序列：`['step']×8 → 'sources' → 'done'`，`token` 帧数 = 0。
   修法：兜底分支也走一次 model（或让 `chat_service` 在 `done` 前补发一帧 `token`）。

2. **检索召回质量偏低。** 问「RecursiveCharacterTextSplitter 怎么切分 markdown」时，
   `top_k=4` 命中的全是 *Build a semantic search engine* / *Build a custom RAG agent* 这类教程页，
   grade 连续三轮判 0/4 相关，重写耗尽后走兜底。
   可调方向：加大 `top_k`、给检索器加重排序（MMR / rerank）、或检查嵌入模型对代码类术语的表现。

---

## 与教程的关系

本项目是 `advanced_tutorial/` 模块 2 的实践载体：

- 模块 1（`01`–`03` notebook）讲解其中用到的 LangChain / LangGraph 核心概念
- 模块 3（`04`–`05` notebook）深入本项目用到的流式输出、回调、自定义工具与错误处理
- 模块 4（`06` notebook）以本项目为例讲解模块化、配置、测试与性能的工程化最佳实践
- `10_RAG进阶检索三件套.ipynb` 对应本项目的检索质量优化方向（见「已知问题 §2」）
