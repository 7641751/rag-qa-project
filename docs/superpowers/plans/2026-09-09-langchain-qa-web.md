# LangChain 智能问答 Web 项目 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付「接口契约文档 + React 前端」，把 `rag_qa_project` 变成网页版 LangChain 智能问答；后端（FastAPI + SSE + graph 改造）由用户按本契约实现。

**Architecture:** 前端是 Vite + React + TS + Tailwind 的单栏聊天 SPA，用 `fetch + ReadableStream` 消费后端 SSE（`step`/`token`/`sources`/`done`/`error`），`thread_id` 驱动服务端多轮记忆，挂载时拉 `history` 回填。契约文档（Markdown + OpenAPI + SSE 样例）是前后端唯一真相源。前端配一个 mock SSE 服务，可在真实后端就绪前独立联调。

**Tech Stack:** React 18、Vite 5、TypeScript 5、Tailwind CSS 3、react-markdown + remark-gfm + rehype-highlight、Vitest + @testing-library/react、OpenAPI 3.1、npm（Node v22，Windows）。

## Global Constraints

- 端点固定三个，一律 `/api` 前缀、无版本前缀：`POST /api/chat/stream`、`GET /api/chat/history?thread_id=`、`GET /api/health`。
- SSE 事件固定 5 类：`step` / `token` / `sources` / `done` / `error`；帧格式 `event: <type>\ndata: <json>\n\n`（空行结束）。
- `step.node` ∈ `{retrieve, grade_documents, rewrite_query, generate}`；`token.text` 仅来自 `generate` 节点。
- `error.code` ∈ `{VALIDATION_ERROR, RETRIEVAL_ERROR, LLM_ERROR, INTERNAL_ERROR}`。
- 请求体 `{ question: string(1..4000), thread_id: uuid }`；`thread_id` 由前端 `crypto.randomUUID()` 生成，存 localStorage 键 `ragqa_thread_id`。
- 布局 A：单栏 + 内联可折叠步骤 + 来源芯片；不引外部状态库（用 `useReducer`）。
- 前端**不**持久化 messages（后端 checkpointer 为单一真相源）；挂载时 `GET /api/chat/history` 回填。
- 开发：Vite `:5173` proxy `/api` → `:8000`；生产：`npm run build` → `dist/`，FastAPI `StaticFiles` 单容器同源。
- 后端（FastAPI 端点 / SSE 映射 / `graph.py` 的 checkpointer + messages 改造）由**用户实现**，不在本计划任务内。
- 目录根：`D:\PycharmProjects\my_langchain_demo\advanced_tutorial\rag_qa_project`（下称 `<ROOT>`）。前端在 `<ROOT>/frontend`，契约文档在 `<ROOT>/docs/api`。
- 本项目无 git 仓库（用户已确认跳过 git）；各任务的“Commit”步骤改为**验证步骤**（跑测试 / 构建），不执行 `git commit`。

## Scope

本计划只覆盖 AI 的两个交付物：**接口契约文档**（Task 1）与**前端**（Task 2–7）。7 个任务，每个以“可独立测试的产物”收尾：

| Task | 交付物 | 独立验证方式 |
|---|---|---|
| 1 | `docs/api/`（README + openapi.yaml + sse-example.txt） | openapi.yaml 结构校验通过 |
| 2 | 前端脚手架 + 布局壳 + proxy + README | `npm run build` 通过、`npm run dev` 显示布局 A 空壳 |
| 3 | `api/types.ts` + `api/client.ts`（SSE 解析）+ 单测 | Vitest：解析器过粘包/半包/多事件用例 |
| 4 | `hooks/useThreadId.ts` + `hooks/useChat.ts` + 单测 | Vitest：状态机 send/abort/newChat/loadHistory |
| 5 | 展示组件 StepTrace/AnswerMarkdown/SourceChips/MessageBubble + 测试 | Vitest + RTL：渲染断言 |
| 6 | 组装 App/ChatWindow/Composer/Header 接通 useChat | 构建通过 + 组件集成测试 |
| 7 | mock SSE 服务 + 端到端联调 | UI 对 mock 跑通完整问答流 |

## File Structure

```
<ROOT>/docs/api/
├── README.md            # 人读契约: 端点/字段/SSE 事件/错误码/样例(源自 spec §4)
├── openapi.yaml         # 机器可读契约(OpenAPI 3.1), 可导入 Postman/Apifox
└── sse-example.txt      # 一段真实 SSE 样例流(逐行 event/data)

<ROOT>/frontend/
├── package.json         # 依赖 + scripts(dev/build/test/preview/mock)
├── vite.config.ts       # React 插件 + proxy /api→:8000 + build.outDir
├── tsconfig.json        # 严格模式 TS 配置
├── tailwind.config.js   # content 扫描 src
├── postcss.config.js    # tailwindcss + autoprefixer
├── index.html           # Vite 入口 HTML
├── README.md            # 前端 dev/build/test 指南
├── mock/server.mjs      # mock SSE 服务(无真实后端时联调用)
└── src/
    ├── main.tsx         # React 挂载入口
    ├── index.css        # Tailwind directives + 全局样式
    ├── App.tsx          # 布局 A 单栏容器, 组装 Header/ChatWindow/Composer
    ├── api/
    │   ├── types.ts     # 契约类型: Step/Source/Msg + 5 类事件 + 请求/历史
    │   └── client.ts    # streamChat(SSE 解析) + fetchHistory + health
    ├── hooks/
    │   ├── useThreadId.ts  # thread_id 生成/localStorage/重置
    │   └── useChat.ts      # 会话状态机(useReducer)
    ├── components/
    │   ├── Header.tsx          # 标题 + 新对话
    │   ├── ChatWindow.tsx      # 消息列表 + 自动滚底
    │   ├── MessageBubble.tsx   # 单条(user/assistant 分派)
    │   ├── StepTrace.tsx       # 可折叠 CRAG 步骤条
    │   ├── AnswerMarkdown.tsx  # markdown + 代码高亮
    │   ├── SourceChips.tsx     # 来源芯片
    │   └── Composer.tsx        # 输入框 + 发送/停止
    └── test/
        ├── client.test.ts      # SSE 解析单测(Task 3)
        ├── useChat.test.ts     # 状态机单测(Task 4)
        ├── useThreadId.test.ts # thread_id 单测(Task 4)
        └── components.test.tsx # 组件渲染测试(Task 5)
```

---

### Task 1: 接口契约文档（`docs/api/`）

**Files:**
- Create: `<ROOT>/docs/api/openapi.yaml`
- Create: `<ROOT>/docs/api/sse-example.txt`
- Create: `<ROOT>/docs/api/README.md`

**Interfaces:**
- Produces: 三个契约文件，是 Task 3（前端类型/客户端）与用户后端的对齐依据。`openapi.yaml` 的 `components.schemas` 名称（`ChatRequest`/`StepEvent`/`TokenEvent`/`Source`/`SourcesEvent`/`DoneEvent`/`ErrorEvent`/`HistoryMessage`/`HistoryResponse`/`Health`/`ErrorBody`）将被 Task 3 的 `types.ts` 一一对应。

- [ ] **Step 1: 写 `docs/api/openapi.yaml`**（完整内容）

```yaml
openapi: 3.1.0
info:
  title: LangChain 智能问答 API
  version: 0.1.0
  description: >
    CRAG 问答后端契约。核心是 SSE 流式端点 /api/chat/stream；
    事件协议见其 responses.200.description 与 components.schemas 中的 *Event。
servers:
  - url: http://localhost:8000
    description: 本地后端(开发期由 Vite proxy 转发 /api)
tags:
  - name: chat
    description: 问答与会话
  - name: system
    description: 系统探针
paths:
  /api/chat/stream:
    post:
      tags: [chat]
      summary: 提问并流式作答(SSE)
      description: >
        返回 text/event-stream。帧格式 `event: <type>` 换行 `data: <json>` 换行换行。
        事件类型: step/token/sources/done/error。
        成功事件序: 若干 step → 若干 token → sources → done。
        step 中 retrieve/grade_documents/rewrite_query 在节点完成时发(均在 token 前);
        generate 步骤可选地在首个 token 前补发。token.text 仅来自 generate 节点。
        出错: 发 error 后关闭流(不再有 done)。
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: '#/components/schemas/ChatRequest' }
      responses:
        '200':
          description: SSE 事件流(见上)。各事件 schema 见 components.schemas 的 *Event。
          content:
            text/event-stream:
              schema: { type: string }
        '422': { $ref: '#/components/responses/ValidationError' }
        '503': { $ref: '#/components/responses/KBNotReady' }
  /api/chat/history:
    get:
      tags: [chat]
      summary: 拉取会话历史
      description: 新/未知 thread_id 返回 200 + 空 messages(不报 404)。steps 不入历史。
      parameters:
        - name: thread_id
          in: query
          required: true
          schema: { type: string, format: uuid }
      responses:
        '200':
          description: 历史消息
          content:
            application/json:
              schema: { $ref: '#/components/schemas/HistoryResponse' }
  /api/health:
    get:
      tags: [system]
      summary: 健康检查
      responses:
        '200':
          description: 服务状态
          content:
            application/json:
              schema: { $ref: '#/components/schemas/Health' }
components:
  schemas:
    ChatRequest:
      type: object
      required: [question, thread_id]
      properties:
        question: { type: string, minLength: 1, maxLength: 4000 }
        thread_id: { type: string, format: uuid }
    StepEvent:
      type: object
      required: [node, label]
      properties:
        node: { type: string, enum: [retrieve, grade_documents, rewrite_query, generate] }
        label: { type: string, description: 中文短标签(检索/评分/重写/生成) }
        detail: { type: string, description: 可选摘要(如"召回 8 段") }
    TokenEvent:
      type: object
      required: [text]
      properties: { text: { type: string } }
    Source:
      type: object
      required: [title]
      properties:
        title: { type: string }
        snippet: { type: string }
        score: { type: [number, 'null'] }
    SourcesEvent:
      type: object
      required: [sources]
      properties:
        sources: { type: array, items: { $ref: '#/components/schemas/Source' } }
    DoneEvent:
      type: object
      required: [thread_id, rewrites]
      properties:
        thread_id: { type: string, format: uuid }
        rewrites: { type: integer, minimum: 0 }
    ErrorEvent:
      type: object
      required: [code, message]
      properties:
        code: { type: string, enum: [VALIDATION_ERROR, RETRIEVAL_ERROR, LLM_ERROR, INTERNAL_ERROR] }
        message: { type: string }
    HistoryMessage:
      type: object
      required: [role, content]
      properties:
        role: { type: string, enum: [user, assistant] }
        content: { type: string }
        sources: { type: array, items: { $ref: '#/components/schemas/Source' } }
    HistoryResponse:
      type: object
      required: [thread_id, messages]
      properties:
        thread_id: { type: string, format: uuid }
        messages: { type: array, items: { $ref: '#/components/schemas/HistoryMessage' } }
    Health:
      type: object
      required: [status]
      properties:
        status: { type: string, example: ok }
        kb_count: { type: integer }
        model: { type: string }
    ErrorBody:
      type: object
      required: [code, message]
      properties:
        code: { type: string }
        message: { type: string }
  responses:
    ValidationError:
      description: 请求体校验失败
      content:
        application/json:
          schema: { $ref: '#/components/schemas/ErrorBody' }
    KBNotReady:
      description: 知识库未就绪
      content:
        application/json:
          schema: { $ref: '#/components/schemas/ErrorBody' }
```

- [ ] **Step 2: 写 `docs/api/sse-example.txt`**（完整内容，逐行 event/data，空行分帧）

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
data: {"thread_id":"550e8400-e29b-41d4-a716-446655440000","rewrites":0}
```

- [ ] **Step 3: 写 `docs/api/README.md`**（人读契约，章节结构固定如下，内容取自 spec §4）

必须包含以下章节（内容照 `<ROOT>/docs/superpowers/specs/2026-09-09-langchain-qa-web-design.md` §4 逐条落地，不得留空）：
1. `# LangChain 智能问答 API 契约` + 一句话简介 + Base URL（`http://localhost:8000`，开发期前端经 Vite proxy 访问 `/api`）。
2. `## 端点清单`：三端点表格（方法/路径/用途/响应类型），同 spec §4.1。
3. `## POST /api/chat/stream`：请求体字段表（question 1..4000、thread_id uuid）+ 响应 `text/event-stream` + 流前错误（422/503）。附 `curl -N` 示例：
   `curl -N -X POST http://localhost:8000/api/chat/stream -H "Content-Type: application/json" -d "{\"question\":\"如何切分 markdown?\",\"thread_id\":\"550e8400-e29b-41d4-a716-446655440000\"}"`
4. `## SSE 事件协议`：帧格式 + 5 类事件表（时机/字段）+ 字段定义（node 枚举、token 仅 generate、score 可选、error.code 枚举）+ 事件序 + step 时序说明（同 spec §4.3）。内嵌 `sse-example.txt` 的样例流。
5. `## GET /api/chat/history`：query、响应结构、role 枚举、sources 可选、steps 不入历史、未知 thread → 空 messages、后端实现提示（`graph.get_state(...).values["messages"]`）。
6. `## GET /api/health`：响应结构（至少 status）。
7. `## 错误处理`：两段式（流前 HTTP 状态码 + 统一错误体 `{code,message}`；流中 `error` 事件）；禁止 200 塞错误文本。
8. `## 响应头`：`Content-Type: text/event-stream` / `Cache-Control: no-cache` / `Connection: keep-alive` / `X-Accel-Buffering: no`。
9. `## 前端消费示例`：`fetch + ReadableStream`（说明为何不用 EventSource），指向 `frontend/src/api/client.ts`。
10. `## 变更约定`：契约为唯一真相源，改字段/事件先改本文档 + openapi.yaml 再改代码。

- [ ] **Step 4: 校验 openapi.yaml 结构**

Run（在 `<ROOT>` 下，用 node 内置能力做 YAML→基本结构检查；无需额外依赖时用 npx）：
`npx --yes @redocly/cli@latest lint docs/api/openapi.yaml`
Expected: 无 error（warning 可接受）；若网络不可用导致 npx 失败，则改用 `node -e "const s=require('fs').readFileSync('docs/api/openapi.yaml','utf8'); if(!/openapi: 3.1.0/.test(s)) throw new Error('bad'); console.log('openapi.yaml header OK', s.length, 'bytes')"`，Expected 输出 `openapi.yaml header OK ... bytes`。

- [ ] **Step 5: 交叉核对（验证步骤，替代 commit）**

人工核对：`openapi.yaml` 的 schema 名与 `README.md` 事件表、`sse-example.txt` 的字段三者一致（node 枚举、error.code 枚举、字段名逐一对照）。Expected: 三者字段/枚举完全一致，无遗漏。

---

### Task 2: 前端脚手架 + 布局壳（Vite + React + TS + Tailwind）

**Files:**
- Create: `<ROOT>/frontend/package.json`、`vite.config.ts`、`tsconfig.json`、`tailwind.config.js`、`postcss.config.js`、`index.html`、`README.md`
- Create: `<ROOT>/frontend/src/main.tsx`、`src/index.css`、`src/App.tsx`、`src/test/setup.ts`

**Interfaces:**
- Produces: 可 `npm run build` / `npm run dev` 的脚手架；`App.tsx` 为布局 A 静态壳（Task 6 会重写为接通 `useChat` 的版本）。`vite.config.ts` 的 proxy 把 `/api` → `http://localhost:8000`，`test` 段配置 Vitest（jsdom + globals + `src/test/setup.ts`）。

- [ ] **Step 1: 写 `frontend/package.json`**

```json
{
  "name": "ragqa-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "preview": "vite preview",
    "test": "vitest run",
    "test:watch": "vitest",
    "mock": "node mock/server.mjs"
  },
  "dependencies": {
    "highlight.js": "^11.10.0",
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-markdown": "^9.0.1",
    "rehype-highlight": "^7.0.0",
    "remark-gfm": "^4.0.0"
  },
  "devDependencies": {
    "@testing-library/jest-dom": "^6.4.0",
    "@testing-library/react": "^16.0.0",
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.0",
    "autoprefixer": "^10.4.0",
    "jsdom": "^24.1.0",
    "postcss": "^8.4.0",
    "tailwindcss": "^3.4.0",
    "typescript": "^5.5.0",
    "vite": "^5.4.0",
    "vitest": "^2.0.0"
  }
}
```

- [ ] **Step 2: 写 `frontend/vite.config.ts`**

```ts
/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': { target: 'http://localhost:8000', changeOrigin: true } },
  },
  build: { outDir: 'dist' },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
  },
});
```

- [ ] **Step 3: 写 `frontend/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "skipLibCheck": true,
    "types": ["vitest/globals", "@testing-library/jest-dom"]
  },
  "include": ["src"]
}
```

- [ ] **Step 4: 写 `frontend/tailwind.config.js` 与 `frontend/postcss.config.js`**

```js
// tailwind.config.js
/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: { extend: {} },
  plugins: [],
};
```

```js
// postcss.config.js
export default { plugins: { tailwindcss: {}, autoprefixer: {} } };
```

- [ ] **Step 5: 写 `frontend/index.html`**

```html
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>LangChain 智能问答</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 6: 写 `frontend/src/index.css`、`src/main.tsx`、`src/test/setup.ts`**

```css
/* src/index.css */
@tailwind base;
@tailwind components;
@tailwind utilities;
```

```tsx
// src/main.tsx
import React from 'react';
import ReactDOM from 'react-dom/client';
import 'highlight.js/styles/github.css';
import './index.css';
import App from './App';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
```

```ts
// src/test/setup.ts
import '@testing-library/jest-dom';

// jsdom 未实现 scrollIntoView，测试里桩掉，避免 ChatWindow（Task 6）自动滚动报错
Element.prototype.scrollIntoView = () => {};
```

- [ ] **Step 7: 写 `frontend/src/App.tsx`（布局 A 静态壳）**

```tsx
export default function App() {
  return (
    <div className="flex h-screen flex-col bg-gray-50">
      <header className="flex items-center justify-between border-b bg-white px-4 py-3">
        <h1 className="text-sm font-semibold text-gray-800">🤖 LangChain 智能问答</h1>
        <button className="rounded-md border px-3 py-1 text-xs text-gray-600 hover:bg-gray-100">
          ＋ 新对话
        </button>
      </header>
      <main className="flex flex-1 items-center justify-center overflow-y-auto p-4">
        <p className="text-sm text-gray-400">开始提问吧（脚手架占位，Task 6 接通）</p>
      </main>
      <footer className="border-t bg-white p-3">
        <div className="mx-auto flex max-w-3xl gap-2">
          <input className="flex-1 rounded-md border px-3 py-2 text-sm" placeholder="输入问题…" disabled />
          <button className="rounded-md bg-blue-600 px-4 py-2 text-sm text-white" disabled>发送</button>
        </div>
      </footer>
    </div>
  );
}
```

- [ ] **Step 8: 安装依赖**

Run（在 `<ROOT>/frontend`）：`npm install`
Expected: 生成 `node_modules/` 与 `package-lock.json`，无 error（若网络受限需配置 npm 镜像）。

- [ ] **Step 9: 构建验证**

Run：`npm run build`
Expected: `tsc --noEmit` 无类型错误 + Vite 产出 `dist/`，退出码 0。

- [ ] **Step 10: 起 dev 验证布局壳**

Run：`npm run dev`（后台）→ 浏览器开 `http://localhost:5173`
Expected: 看到布局 A 空壳（顶栏标题 + 「＋ 新对话」、中间占位文案、底部禁用输入框/发送）。验证后停止 dev。

- [ ] **Step 11: 写 `frontend/README.md`**

章节：`# 前端` 简介；`## 环境`（Node v22、npm）；`## 开发`（`npm install` → `npm run dev`，:5173，`/api` 经 proxy 到 :8000；后端未就绪时 `npm run mock` 起 mock SSE 服务）；`## 构建`（`npm run build` → `dist/`，生产由 FastAPI StaticFiles 挂载）；`## 测试`（`npm run test`）；`## 契约`（指向 `../docs/api/README.md`，改字段先改契约）。

- [ ] **Step 12: 验证（替代 commit）**

Run：`npm run build && npm run test`
Expected: build 退出码 0；test 此时无用例应报 “No test files found” 或 0 passed（Task 3 起加入用例）。

---

### Task 3: 契约类型 + SSE 客户端（`api/types.ts` + `api/client.ts`）⭐

**Files:**
- Create: `<ROOT>/frontend/src/api/types.ts`
- Create: `<ROOT>/frontend/src/api/client.ts`
- Test: `<ROOT>/frontend/src/test/client.test.ts`

**Interfaces:**
- Consumes: 无（契约类型镜像 Task 1 的 openapi.yaml schemas）。
- Produces:
  - 类型：`Step`、`Source`、`StepEvent`、`TokenEvent`、`SourcesEvent`、`DoneEvent`、`ErrorCode`、`ErrorEvent`、`ChatRequest`、`HistoryMessage`、`HistoryResponse`、`Health`、`StreamHandlers`。
  - 函数：`parseSSEFrame(frame: string): { event: string; data: string } | null`；`splitFrames(buffer: string): { frames: string[]; rest: string }`；`streamChat(body: ChatRequest, handlers: StreamHandlers & { signal?: AbortSignal }): Promise<void>`；`fetchHistory(threadId: string): Promise<HistoryResponse>`；`fetchHealth(): Promise<Health>`。Task 4/6 依赖这些签名。

- [ ] **Step 1: 写失败测试 `src/test/client.test.ts`**

```ts
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { parseSSEFrame, splitFrames, streamChat } from '../api/client';
import type { StepEvent, TokenEvent, DoneEvent, ErrorEvent } from '../api/types';

function mockStream(chunks: string[], init: { ok?: boolean; status?: number } = {}) {
  const enc = new TextEncoder();
  let i = 0;
  const res = {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    text: async () => '',
    body: {
      getReader: () => ({
        read: async () =>
          i < chunks.length
            ? { value: enc.encode(chunks[i++]), done: false }
            : { value: undefined, done: true },
      }),
    },
  };
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(res as unknown as Response));
}

beforeEach(() => vi.unstubAllGlobals());

describe('parseSSEFrame', () => {
  it('解析单帧 event+data', () => {
    expect(parseSSEFrame('event: token\ndata: {"text":"hi"}')).toEqual({ event: 'token', data: '{"text":"hi"}' });
  });
  it('多行 data 拼接', () => {
    expect(parseSSEFrame('data: a\ndata: b')!.data).toBe('a\nb');
  });
  it('无 data 返回 null', () => {
    expect(parseSSEFrame('event: ping')).toBeNull();
  });
});

describe('splitFrames', () => {
  it('粘包: 一个 buffer 两帧', () => {
    const { frames, rest } = splitFrames('event: a\ndata: 1\n\nevent: b\ndata: 2\n\n');
    expect(frames).toHaveLength(2);
    expect(rest).toBe('');
  });
  it('半包: 残帧留在 rest', () => {
    const { frames, rest } = splitFrames('event: a\ndata: 1\n\nevent: b\nda');
    expect(frames).toHaveLength(1);
    expect(rest).toBe('event: b\nda');
  });
});

describe('streamChat', () => {
  it('跨 chunk 粘包/半包仍能按序回调', async () => {
    mockStream([
      'event: step\ndata: {"node":"retrieve","la',
      'bel":"检索"}\n\nevent: token\ndata: {"text":"你"}\n\n',
      'event: done\ndata: {"thread_id":"t1","rewrites":0}\n\n',
    ]);
    const steps: StepEvent[] = []; const tokens: TokenEvent[] = []; let done: DoneEvent | null = null;
    await streamChat({ question: 'q', thread_id: 't1' }, {
      onStep: e => steps.push(e), onToken: e => tokens.push(e), onDone: e => { done = e; },
    });
    expect(steps).toEqual([{ node: 'retrieve', label: '检索' }]);
    expect(tokens).toEqual([{ text: '你' }]);
    expect(done).toEqual({ thread_id: 't1', rewrites: 0 });
  });
  it('error 事件回调 onError', async () => {
    mockStream(['event: error\ndata: {"code":"LLM_ERROR","message":"boom"}\n\n']);
    let err: ErrorEvent | null = null;
    await streamChat({ question: 'q', thread_id: 't' }, { onError: e => { err = e; } });
    expect(err).toEqual({ code: 'LLM_ERROR', message: 'boom' });
  });
  it('非 2xx 响应转 onError', async () => {
    mockStream([], { ok: false, status: 503 });
    const errs: ErrorEvent[] = [];
    await streamChat({ question: 'q', thread_id: 't' }, { onError: e => errs.push(e) });
    expect(errs[0]?.code).toBeTruthy();
    expect(errs[0]?.message).toContain('503');
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run（在 `<ROOT>/frontend`）：`npx vitest run src/test/client.test.ts`
Expected: FAIL —— 找不到模块 `../api/client`（尚未实现）。

- [ ] **Step 3: 写 `src/api/types.ts`**

```ts
export type StepNode = 'retrieve' | 'grade_documents' | 'rewrite_query' | 'generate';
export interface Step { node: StepNode; label: string; detail?: string; }
export interface Source { title: string; snippet?: string; score?: number | null; }
export type StepEvent = Step;
export interface TokenEvent { text: string; }
export interface SourcesEvent { sources: Source[]; }
export interface DoneEvent { thread_id: string; rewrites: number; }
export type ErrorCode = 'VALIDATION_ERROR' | 'RETRIEVAL_ERROR' | 'LLM_ERROR' | 'INTERNAL_ERROR';
export interface ErrorEvent { code: ErrorCode; message: string; }
export interface ChatRequest { question: string; thread_id: string; }
export type HistoryRole = 'user' | 'assistant';
export interface HistoryMessage { role: HistoryRole; content: string; sources?: Source[]; }
export interface HistoryResponse { thread_id: string; messages: HistoryMessage[]; }
export interface Health { status: string; kb_count?: number; model?: string; }
export interface StreamHandlers {
  onStep?: (e: StepEvent) => void;
  onToken?: (e: TokenEvent) => void;
  onSources?: (e: SourcesEvent) => void;
  onDone?: (e: DoneEvent) => void;
  onError?: (e: ErrorEvent) => void;
}
```

- [ ] **Step 4: 写 `src/api/client.ts`**

```ts
import type {
  ChatRequest, StreamHandlers, HistoryResponse, Health,
  StepEvent, TokenEvent, SourcesEvent, DoneEvent, ErrorEvent, ErrorCode,
} from './types';

const API_BASE = '/api';

export function parseSSEFrame(frame: string): { event: string; data: string } | null {
  let event = 'message';
  const dataLines: string[] = [];
  for (const raw of frame.split('\n')) {
    const line = raw.replace(/\r$/, '');
    if (!line || line.startsWith(':')) continue;
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''));
  }
  if (dataLines.length === 0) return null;
  return { event, data: dataLines.join('\n') };
}

export function splitFrames(buffer: string): { frames: string[]; rest: string } {
  const parts = buffer.split('\n\n');
  const rest = parts.pop() ?? '';
  return { frames: parts.filter(f => f.trim().length > 0), rest };
}

export async function streamChat(
  body: ChatRequest,
  handlers: StreamHandlers & { signal?: AbortSignal },
): Promise<void> {
  const res = await fetch(`${API_BASE}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(body),
    signal: handlers.signal,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    let code: ErrorCode = 'INTERNAL_ERROR';
    let message = `HTTP ${res.status}`;
    try { const j = JSON.parse(text); if (j.code) code = j.code; if (j.message) message = j.message; } catch { /* 非 JSON */ }
    handlers.onError?.({ code, message });
    return;
  }
  if (!res.body) { handlers.onError?.({ code: 'INTERNAL_ERROR', message: '响应无 body' }); return; }

  const dispatch = (frame: string) => {
    const parsed = parseSSEFrame(frame);
    if (!parsed) return;
    let data: unknown;
    try { data = JSON.parse(parsed.data); } catch { return; }
    switch (parsed.event) {
      case 'step': handlers.onStep?.(data as StepEvent); break;
      case 'token': handlers.onToken?.(data as TokenEvent); break;
      case 'sources': handlers.onSources?.(data as SourcesEvent); break;
      case 'done': handlers.onDone?.(data as DoneEvent); break;
      case 'error': handlers.onError?.(data as ErrorEvent); break;
      default: break;
    }
  };

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const { frames, rest } = splitFrames(buffer);
    buffer = rest;
    for (const f of frames) dispatch(f);
  }
  buffer += decoder.decode();
  if (buffer.trim()) for (const f of splitFrames(buffer + '\n\n').frames) dispatch(f);
}

export async function fetchHistory(threadId: string): Promise<HistoryResponse> {
  const res = await fetch(`${API_BASE}/chat/history?thread_id=${encodeURIComponent(threadId)}`);
  if (!res.ok) throw new Error(`history HTTP ${res.status}`);
  return (await res.json()) as HistoryResponse;
}

export async function fetchHealth(): Promise<Health> {
  const res = await fetch(`${API_BASE}/health`);
  if (!res.ok) throw new Error(`health HTTP ${res.status}`);
  return (await res.json()) as Health;
}
```

- [ ] **Step 5: 跑测试确认通过**

Run：`npx vitest run src/test/client.test.ts`
Expected: PASS —— 全部用例通过（parseSSEFrame 3、splitFrames 2、streamChat 3）。

- [ ] **Step 6: 验证（替代 commit）**

Run：`npm run build`
Expected: `tsc --noEmit` 无类型错误（types.ts/client.ts 严格模式通过），退出码 0。

---

### Task 4: 会话状态 hooks（`useThreadId` + `useChat`）

**Files:**
- Create: `<ROOT>/frontend/src/hooks/useThreadId.ts`
- Create: `<ROOT>/frontend/src/hooks/useChat.ts`
- Test: `<ROOT>/frontend/src/test/useThreadId.test.ts`、`<ROOT>/frontend/src/test/useChat.test.ts`

**Interfaces:**
- Consumes: Task 3 的 `streamChat` / `fetchHistory` 与类型 `Step`/`Source`/`ErrorEvent`。
- Produces:
  - `useThreadId(): { threadId: string; resetThreadId: () => string }`。
  - `useChat(): { messages: Msg[]; streaming: boolean; send(q: string): void; abort(): void; newChat(): void; threadId: string }`；导出类型 `Msg = UserMsg | AssistantMsg`（`AssistantMsg = { role:'assistant'; steps: Step[]; answer: string; sources: Source[]; status:'streaming'|'done'|'error'; error?: string }`，`UserMsg = { role:'user'; content: string }`）。Task 6 依赖此签名。

- [ ] **Step 1: 写失败测试 `src/test/useThreadId.test.ts`**

```ts
import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useThreadId } from '../hooks/useThreadId';

beforeEach(() => localStorage.clear());

describe('useThreadId', () => {
  it('首访生成并持久化 uuid', () => {
    const { result } = renderHook(() => useThreadId());
    expect(result.current.threadId).toMatch(/^[0-9a-f-]{36}$/);
    expect(localStorage.getItem('ragqa_thread_id')).toBe(result.current.threadId);
  });
  it('复用已存 thread_id', () => {
    localStorage.setItem('ragqa_thread_id', 'fixed-id');
    const { result } = renderHook(() => useThreadId());
    expect(result.current.threadId).toBe('fixed-id');
  });
  it('resetThreadId 换新并持久化', () => {
    const { result } = renderHook(() => useThreadId());
    const first = result.current.threadId;
    let second = '';
    act(() => { second = result.current.resetThreadId(); });
    expect(second).not.toBe(first);
    expect(localStorage.getItem('ragqa_thread_id')).toBe(second);
  });
});
```

- [ ] **Step 2: 写失败测试 `src/test/useChat.test.ts`**

```ts
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

const { streamChatMock, fetchHistoryMock } = vi.hoisted(() => ({
  streamChatMock: vi.fn(), fetchHistoryMock: vi.fn(),
}));
vi.mock('../api/client', () => ({
  streamChat: streamChatMock, fetchHistory: fetchHistoryMock,
}));

import { useChat } from '../hooks/useChat';

beforeEach(() => {
  localStorage.clear();
  streamChatMock.mockReset();
  fetchHistoryMock.mockReset();
  fetchHistoryMock.mockResolvedValue({ thread_id: 't', messages: [] });
});

describe('useChat', () => {
  it('send 压入 user+assistant 并按事件增量更新', async () => {
    streamChatMock.mockImplementation(async (_b: unknown, h: any) => {
      h.onStep({ node: 'retrieve', label: '检索' });
      h.onToken({ text: '你' }); h.onToken({ text: '好' });
      h.onSources({ sources: [{ title: 'a.md' }] });
      h.onDone({ thread_id: 't', rewrites: 0 });
    });
    const { result } = renderHook(() => useChat());
    await act(async () => { result.current.send('问题'); });
    const m = result.current.messages;
    expect(m).toHaveLength(2);
    expect(m[0]).toEqual({ role: 'user', content: '问题' });
    const a = m[1];
    if (a.role === 'assistant') {
      expect(a.steps).toEqual([{ node: 'retrieve', label: '检索' }]);
      expect(a.answer).toBe('你好');
      expect(a.sources).toEqual([{ title: 'a.md' }]);
      expect(a.status).toBe('done');
    }
    expect(result.current.streaming).toBe(false);
  });

  it('onError 标 error', async () => {
    streamChatMock.mockImplementation(async (_b: unknown, h: any) => h.onError({ code: 'LLM_ERROR', message: 'boom' }));
    const { result } = renderHook(() => useChat());
    await act(async () => { result.current.send('q'); });
    const a = result.current.messages[1];
    if (a.role === 'assistant') { expect(a.status).toBe('error'); expect(a.error).toBe('boom'); }
  });

  it('空问题不发送', async () => {
    const { result } = renderHook(() => useChat());
    await act(async () => { result.current.send('   '); });
    expect(streamChatMock).not.toHaveBeenCalled();
    expect(result.current.messages).toHaveLength(0);
  });

  it('挂载时拉历史回填', async () => {
    fetchHistoryMock.mockResolvedValue({ thread_id: 't', messages: [
      { role: 'user', content: '旧问题' },
      { role: 'assistant', content: '旧答案', sources: [{ title: 'x.md' }] },
    ]});
    const { result } = renderHook(() => useChat());
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(result.current.messages).toHaveLength(2);
    const a = result.current.messages[1];
    if (a.role === 'assistant') { expect(a.answer).toBe('旧答案'); expect(a.status).toBe('done'); }
  });

  it('newChat 清空并换 thread_id', async () => {
    streamChatMock.mockImplementation(async (_b: unknown, h: any) => h.onDone({ thread_id: 't', rewrites: 0 }));
    const { result } = renderHook(() => useChat());
    await act(async () => { result.current.send('q'); });
    const before = result.current.threadId;
    act(() => { result.current.newChat(); });
    expect(result.current.messages).toHaveLength(0);
    expect(result.current.threadId).not.toBe(before);
  });
});
```

- [ ] **Step 3: 跑测试确认失败**

Run：`npx vitest run src/test/useThreadId.test.ts src/test/useChat.test.ts`
Expected: FAIL —— 找不到 `../hooks/useThreadId` / `../hooks/useChat`。

- [ ] **Step 4: 写 `src/hooks/useThreadId.ts`**

```ts
import { useCallback, useState } from 'react';

const KEY = 'ragqa_thread_id';

function genId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

export function useThreadId(): { threadId: string; resetThreadId: () => string } {
  const [threadId, setThreadId] = useState<string>(() => {
    const saved = localStorage.getItem(KEY);
    if (saved) return saved;
    const id = genId();
    localStorage.setItem(KEY, id);
    return id;
  });
  const resetThreadId = useCallback(() => {
    const id = genId();
    localStorage.setItem(KEY, id);
    setThreadId(id);
    return id;
  }, []);
  return { threadId, resetThreadId };
}
```

- [ ] **Step 5: 写 `src/hooks/useChat.ts`**

```ts
import { useCallback, useEffect, useReducer, useRef } from 'react';
import type { Step, Source, ErrorEvent } from '../api/types';
import { streamChat, fetchHistory } from '../api/client';
import { useThreadId } from './useThreadId';

export interface UserMsg { role: 'user'; content: string; }
export interface AssistantMsg {
  role: 'assistant'; steps: Step[]; answer: string; sources: Source[];
  status: 'streaming' | 'done' | 'error'; error?: string;
}
export type Msg = UserMsg | AssistantMsg;

type State = { messages: Msg[]; streaming: boolean };
type Action =
  | { type: 'PUSH_USER'; content: string }
  | { type: 'START_ASSISTANT' }
  | { type: 'ADD_STEP'; step: Step }
  | { type: 'APPEND_TOKEN'; text: string }
  | { type: 'SET_SOURCES'; sources: Source[] }
  | { type: 'FINISH' }
  | { type: 'FAIL'; error: string }
  | { type: 'LOAD_HISTORY'; messages: Msg[] }
  | { type: 'CLEAR' };

function updateLastAssistant(msgs: Msg[], fn: (a: AssistantMsg) => AssistantMsg): Msg[] {
  const out = msgs.slice();
  for (let i = out.length - 1; i >= 0; i--) {
    const m = out[i];
    if (m.role === 'assistant') { out[i] = fn(m); break; }
  }
  return out;
}

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case 'PUSH_USER':
      return { ...state, messages: [...state.messages, { role: 'user', content: action.content }] };
    case 'START_ASSISTANT':
      return { ...state, streaming: true, messages: [...state.messages, { role: 'assistant', steps: [], answer: '', sources: [], status: 'streaming' }] };
    case 'ADD_STEP':
      return { ...state, messages: updateLastAssistant(state.messages, a => ({ ...a, steps: [...a.steps, action.step] })) };
    case 'APPEND_TOKEN':
      return { ...state, messages: updateLastAssistant(state.messages, a => ({ ...a, answer: a.answer + action.text })) };
    case 'SET_SOURCES':
      return { ...state, messages: updateLastAssistant(state.messages, a => ({ ...a, sources: action.sources })) };
    case 'FINISH':
      return { ...state, streaming: false, messages: updateLastAssistant(state.messages, a => ({ ...a, status: 'done' })) };
    case 'FAIL':
      return { ...state, streaming: false, messages: updateLastAssistant(state.messages, a => ({ ...a, status: 'error', error: action.error })) };
    case 'LOAD_HISTORY':
      // 仅当当前无消息时回填：避免异步历史冲掉用户刚发起的对话，也优雅处理 StrictMode 双调用
      return state.messages.length === 0 ? { messages: action.messages, streaming: false } : state;
    case 'CLEAR':
      return { messages: [], streaming: false };
  }
}

export function useChat() {
  const [state, dispatch] = useReducer(reducer, { messages: [], streaming: false });
  const { threadId, resetThreadId } = useThreadId();
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    let alive = true;
    fetchHistory(threadId)
      .then(h => {
        if (!alive) return;
        const msgs: Msg[] = h.messages.map(m =>
          m.role === 'user'
            ? { role: 'user', content: m.content }
            : { role: 'assistant', steps: [], answer: m.content, sources: m.sources ?? [], status: 'done' },
        );
        dispatch({ type: 'LOAD_HISTORY', messages: msgs });
      })
      .catch(() => { /* 新会话或后端未就绪: 静默 */ });
    return () => { alive = false; };
  }, [threadId]);

  const send = useCallback((question: string) => {
    const q = question.trim();
    if (!q || state.streaming) return;
    dispatch({ type: 'PUSH_USER', content: q });
    dispatch({ type: 'START_ASSISTANT' });
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    void streamChat({ question: q, thread_id: threadId }, {
      signal: ctrl.signal,
      onStep: e => dispatch({ type: 'ADD_STEP', step: e }),
      onToken: e => dispatch({ type: 'APPEND_TOKEN', text: e.text }),
      onSources: e => dispatch({ type: 'SET_SOURCES', sources: e.sources }),
      onDone: () => dispatch({ type: 'FINISH' }),
      onError: (e: ErrorEvent) => dispatch({ type: 'FAIL', error: e.message }),
    }).catch((err: unknown) => {
      if ((err as Error)?.name === 'AbortError') dispatch({ type: 'FINISH' });
      else dispatch({ type: 'FAIL', error: '无法连接后端' });
    });
  }, [state.streaming, threadId]);

  const abort = useCallback(() => { abortRef.current?.abort(); }, []);
  const newChat = useCallback(() => {
    abortRef.current?.abort();
    dispatch({ type: 'CLEAR' });
    resetThreadId();
  }, [resetThreadId]);

  return { messages: state.messages, streaming: state.streaming, send, abort, newChat, threadId };
}
```

- [ ] **Step 6: 跑测试确认通过**

Run：`npx vitest run src/test/useThreadId.test.ts src/test/useChat.test.ts`
Expected: PASS —— useThreadId 3 例 + useChat 5 例全过。

- [ ] **Step 7: 验证（替代 commit）**

Run：`npm run build`
Expected: `tsc --noEmit` 无类型错误，退出码 0。

---

### Task 5: 展示组件（StepTrace / AnswerMarkdown / SourceChips / MessageBubble）

**Files:**
- Create: `<ROOT>/frontend/src/components/StepTrace.tsx`、`AnswerMarkdown.tsx`、`SourceChips.tsx`、`MessageBubble.tsx`
- Modify: `<ROOT>/frontend/tailwind.config.js`（加 typography 插件）、`<ROOT>/frontend/package.json`（加 devDep）
- Test: `<ROOT>/frontend/src/test/components.test.tsx`

**Interfaces:**
- Consumes: Task 3 类型 `Step`/`Source`；Task 4 类型 `Msg`。
- Produces（均为具名导出的纯展示组件，Task 6 依赖）：`StepTrace({ steps: Step[] })`、`AnswerMarkdown({ text: string })`、`SourceChips({ sources: Source[] })`、`MessageBubble({ msg: Msg })`。

- [ ] **Step 1: 加 @tailwindcss/typography（markdown 排版）**

修改 `frontend/package.json` 的 `devDependencies`，新增一行（按字母序放在 `@testing-library/jest-dom` 前）：
```json
    "@tailwindcss/typography": "^0.5.13",
```
把 `frontend/tailwind.config.js` 改为：
```js
import typography from '@tailwindcss/typography';
/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: { extend: {} },
  plugins: [typography],
};
```
Run（`<ROOT>/frontend`）：`npm install`
Expected: 新增 `@tailwindcss/typography` 安装成功，无 error。

- [ ] **Step 2: 写失败测试 `src/test/components.test.tsx`**

```tsx
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { StepTrace } from '../components/StepTrace';
import { SourceChips } from '../components/SourceChips';
import { AnswerMarkdown } from '../components/AnswerMarkdown';
import { MessageBubble } from '../components/MessageBubble';

describe('StepTrace', () => {
  it('无步骤不渲染', () => {
    const { container } = render(<StepTrace steps={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
  it('点击展开显示步骤 label 与 detail', () => {
    render(<StepTrace steps={[{ node: 'retrieve', label: '检索', detail: '召回 8 段' }]} />);
    fireEvent.click(screen.getByText(/推理步骤/));
    expect(screen.getByText('检索')).toBeInTheDocument();
    expect(screen.getByText(/召回 8 段/)).toBeInTheDocument();
  });
});

describe('SourceChips', () => {
  it('渲染来源标题与分数', () => {
    render(<SourceChips sources={[{ title: '01_加载.md', score: 0.82 }]} />);
    expect(screen.getByText('01_加载.md')).toBeInTheDocument();
    expect(screen.getByText('0.82')).toBeInTheDocument();
  });
});

describe('AnswerMarkdown', () => {
  it('渲染 markdown 标题与代码块', () => {
    const { container } = render(<AnswerMarkdown text={'# 标题\n\n```py\nprint(1)\n```'} />);
    expect(container.querySelector('h1')).not.toBeNull();
    expect(container.querySelector('code')).not.toBeNull();
  });
});

describe('MessageBubble', () => {
  it('user 气泡显示内容', () => {
    render(<MessageBubble msg={{ role: 'user', content: '你好' }} />);
    expect(screen.getByText('你好')).toBeInTheDocument();
  });
  it('assistant streaming 空答案显示思考中', () => {
    render(<MessageBubble msg={{ role: 'assistant', steps: [], answer: '', sources: [], status: 'streaming' }} />);
    expect(screen.getByText('思考中…')).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: 跑测试确认失败**

Run：`npx vitest run src/test/components.test.tsx`
Expected: FAIL —— 找不到 `../components/*`。

- [ ] **Step 4: 写 `src/components/StepTrace.tsx`**

```tsx
import { useState } from 'react';
import type { Step } from '../api/types';

export function StepTrace({ steps }: { steps: Step[] }) {
  const [open, setOpen] = useState(false);
  if (steps.length === 0) return null;
  return (
    <div className="mb-2 text-xs">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1 rounded bg-blue-50 px-2 py-1 text-blue-700 hover:bg-blue-100"
      >
        <span>{open ? '▾' : '▸'}</span>
        <span>🔍 推理步骤（{steps.length}）</span>
      </button>
      {open && (
        <ol className="mt-1 space-y-0.5 border-l-2 border-blue-100 pl-3">
          {steps.map((s, i) => (
            <li key={i} className="text-gray-600">
              <span className="font-medium">{s.label}</span>
              {s.detail ? <span className="text-gray-400"> · {s.detail}</span> : null}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
```

- [ ] **Step 5: 写 `src/components/AnswerMarkdown.tsx`**

```tsx
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';

export function AnswerMarkdown({ text }: { text: string }) {
  return (
    <div className="prose prose-sm max-w-none break-words">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
```

- [ ] **Step 6: 写 `src/components/SourceChips.tsx`**

```tsx
import type { Source } from '../api/types';

export function SourceChips({ sources }: { sources: Source[] }) {
  if (sources.length === 0) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {sources.map((s, i) => (
        <span
          key={i}
          title={s.snippet ?? s.title}
          className="inline-flex items-center gap-1 rounded-full border border-orange-200 bg-orange-50 px-2 py-0.5 text-xs text-orange-700"
        >
          <span>{i + 1}</span>
          <span className="max-w-[16rem] truncate">{s.title}</span>
          {typeof s.score === 'number' ? <span className="text-orange-400">{s.score.toFixed(2)}</span> : null}
        </span>
      ))}
    </div>
  );
}
```

- [ ] **Step 7: 写 `src/components/MessageBubble.tsx`**

```tsx
import type { Msg } from '../hooks/useChat';
import { StepTrace } from './StepTrace';
import { AnswerMarkdown } from './AnswerMarkdown';
import { SourceChips } from './SourceChips';

export function MessageBubble({ msg }: { msg: Msg }) {
  if (msg.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-lg bg-green-100 px-3 py-2 text-sm text-gray-800">{msg.content}</div>
      </div>
    );
  }
  return (
    <div className="flex justify-start">
      <div className="max-w-[85%] rounded-lg border bg-white px-3 py-2">
        <StepTrace steps={msg.steps} />
        {msg.status === 'streaming' && msg.answer === '' ? (
          <p className="text-sm text-gray-400">思考中…</p>
        ) : (
          <AnswerMarkdown text={msg.answer} />
        )}
        {msg.status === 'error' ? (
          <p className="mt-1 text-xs text-red-600">⚠ {msg.error ?? '出错了'}</p>
        ) : null}
        <SourceChips sources={msg.sources} />
      </div>
    </div>
  );
}
```

- [ ] **Step 8: 跑测试确认通过**

Run：`npx vitest run src/test/components.test.tsx`
Expected: PASS —— StepTrace 2 + SourceChips 1 + AnswerMarkdown 1 + MessageBubble 2 全过。

- [ ] **Step 9: 验证（替代 commit）**

Run：`npm run build`
Expected: `tsc --noEmit` 无类型错误，退出码 0。

---

### Task 6: 组装聊天界面（Header / ChatWindow / Composer + 重写 App）

**Files:**
- Create: `<ROOT>/frontend/src/components/Header.tsx`、`ChatWindow.tsx`、`Composer.tsx`
- Modify: `<ROOT>/frontend/src/App.tsx`（由静态壳重写为接通 `useChat`）
- Test: `<ROOT>/frontend/src/test/app.test.tsx`

**Interfaces:**
- Consumes: Task 4 `useChat`（`{ messages, streaming, send, abort, newChat }`）；Task 5 `MessageBubble`。
- Produces: `Header({ onNewChat }: { onNewChat: () => void })`、`ChatWindow({ messages }: { messages: Msg[] })`、`Composer({ streaming, onSend, onStop }: { streaming: boolean; onSend: (q: string) => void; onStop: () => void })`；可运行的完整 App。

- [ ] **Step 1: 写失败集成测试 `src/test/app.test.tsx`**

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const { streamChatMock, fetchHistoryMock } = vi.hoisted(() => ({
  streamChatMock: vi.fn(), fetchHistoryMock: vi.fn(),
}));
vi.mock('../api/client', () => ({ streamChat: streamChatMock, fetchHistory: fetchHistoryMock }));

import App from '../App';

beforeEach(() => {
  localStorage.clear();
  streamChatMock.mockReset();
  fetchHistoryMock.mockResolvedValue({ thread_id: 't', messages: [] });
});

describe('App 集成', () => {
  it('输入并发送后, 界面出现用户问题与流式答案', async () => {
    streamChatMock.mockImplementation(async (_b: unknown, h: any) => {
      h.onToken({ text: '你好' });
      h.onDone({ thread_id: 't', rewrites: 0 });
    });
    render(<App />);
    fireEvent.change(screen.getByPlaceholderText('输入问题…'), { target: { value: '什么是 LangChain' } });
    fireEvent.click(screen.getByText('发送'));
    await waitFor(() => expect(screen.getByText('什么是 LangChain')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText(/你好/)).toBeInTheDocument());
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run：`npx vitest run src/test/app.test.tsx`
Expected: FAIL —— App 仍为静态壳（无 `输入问题…` 可交互输入框 / 无 `useChat`）。

- [ ] **Step 3: 写 `src/components/Header.tsx`**

```tsx
export function Header({ onNewChat }: { onNewChat: () => void }) {
  return (
    <header className="flex items-center justify-between border-b bg-white px-4 py-3">
      <h1 className="text-sm font-semibold text-gray-800">🤖 LangChain 智能问答</h1>
      <button onClick={onNewChat} className="rounded-md border px-3 py-1 text-xs text-gray-600 hover:bg-gray-100">
        ＋ 新对话
      </button>
    </header>
  );
}
```

- [ ] **Step 4: 写 `src/components/ChatWindow.tsx`**

```tsx
import { useEffect, useRef } from 'react';
import type { Msg } from '../hooks/useChat';
import { MessageBubble } from './MessageBubble';

export function ChatWindow({ messages }: { messages: Msg[] }) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);
  return (
    <main className="flex-1 overflow-y-auto p-4">
      <div className="mx-auto flex max-w-3xl flex-col gap-3">
        {messages.length === 0 ? (
          <p className="mt-10 text-center text-sm text-gray-400">开始提问吧</p>
        ) : (
          messages.map((m, i) => <MessageBubble key={i} msg={m} />)
        )}
        <div ref={endRef} />
      </div>
    </main>
  );
}
```

- [ ] **Step 5: 写 `src/components/Composer.tsx`**

```tsx
import { useState } from 'react';

export function Composer({ streaming, onSend, onStop }: {
  streaming: boolean; onSend: (q: string) => void; onStop: () => void;
}) {
  const [value, setValue] = useState('');
  const submit = () => {
    const q = value.trim();
    if (!q || streaming) return;
    onSend(q);
    setValue('');
  };
  return (
    <footer className="border-t bg-white p-3">
      <div className="mx-auto flex max-w-3xl gap-2">
        <input
          className="flex-1 rounded-md border px-3 py-2 text-sm"
          placeholder="输入问题…"
          value={value}
          maxLength={4000}
          onChange={e => setValue(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); } }}
        />
        {streaming ? (
          <button onClick={onStop} className="rounded-md bg-red-500 px-4 py-2 text-sm text-white hover:bg-red-600">停止</button>
        ) : (
          <button onClick={submit} className="rounded-md bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700">发送</button>
        )}
      </div>
    </footer>
  );
}
```

- [ ] **Step 6: 重写 `src/App.tsx`**

```tsx
import { useChat } from './hooks/useChat';
import { Header } from './components/Header';
import { ChatWindow } from './components/ChatWindow';
import { Composer } from './components/Composer';

export default function App() {
  const { messages, streaming, send, abort, newChat } = useChat();
  return (
    <div className="flex h-screen flex-col bg-gray-50">
      <Header onNewChat={newChat} />
      <ChatWindow messages={messages} />
      <Composer streaming={streaming} onSend={send} onStop={abort} />
    </div>
  );
}
```

- [ ] **Step 7: 跑集成测试确认通过**

Run：`npx vitest run src/test/app.test.tsx`
Expected: PASS —— 发送后界面出现用户问题与流式答案。

- [ ] **Step 8: 跑全量测试 + 构建验证（替代 commit）**

Run：`npm run test && npm run build`
Expected: 全部测试套件（client / useThreadId / useChat / components / app）PASS；build 退出码 0。

---

### Task 7: mock SSE 服务 + 端到端联调

**Files:**
- Create: `<ROOT>/frontend/mock/server.mjs`

**Interfaces:**
- Consumes: 无（独立 Node 脚本，只依赖 `node:http`）。
- Produces: 一个监听 `:8000` 的 mock 后端，实现三端点，供前端在真实后端就绪前联调。

- [ ] **Step 1: 写 `mock/server.mjs`**

```js
// 无真实后端时联调用。启动: npm run mock (监听 :8000)
import http from 'node:http';

const PORT = 8000;
const steps = [
  ['step', { node: 'retrieve', label: '检索', detail: '召回 8 段' }],
  ['step', { node: 'grade_documents', label: '评分', detail: '相关 6/8' }],
  ['step', { node: 'generate', label: '生成', detail: '开始作答' }],
];
const tokens = ['要切分 markdown，', '用 `RecursiveCharacterTextSplitter.from_language', '(Language.MARKDOWN, ...)`。', '它按标题/代码围栏优先切分。'];
const sources = [{ title: '01_文档加载与文本分割.md', snippet: 'RecursiveCharacterTextSplitter.from_language(...)', score: 0.82 }];

const frame = (event, data) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
const sleep = ms => new Promise(r => setTimeout(r, ms));
function cors(res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET,POST,OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
}

const server = http.createServer(async (req, res) => {
  cors(res);
  if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return; }
  const url = new URL(req.url, `http://localhost:${PORT}`);

  if (req.method === 'GET' && url.pathname === '/api/health') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ status: 'ok', kb_count: 2885, model: 'qwen3.7-text-embedding' }));
    return;
  }
  if (req.method === 'GET' && url.pathname === '/api/chat/history') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ thread_id: url.searchParams.get('thread_id') ?? '', messages: [] }));
    return;
  }
  if (req.method === 'POST' && url.pathname === '/api/chat/stream') {
    const body = await new Promise(resolve => { let b = ''; req.on('data', c => b += c); req.on('end', () => resolve(b)); });
    let thread_id = 'mock';
    try { thread_id = JSON.parse(body).thread_id ?? 'mock'; } catch { /* 忽略 */ }
    res.writeHead(200, {
      'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache',
      Connection: 'keep-alive', 'X-Accel-Buffering': 'no',
    });
    for (const [ev, data] of steps) { res.write(frame(ev, data)); await sleep(200); }
    for (const t of tokens) { res.write(frame('token', { text: t })); await sleep(120); }
    res.write(frame('sources', { sources })); await sleep(100);
    res.write(frame('done', { thread_id, rewrites: 0 }));
    res.end();
    return;
  }
  res.writeHead(404, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({ code: 'NOT_FOUND', message: 'no route' }));
});

server.listen(PORT, () => console.log(`[mock] SSE 服务已起: http://localhost:${PORT}`));
```

- [ ] **Step 2: 起 mock 并用 curl 验证 SSE**

Run（后台）：`npm run mock`；另开一个终端：
`curl -N -X POST http://localhost:8000/api/chat/stream -H "Content-Type: application/json" -d "{\"question\":\"如何切分 markdown?\",\"thread_id\":\"t1\"}"`
Expected: 逐条打印 `event: step`（×3）→ `event: token`（×4）→ `event: sources` → `event: done`，帧间有延迟。

- [ ] **Step 3: 前端 dev 对 mock 端到端联调（手动）**

Run（后台，保持 mock 运行）：`npm run dev` → 浏览器 `http://localhost:5173`。
操作与 Expected：
- 输入问题并发送 → 顶部出现可折叠「🔍 推理步骤」（展开见检索/评分/生成）→ 答案打字机逐字出现（含代码高亮）→ 末尾出现来源芯片。
- 生成中点「停止」→ 中断，保留已生成部分。
- 点「＋ 新对话」→ 消息清空，thread_id 更换（控制台/localStorage 可见）。

- [ ] **Step 4: 停掉 mock 与 dev**

停止两个后台进程（mock :8000 / dev :5173）。

- [ ] **Step 5: 最终验证（替代 commit）**

Run：`npm run test && npm run build`
Expected: 全部测试 PASS；build 退出码 0、产出 `dist/`。

---

## 整体验收标准（Definition of Done）

- `cd frontend && npm install && npm run test` 全绿（client / useThreadId / useChat / components / app 五个套件）。
- `npm run build` 退出码 0，产出 `dist/`（供生产 FastAPI StaticFiles 挂载）。
- `npm run mock` + `npm run dev`：浏览器 `:5173` 提问 → 见步骤折叠条 + 打字机答案 + 来源芯片；停止可中断；新对话清空并换 thread_id。
- `docs/api/` 三文件（README.md / openapi.yaml / sse-example.txt）齐全且互相一致；openapi.yaml 通过 lint / 结构校验。
- 契约与前端类型一一对应（openapi schema 名 ↔ `types.ts`）。
- 后端（用户实现）完成后，将 `vite.config.ts` 的 proxy 指向真实 :8000 即可无缝切换（mock 与真实后端同契约）。

---

## 执行修订记录（2026-09-09，Inline 执行落地）

实现中发现并修复的 3 处，已回写到上文对应任务，代码与计划现已一致：

1. **Task 3 · `client.test.ts`**：非 2xx 错误断言改用数组收集器 `const errs: ErrorEvent[] = []` + `errs[0]?.code`，替代 `let err: ErrorEvent | null = null`——后者会被 TS 控制流在断言处窄化为 `null`，使 `err?.code` 报「Property does not exist on type never」。
2. **Task 4 · `useChat.ts`**：`LOAD_HISTORY` 加「仅当 `messages` 为空才回填」守卫，避免挂载时异步 `fetchHistory` 结果冲掉用户在其 resolve 前发出的消息，同时化解 StrictMode 的 effect 双调用。
3. **Task 2 · `src/test/setup.ts`**：新增 `Element.prototype.scrollIntoView = () => {}` 桩——jsdom 未实现该方法，Task 6 的 ChatWindow 自动滚动会在测试中报错。

**最终状态**：23 个前端测试全绿，`npm run build` 通过（501 模块 → dist/），mock + Vite proxy 端到端 curl 验证通过（health / history / SSE stream 三端点 + SSE 经代理不缓冲）。
