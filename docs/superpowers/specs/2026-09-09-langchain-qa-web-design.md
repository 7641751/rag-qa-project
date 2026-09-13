# LangChain 智能问答 Web 项目 · 设计文档（Spec）

- **日期**：2026-09-09
- **项目**：`advanced_tutorial/rag_qa_project`
- **本次范围**：接口契约文档 + 前端（**后端由用户实现**）
- **状态**：设计已与用户逐节确认（架构 / 接口契约 / 前端结构 / 错误处理·测试·分工）

---

## 1. 背景与目标

### 现状
- `rag_qa_project` 是 CLI 版 **CRAG（纠错式检索增强）** 问答：
  - `graph.py`：LangGraph `StateGraph`，流程 `retrieve → grade_documents → decide → generate / rewrite_query`。
  - `ingest.py`：加载 88 篇 `langchain_docs` markdown + DashScope 嵌入（`qwen3.7-text-embedding`，dim=1024）+ Chroma 分批入库。
  - `config.py`：`pydantic-settings`，环境变量前缀 `RAGQA_`。
  - `run.py`：CLI 入口，已用 `stream_mode=["updates","messages"]` 双流。
  - `tests/test_graph.py`：11 个离线测试（FakeLLM + 内存检索器）。
- 知识库已入库（Chroma，`data/chroma_db`，collection `langchain_docs`）。
- `backend/app/api/main.py`：已有 FastAPI + CORS（放行 Vite 端口 5173/4173）骨架，**无任何路由**。
- `frontend/`：空。

### 目标
把该项目重构为**部署在网页**的 LangChain 智能问答应用：浏览器提问 → 后端驱动 CRAG 图 → **SSE 流式**回传推理步骤 + 答案 + 来源，支持**服务端多轮记忆**。

### 分工
- **用户（后端）**：FastAPI 端点 + SSE 映射 + `graph.py` 改造（checkpointer / messages 通道）+ 后端测试。
- **AI（本次交付）**：接口契约文档（Markdown + OpenAPI + SSE 样例）+ 完整前端（React + Vite + TS + Tailwind）+ 前端测试。

---

## 2. 技术选型（已确认）

| 维度 | 决策 |
|---|---|
| 前端框架 | React + Vite + TypeScript |
| 样式 | Tailwind CSS |
| 交互 | 全功能 SSE 流式（`step` / `token` / `sources` / `done` / `error`） |
| 会话记忆 | 服务端 `thread_id`（LangGraph checkpointer） |
| 界面布局 | A：单栏 + 内联可折叠步骤 + 来源芯片 |
| 契约文档 | Markdown + OpenAPI 3.1 YAML + 真实 SSE 样例流 |
| 部署 | dev：Vite proxy → `:8000`；prod：`npm run build` → FastAPI `StaticFiles` 单容器 |

---

## 3. 架构与数据流

**一次问答：**
1. 前端 `POST /api/chat/stream`，body `{ question, thread_id }`。
2. 后端在 `lifespan` 内**只建一次**图 + checkpointer（单例复用；加载 Chroma / 嵌入 / LLM 很重，**禁止每请求重建**）。
3. `graph.astream(state, config={"configurable": {"thread_id": …}}, stream_mode=["updates","messages"])` 驱动图。
4. 后端把输出**映射成 SSE 事件**：
   - `updates` → `step`
   - `messages` 中 **仅 `generate` 节点**的 token → `token`（**必须过滤** `grade` / `rewrite` 的 JSON 结构化输出）
   - 最终检索文档 → `sources`
   - 结束 → `done`；异常 → `error`
5. 前端 `fetch + ReadableStream` 解析，按事件增量渲染（打字机答案 / 步骤条 / 来源芯片）。

**页面加载：** 前端按当前 `thread_id` 调 `GET /api/chat/history` 回填历史消息。

**部署形态：**
- **开发**：Vite `:5173` 用 proxy 把 `/api` 转发到 uvicorn `:8000`（免 CORS；现有 CORS 放行作兜底）。
- **生产**：`npm run build` → `dist/`，由 FastAPI `StaticFiles` 挂载 → **单容器单端口**，前端同源请求 `/api`。

---

## 4. 接口契约

### 4.1 端点清单

| 方法 | 路径 | 用途 | 响应类型 |
|---|---|---|---|
| `POST` | `/api/chat/stream` | 提问并流式作答 | `text/event-stream` |
| `GET` | `/api/chat/history` | 拉取会话历史（`?thread_id=`） | `application/json` |
| `GET` | `/api/health` | 健康检查 / 就绪探针 | `application/json` |

> 用 `POST` 而非 `GET` 提问：中文长问题走 body，避开 URL 长度 / 编码限制。v1 就这三个端点（`thread_id` 前端生成、单会话，故不需要“建会话 / 列会话”接口）。

### 4.2 `POST /api/chat/stream`

**请求体**（`application/json`）：

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `question` | string | 必填，长度 1..4000 | 用户问题 |
| `thread_id` | string(uuid) | 必填 | 会话 ID，前端 `crypto.randomUUID()` 生成 |

**响应**：`200 text/event-stream`，事件见 4.3。
**流开始前的错误**：`422`（body 不合法，Pydantic 自动）、`503`（知识库未就绪）。

### 4.3 SSE 事件协议（5 类）

**帧格式**（每条以空行结束）：
```
event: <type>
data: <json>

```

| event | 时机 | data 载荷 |
|---|---|---|
| `step` | 每个 CRAG 节点**完成**时 | `{"node","label","detail"}` |
| `token` | `generate` 节点每吐一段 | `{"text"}` |
| `sources` | 生成结束前，一次性 | `{"sources":[{"title","snippet","score"}]}` |
| `done` | 正常结束 | `{"thread_id","rewrites"}` |
| `error` | 流中途出错 | `{"code","message"}` |

**字段定义：**
- `step.node` ∈ `{retrieve, grade_documents, rewrite_query, generate}`（与 `graph.py` 节点名一致）。
- `step.label`：中文短标签（检索 / 评分 / 重写 / 生成）。
- `step.detail`：可选摘要（如“召回 8 段”“相关 6/8”）。
- `token.text`：字符串，**仅来自 `generate` 节点**。
- `sources[].title`：文档名 / 标题；`snippet`：可选摘录；`score`：可选相关性（`number` 或 `null`）。
- `done.rewrites`：本次发生的查询重写次数（整数 ≥ 0）。
- `error.code` ∈ `{VALIDATION_ERROR, RETRIEVAL_ERROR, LLM_ERROR, INTERNAL_ERROR}`；`error.message`：可读中文。

**样例流：**
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
data: {"text":"用 RecursiveCharacterTextSplitter…"}

event: sources
data: {"sources":[{"title":"01_文档加载与文本分割.md","snippet":"RecursiveCharacterTextSplitter.from_language(...)","score":0.82}]}

event: done
data: {"thread_id":"550e8400-e29b-41d4-a716-446655440000","rewrites":0}
```

**约定：**
- `step` 时序（重要）：`retrieve` / `grade_documents` / `rewrite_query` 由 `updates` 在节点**完成**时触发，均在答案 `token` 之前；`generate` 阶段**可选**地由后端在转发**首个 `token` 之前**补发一次 `step`（标记“开始作答”，样例流即此写法），若不发则前端把首个 `token` 视为生成开始。“进行中”提示由前端在收到首个事件前显示通用「思考中…」。
- 一次成功响应的事件序：若干 `step` → 若干 `token` → `sources` → `done`。
- 出错：发 `error` 后关闭流（不再有 `done`）。

### 4.4 `GET /api/chat/history`

**Query**：`thread_id`（必填，uuid）。

**响应** `200 application/json`：
```json
{
  "thread_id": "550e8400-e29b-41d4-a716-446655440000",
  "messages": [
    { "role": "user",      "content": "如何切分 markdown?" },
    { "role": "assistant", "content": "答案全文…", "sources": [ {"title":"01_文档加载.md","snippet":"…","score":0.82} ] }
  ]
}
```
- `role` ∈ `{user, assistant}`。
- `assistant.sources` **可选**：需后端把来源随 `AIMessage`（如 `additional_kwargs`）持久化才返回；否则省略，刷新后不显示来源芯片。
- `steps` 是瞬时的，**不进 history**。
- **新 / 未知 `thread_id` → `200` + 空 `messages`**（不报 404，新建会话本就无历史）。
- 后端实现：`graph.get_state(config={"configurable":{"thread_id":…}}).values["messages"]` 映射为上述结构。

### 4.5 `GET /api/health`

**响应** `200 application/json`：
```json
{ "status": "ok", "kb_count": 2885, "model": "qwen3.7-text-embedding" }
```
- 至少含 `status`；`kb_count`（向量条数）、`model`（嵌入 / LLM 标识）便于排障，可选。

### 4.6 错误处理

- **流开始前**：标准 HTTP 状态码（`422` 校验、`503` KB 未就绪、`500` 内部）。错误体统一 `{"code","message"}`。
- **流开始后**（已发 `200` + 响应头，无法改状态码）：发 `event: error`（`code` 同上枚举）再关闭流。
- **禁止**返回 `200` 塞错误文本（反模式）。

### 4.7 响应头（stream 端点）
```
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive
X-Accel-Buffering: no
```
> `X-Accel-Buffering: no` 关掉 nginx / 代理缓冲，否则 token 会被憋住不流式。

### 4.8 OpenAPI 说明
- `openapi.yaml` 覆盖三个端点；SSE 端点 `responses` 标 `text/event-stream`，并用 `openapi_extra` / `description` 写明事件协议（Swagger UI 无法演示流式）。
- 请求 / 响应 / 事件载荷均定义 JSON Schema；`history` / `health` 用 `response_model`，让 `/docs` 展示 schema。

---

## 5. 前端设计（React + Vite + TS + Tailwind）

### 5.1 目录结构
```
frontend/
├── index.html
├── package.json
├── vite.config.ts          # proxy /api → :8000
├── tailwind.config.js
├── postcss.config.js
├── tsconfig.json
└── src/
    ├── main.tsx            # React 入口
    ├── App.tsx             # 布局 A 单栏容器
    ├── index.css           # Tailwind directives + 全局样式
    ├── api/
    │   ├── types.ts        # 契约类型（镜像 4.3 的 5 类事件 + Step/Source）
    │   └── client.ts       # streamChat(): fetch + ReadableStream 解析 SSE
    ├── hooks/
    │   ├── useChat.ts      # 会话状态机: messages / send / abort / newChat / loadHistory
    │   └── useThreadId.ts  # thread_id 生成 + localStorage 持久化
    └── components/
        ├── Header.tsx          # 标题 · 新对话
        ├── ChatWindow.tsx      # 消息列表 · 自动滚底
        ├── MessageBubble.tsx   # 单条: user / assistant
        ├── StepTrace.tsx       # 可折叠 CRAG 步骤条
        ├── AnswerMarkdown.tsx  # 流式答案 · markdown + 代码高亮
        ├── SourceChips.tsx     # 来源芯片
        └── Composer.tsx        # 输入框 · 发送/停止
```

### 5.2 组件树
```
App
├── Header            [标题 · 新对话按钮]
├── ChatWindow        [消息列表 · 自动滚到底]
│   └── MessageBubble (×N)
│       ├── (user)      纯文本气泡
│       └── (assistant) StepTrace + AnswerMarkdown + SourceChips
└── Composer          [输入框 · 发送/停止]
```
**不引外部状态库**（zustand / redux）：单会话用 `useChat` 内的 `useReducer` 足够（YAGNI）。

### 5.3 状态模型（`useChat.ts`）
```ts
type Step   = { node: string; label: string; detail?: string };
type Source = { title: string; snippet?: string; score?: number | null };
type Msg =
  | { role: "user"; content: string }
  | { role: "assistant"; steps: Step[]; answer: string;
      sources: Source[]; status: "streaming" | "done" | "error"; error?: string };
```
- `send(q)`：压入 user 消息 + 一条空 assistant（`status:"streaming"`）→ 调 `streamChat` → 按事件**增量更新最后一条** assistant（step 追加、token 拼接、sources 落位、done/error 收尾）。
- `abort()`：`AbortController` 取消 fetch =「停止生成」，保留已生成部分并标 `done`。
- `newChat()`：清空 messages + 换新 `thread_id`。
- `loadHistory()`：挂载时按 `thread_id` 拉历史回填（见 5.6）。

### 5.4 SSE 消费（`api/client.ts`）——前端最易出 bug 处
用 `fetch(POST) + res.body.getReader() + TextDecoder` 手动解析（**POST 流式不能用 `EventSource`**）。核心是**处理粘包 / 半包**：
```
buffer += decoder.decode(chunk, { stream: true })
按 "\n\n" 切帧 → 只处理完整帧，残帧留在 buffer 等下次
每帧取 event: / data: → JSON.parse(data) → 按类型回调 onStep / onToken / onSources / onDone / onError
```
该模块配**单元测试**（见第 8 节）覆盖各种分片 / 粘包样本。

### 5.5 Markdown + 代码高亮
`react-markdown` + `remark-gfm`（表格）+ `rehype-highlight`（highlight.js 代码高亮）。流式时每来一个 token 追加 `answer` 并重渲染；`react-markdown` 对“半截 markdown”（如 ``` 只来一半）容错，基本无碍。

### 5.6 thread_id + history 生命周期
- `useThreadId`：首访读 localStorage `ragqa_thread_id`，无则 `crypto.randomUUID()` 生成并存；「新对话」换新 UUID。
- `useChat.loadHistory()`：挂载时 `GET /api/chat/history?thread_id=<current>` 回填消息。
- **messages 不存 localStorage**（后端 checkpointer 为单一真相源）；刷新后靠 history 接口恢复显示，与后端记忆一致。

---

## 6. 后端实现要求（契约约束，用户实现）

1. **`graph.py` 改造**（支持服务端多轮记忆）：
   - `compile(checkpointer=<MemorySaver（dev）/ SqliteSaver（持久）>)`。
   - `RAGState` 增 `messages` 通道（`Annotated[list, add_messages]`），并把历史喂进 `generate` 提示词。
2. **SSE 映射**：`astream(stream_mode=["updates","messages"])` 双流 → 事件；**过滤只流 `generate` 节点 token**（头号坑：grade/rewrite 的 JSON 不能混入答案）。
3. **三端点**：`POST /api/chat/stream`、`GET /api/chat/history`、`GET /api/health`；统一错误体 `{"code","message"}`；stream 响应头含 `X-Accel-Buffering: no`。
4. **history**：从 checkpointer `get_state` 读 `messages` 映射；未知 thread → 空列表。
5. **单例**：`lifespan` 内建一次图 + checkpointer，全局复用。

---

## 7. 错误处理（端到端）

| 场景 | 前端表现 |
|---|---|
| 后端不可达（fetch 抛错） | 顶部提示“无法连接后端”，该条消息标 `error` + 重试按钮 |
| HTTP 4xx/5xx（流开始前） | 读 `res.status` + body：422 校验失败、503 KB 未就绪，分别提示 |
| SSE `error` 事件（流中途） | 按 `code` 显示 `message`，消息标 `error`，已收 token 保留 |
| 用户点「停止」 | `abort()` → 保留已生成部分，标 `done` |
| 空 / 超长问题 | 前端先校验（1..4000），不发请求 |

统一错误体 `{"code","message"}`；**绝不**用 200 塞错误文本。

---

## 8. 测试策略

- **前端（AI 交付，带 Vitest）**：
  - ⭐ **SSE 解析器单测**：喂完整帧 / 粘包 / 半包 / 多事件样本，断言回调正确——最易出 bug，重点覆盖。
  - `useChat` 状态机：模拟事件序列，断言 messages 增量更新、status 流转、abort / newChat / loadHistory。
  - 组件冒烟（React Testing Library）：`MessageBubble` / `StepTrace` / `SourceChips` 渲染。
- **契约对齐**：用录制的样例流跑前端解析；后端拿契约里的 SSE 样例自测输出格式，双向对齐。
- **后端（用户）**：复用现有 `test_graph.py`（11 个离线测试）+ 新增 SSE 映射测试（用 fake graph 断言事件序列）。

---

## 9. 部署

- **dev**：`vite :5173` + `uvicorn :8000`，Vite proxy `/api` → `:8000`（CORS 兜底）。
- **prod**：`npm run build` → `dist/`，FastAPI `StaticFiles` 挂载，单容器单端口，同源 `/api`。
- **密钥**：走 env（`DASHSCOPE_API_KEY` / `DEEPSEEK_API_KEY` 等），不入库。
- **数据**：Chroma 目录作为持久卷挂载。
- **忽略**：`.superpowers/` 加入 `.gitignore`（可视化伴侣的 mockup 缓存）。

---

## 10. 交付物清单 & 分工

**AI 交付：**
1. **接口契约文档** → `docs/api/README.md`（人读）+ `docs/api/openapi.yaml`（机器可读，可导入 Postman/Apifox）+ `docs/api/sse-example.txt`（真实样例流）。
2. **完整前端** → `frontend/`（Vite + React + TS + Tailwind）：SSE 消费、`useChat` / `useThreadId`、7 个组件、Markdown + 高亮、history 回填、Vitest 测试。
3. `frontend/README.md`：dev（proxy → `:8000`）/ build / test 指南。

**用户交付（后端）：**
1. `POST /api/chat/stream`（SSE 映射，**过滤只流 generate token**）。
2. `GET /api/chat/history?thread_id=`。
3. `GET /api/health`。
4. `graph.py` 改造：`checkpointer` + `messages` 通道 + 历史喂进 generate。
5. 后端测试。

**边界铁律**：契约文档是双方**唯一真相源**；字段 / 事件要改，**先改文档再改代码**。

---

## 11. 新增文件落位

- `docs/superpowers/specs/2026-09-09-langchain-qa-web-design.md`（本文件）
- `docs/api/README.md`、`docs/api/openapi.yaml`、`docs/api/sse-example.txt`
- `frontend/`（完整脚手架 + `src/...`）

---

## 12. 附录：相关遗留项（**不在**本次前端 + 契约交付内，建议后端顺带处理）

- `graph.py` 的 `generate` 提示词仍是“公司制度问答 / 联系 HR”，与 `langchain_docs` KB 不符 → 建议改为“LangChain / LangGraph 文档助手”。
- `config.py`：`embed_model` 注释误写“本地 bge，离线可用”（实为 DashScope）；`grade_strict` 定义但未被 `graph.py` 使用（死配置）。
- `requirements.txt`：缺 `fastapi` / `uvicorn` / `dashscope` / `sse-starlette`；`langchain-huggingface` + `sentence-transformers` 为 bge 时代遗留（现用 DashScope）。
- `README.md`：过时（仍描述“公司制度 / bge / knowledge.json”）。
