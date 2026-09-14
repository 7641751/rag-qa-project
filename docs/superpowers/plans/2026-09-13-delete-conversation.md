# 删除对话 + 会话持久化（P1）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给前端加「删除对话」入口（二次确认模态框），并把契约扩展到 `DELETE /api/chat/threads/{thread_id}`；后端（SqliteSaver 持久化 + DELETE 端点）由用户实现。

**Architecture:** 契约先行 → 前端 `client.ts` 加一个 `deleteThread()` → `useChat` 状态机加 4 个 action 与一个 `deleteChat()`（严格串行 `abort → await 流结束 → DELETE`）→ 新建通用 `ConfirmDialog` 组件 → `Header` 加第三个按钮、`App` 持弹窗开关。mock server 补 DELETE 端点与按 thread 存储的历史，使前端无需真后端即可端到端自测。

**Tech Stack:** React 18 + TypeScript + Vite 5 + Tailwind 3；Vitest 2 + Testing Library + jsdom；契约 OpenAPI 3.1 + Markdown。

**Spec:** `docs/superpowers/specs/2026-09-13-delete-conversation-design.md`（9 项决策与全部证据行号在那里）

---

## Global Constraints

以下为全计划约束，**每个 Task 的要求都隐含包含本节**：

1. **契约先行**：Task 1 必须最先完成。任何字段/事件变更先改 `docs/api/README.md` + `docs/api/openapi.yaml`，再改 `types.ts` / `client.ts`（`docs/api/README.md:370-373` 的既有约定）。
2. **端点与字段名严格照契约**：`DELETE /api/chat/threads/{thread_id}`；响应 `{thread_id, deleted}`；**不新增错误码**（沿用 `VALIDATION_ERROR` / `INTERNAL_ERROR`，现有 10 个）。
3. **幂等语义**：未知/已删过的 `thread_id` 返回 **200 + `deleted:false`**，不是 404。前端把 `deleted:false` 当成功处理。
4. **悲观时序**：DELETE 成功才清空前端消息；失败保留消息 + 就地显示错误（不引 toast 系统）。
5. **硬约束顺序**：流式进行中删除必须 `abort()` → `await` 在飞的流 settle → 才发 DELETE。否则被取消的 `astream` 可能在 DELETE 之后写入 checkpoint，留下删不掉的残行。
6. **删除后保留原 `thread_id`**：`deleteChat` **不得**调用 `resetThreadId()`（与「＋ 新对话」语义正交）。
7. **不新增任何前端依赖**：`ConfirmDialog` 自己写（`package.json` 现有 9 个 dependencies 一个都不加）。
8. **既有 66 个测试必须全绿**：只允许按 Task 3 Step 1、Task 5 Step 1 的说明**扩展 `vi.mock` 工厂**，**不得修改任何既有断言**。
9. **聊天链路零改动**：`ChatWindow` / `Composer` / `MessageBubble` / `AnswerMarkdown` / `useThreadId` 本期一行不改。`useChat.ts` 只加不改，唯一例外见 Task 3 Step 3 的 `CLEAR` 分支（必须补 `...state`，否则 TS 报缺字段）。
10. **后端不在本计划内**：后端步骤（SqliteSaver、DELETE 端点、`.gitignore`、pytest）全部集中在末尾「交接给用户」章节，执行者**不要**改 `backend/` 下任何文件。
11. **提交需确认**：本仓库已推送到 GitHub（`origin` = `github.com/7641751/advanced_tutorial`）。计划里的 commit 步骤**执行前必须先征得用户同意**，不擅自 commit/push。
12. **Windows 环境**：shell 是 `cmd.exe`。`findstr` 遇中文会「写入错误」中断 → 校验一律用 `node`；命令分隔用 `&` 而非 `;`；批处理文件里**不要写中文注释**（cmd 按 GBK 解析 UTF-8 会乱码并吞掉后续命令）。

---

## File Structure

| 文件 | 动作 | 责任 | 所属 Task |
|---|---|---|---|
| `docs/api/README.md` | 改 | 端点清单 +1 行；新增「DELETE /api/chat/threads/{thread_id}」章节；修第 1 行手误 | 1 |
| `docs/api/openapi.yaml` | 改 | 新增 path 与 `ChatDeleteResponse` schema | 1 |
| `frontend/src/api/types.ts` | 改 | 新增 `ChatDeleteResponse` | 2 |
| `frontend/src/api/client.ts` | 改 | 新增 `deleteThread()` | 2 |
| `frontend/src/test/client.test.ts` | 改 | +3 用例（`deleteThread`） | 2 |
| `frontend/src/hooks/useChat.ts` | 改 | State +2 字段、Action +4、`streamRef`、`deleteChat`、`clearDeleteError` | 3 |
| `frontend/src/test/useChat.test.ts` | 改 | mock 工厂 +`deleteThread`；+5 用例 | 3 |
| `frontend/src/components/ConfirmDialog.tsx` | **新建** | 通用确认模态框 | 4 |
| `frontend/src/test/confirm-dialog.test.tsx` | **新建** | +6 用例 | 4 |
| `frontend/src/index.css` | 改 | 加 `dialog-pop-in` keyframes | 4 |
| `frontend/src/components/Header.tsx` | 改 | 第三个按钮「🗑 删除对话」 | 5 |
| `frontend/src/App.tsx` | 改 | 持弹窗开关 + 接线 | 5 |
| `frontend/src/test/app.test.tsx` | 改 | mock 工厂 +`deleteThread`；+3 用例 | 5 |
| `frontend/mock/server.mjs` | 改 | 按 thread 存历史 + DELETE 端点（6 → 7 端点） | 6 |
| `frontend/README.md` | 改 | 补「删除对话」说明与端点数 | 6 |

---

### Task 1: 契约扩展（docs/api）

**Files:**
- Modify: `docs/api/README.md:1`（修手误）、`:13-20`（端点清单）、`:173`（在 `---` 后插入新章节）
- Modify: `docs/api/openapi.yaml:58/59`（插入 path）、`:198/199`（插入 schema）

**Interfaces:**
- Consumes: 无（本 Task 是源头）
- Produces: `DELETE /api/chat/threads/{thread_id}` → `200 {thread_id: string, deleted: boolean}`；schema 名 `ChatDeleteResponse`。后续 Task 2 的 TS 类型必须与此**逐字一致**。

- [ ] **Step 1: 修 `docs/api/README.md` 第 1 行的手误**

第 1 行当前是 `@# LangChain 智能问答 API 契约`，开头多了个 `@`，导致 H1 渲染不出来（GitHub 与 VS Code 预览都会把它当普通段落）。

改为：

```markdown
# LangChain 智能问答 API 契约
```

- [ ] **Step 2: 端点清单加一行**

在 `docs/api/README.md` 的端点清单表格里，`GET /api/chat/history` 那一行（当前第 16 行）**之后**插入：

```markdown
| `DELETE` | `/api/chat/threads/{thread_id}` | 删除会话的全部服务端状态（幂等） | `application/json` |
```

插入后表格应为 7 行（chat 3 行 + kb 3 行 + health 1 行）。

- [ ] **Step 3: 新增「DELETE /api/chat/threads/{thread_id}」章节**

在 `## GET /api/chat/history` 章节结束的 `---`（当前第 173 行）之后、`## GET /api/health`（当前第 175 行）之前，插入下面整段。

> ⚠ 这段内容自身含有 ``` 围栏，粘贴时保持原样（外层不需要再包一层）。

````markdown
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
````

- [ ] **Step 4: `openapi.yaml` 新增 path**

在 `/api/chat/history` 块结束处（当前第 58 行 `schema: { $ref: '#/components/schemas/HistoryResponse' }`）之后、`  /api/health:`（当前第 59 行）之前插入。**缩进必须与 `/api/chat/history:` 一致（2 空格）**：

```yaml
  /api/chat/threads/{thread_id}:
    delete:
      tags: [chat]
      summary: 删除会话的全部 checkpoint(幂等)
      description: >
        删除该 thread 在 checkpointer 中的全部 checkpoint 与 write 行，不可恢复。
        幂等: 未知/已删过的 thread_id 返回 200 + deleted=false(不是 404)。
        与 KB 的 DELETE(404) 故意不同: doc_id 来自服务端列表，thread_id 由前端本地生成。
        删除后 GET /api/chat/history 仍返回 200 + 空 messages。
        同一 thread_id 删除后可继续使用(LangGraph 会自动重建)。
      parameters:
        - name: thread_id
          in: path
          required: true
          schema: { type: string, format: uuid }
      responses:
        '200':
          description: 删除结果。deleted=false 表示本来就不存在(幂等，非错误)
          content:
            application/json:
              schema: { $ref: '#/components/schemas/ChatDeleteResponse' }
        '422': { $ref: '#/components/responses/ValidationError' }
```

- [ ] **Step 5: `openapi.yaml` 新增 schema**

在 `HistoryResponse` 块结束处（当前第 198 行 `messages: { type: array, ... }`）之后、`    Health:`（当前第 199 行）之前插入。**缩进 4 空格**（与 `HistoryResponse:` 同级）：

```yaml
    ChatDeleteResponse:
      type: object
      required: [thread_id, deleted]
      properties:
        thread_id: { type: string, format: uuid }
        deleted:
          type: boolean
          description: >
            删除前该 thread 是否存在至少一个 checkpoint。false = 本来就不存在(幂等，非错误)。
            后端需先用 checkpointer.get_tuple(config) 探测，因为 delete_thread 返回 None。
```

- [ ] **Step 6: 验证契约（真解析，不是肉眼看）**

Run（在 `rag_qa_project/` 目录下）：

```bat
.venv\Scripts\python.exe -c "import yaml,io;d=yaml.safe_load(io.open('docs/api/openapi.yaml',encoding='utf-8'));p=d['paths'];s=d['components']['schemas'];print('paths=',len(p));print('threads_path=', '/api/chat/threads/{thread_id}' in p);print('methods=',list(p['/api/chat/threads/{thread_id}'].keys()));print('ChatDeleteResponse=',list(s['ChatDeleteResponse']['properties'].keys()));print('required=',s['ChatDeleteResponse']['required']);print('responses=',list(p['/api/chat/threads/{thread_id}']['delete']['responses'].keys()))"
```

Expected:
```
paths= 6
threads_path= True
methods= ['delete']
ChatDeleteResponse= ['thread_id', 'deleted']
required= ['thread_id', 'deleted']
responses= ['200', '422']
```

（6 个 path = `/api/chat/stream`、`/api/chat/history`、`/api/chat/threads/{thread_id}`、`/api/health`、`/api/kb/documents`、`/api/kb/documents/{doc_id}`。）

再验证 README 的插入位置与手误修复：

```bat
node -e "const fs=require('fs');const L=fs.readFileSync('docs/api/README.md','utf8').split(/\r?\n/);console.log('L1=',JSON.stringify(L[0]));console.log('endpoint_rows=',L.filter(l=>/^\| `(GET|POST|DELETE|PATCH)`/.test(l)).length);console.log('has_delete_section=',L.some(l=>l.startsWith('## DELETE /api/chat/threads/')));console.log('stray_at=',L[0].startsWith('@'));"
```

Expected:
```
L1= "# LangChain 智能问答 API 契约"
endpoint_rows= 7
has_delete_section= true
stray_at= false
```

- [ ] **Step 7: 提交（需用户确认）**

```bat
git add docs/api/README.md docs/api/openapi.yaml
git commit -m "docs(api): 新增 DELETE /api/chat/threads/{thread_id} 契约" -m "幂等语义: 未知 thread_id 返回 200 + deleted=false(与 KB DELETE 的 404 故意不同，因 thread_id 由前端本地生成)。顺带修掉 README 第 1 行多余的手误字符 @。"
```

---

### Task 2: 前端类型与 client（TDD）

**Files:**
- Test: `frontend/src/test/client.test.ts:2`（import 行）、文件末尾追加 describe
- Modify: `frontend/src/api/types.ts:18` 之后
- Modify: `frontend/src/api/client.ts:1-5`（type import）、`:145` 之后（新函数）

**Interfaces:**
- Consumes: Task 1 的契约（`{thread_id, deleted}`）
- Produces: `deleteThread(threadId: string): Promise<ChatDeleteResponse>`；`interface ChatDeleteResponse { thread_id: string; deleted: boolean }`。Task 3 的 `useChat` 会 import 它。

- [ ] **Step 1: 写失败测试**

先改 `frontend/src/test/client.test.ts` 第 2 行的 import，加上 `deleteThread`：

```ts
import { parseSSEFrame, splitFrames, streamChat, uploadDocument, fetchDocuments, deleteDocument, deleteThread } from '../api/client';
```

然后在文件**末尾**（当前第 145 行 `});` 之后）追加：

```ts
describe('deleteThread', () => {
  it('用 DELETE 方法并把 thread_id 编进路径', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ thread_id: 't1', deleted: true }),
    } as unknown as Response);
    vi.stubGlobal('fetch', fetchMock);

    const r = await deleteThread('t1');

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe('/api/chat/threads/t1');
    expect(init).toEqual({ method: 'DELETE' });
    expect(r).toEqual({ thread_id: 't1', deleted: true });
  });

  it('对含特殊字符的 id 做 URL 编码', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ thread_id: 'a/b', deleted: false }),
    } as unknown as Response);
    vi.stubGlobal('fetch', fetchMock);

    await deleteThread('a/b');

    expect(fetchMock.mock.calls[0]![0]).toBe('/api/chat/threads/a%2Fb');
  });

  it('deleted:false 也是成功(幂等)，非 2xx 才抛错', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ thread_id: 't1', deleted: false }),
    } as unknown as Response));
    await expect(deleteThread('t1')).resolves.toEqual({ thread_id: 't1', deleted: false });

    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 500 } as unknown as Response));
    await expect(deleteThread('t1')).rejects.toThrow(/500/);
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run（在 `frontend/` 目录下）：

```bat
npm run test -- src/test/client.test.ts
```

Expected: **FAIL**。报错形如 `[vitest] No "deleteThread" export is defined on the "../api/client" mock` 或 `deleteThread is not a function` / TS2305 找不到导出。既有 **14** 个 client 用例仍应通过。

- [ ] **Step 3: `types.ts` 加类型**

在 `frontend/src/api/types.ts` 的 `HistoryResponse`（当前第 18 行）**之后**插入：

```ts
/** DELETE /api/chat/threads/{id} 的响应。deleted=false 表示本来就不存在（幂等，非错误）。 */
export interface ChatDeleteResponse { thread_id: string; deleted: boolean; }
```

- [ ] **Step 4: `client.ts` 加函数**

先把 `ChatDeleteResponse` 加进文件顶部的 type import（当前第 1-5 行）：

```ts
import type {
  ChatRequest, StreamHandlers, HistoryResponse, Health, ChatDeleteResponse,
  StepEvent, TokenEvent, SourcesEvent, DoneEvent, ErrorEvent, ErrorCode,
  KbStreamHandlers, KbProgressEvent, KbDoneEvent, KbListResponse, KbDeleteResponse,
} from './types';
```

再在 `fetchHistory`（当前第 141-145 行）**之后**插入：

```ts
/** DELETE /api/chat/threads/{thread_id} → 删除该会话在服务端的全部 checkpoint。
 *  契约规定未知 id 也返 200（deleted:false 表示本来就没有），所以非 2xx 一律是真失败，
 *  直接 throw 交给调用方（useChat.deleteChat 会转成中文提示）。 */
export async function deleteThread(threadId: string): Promise<ChatDeleteResponse> {
  const res = await fetch(`${API_BASE}/chat/threads/${encodeURIComponent(threadId)}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`delete thread HTTP ${res.status}`);
  return (await res.json()) as ChatDeleteResponse;
}
```

- [ ] **Step 5: 跑测试确认通过**

```bat
npm run test -- src/test/client.test.ts
```

Expected: **PASS**，17 个用例（原 14 + 新 3）。

再跑全量确认无回归：

```bat
npm run test
```

Expected: **69 passed**（66 + 3）。

（当前基线已实测：7 个测试文件共 **66** 个用例 —— client 14 / useThreadId 3 / kb-components 11 / useChat 8 / useKnowledgeBase 12 / components 14 / app 4。本期新增第 8 个文件 `confirm-dialog.test.tsx`。）

- [ ] **Step 6: 提交（需用户确认）**

```bat
git add frontend/src/api/types.ts frontend/src/api/client.ts frontend/src/test/client.test.ts
git commit -m "feat(frontend): client 新增 deleteThread + ChatDeleteResponse 类型"
```

---

### Task 3: `useChat` 状态机扩展（TDD）

**Files:**
- Test: `frontend/src/test/useChat.test.ts:4-9`（mock 工厂）、`:13-18`（beforeEach）、文件末尾追加 5 用例
- Modify: `frontend/src/hooks/useChat.ts`（整文件重写，113 → 约 165 行）

**Interfaces:**
- Consumes: Task 2 的 `deleteThread(threadId): Promise<ChatDeleteResponse>`
- Produces: `useChat()` 返回值新增 4 项 —— `deleting: boolean`、`deleteError: string | null`、`deleteChat(): Promise<boolean>`、`clearDeleteError(): void`。Task 5 的 `App.tsx` 依赖这 4 个名字，**一字不能差**。

- [ ] **Step 1: 先扩 mock 工厂（不做这一步整个测试文件会炸）**

`useChat.ts` 一旦 import `deleteThread`，现有的 `vi.mock('../api/client', ...)` 工厂只导出了 `streamChat` 与 `fetchHistory`，vitest 会对缺失的导出报 `No "deleteThread" export is defined on the mock`，**8 个既有用例全部失败**。

把 `frontend/src/test/useChat.test.ts` 的第 4-9 行替换为：

```ts
const { streamChatMock, fetchHistoryMock, deleteThreadMock } = vi.hoisted(() => ({
  streamChatMock: vi.fn(), fetchHistoryMock: vi.fn(), deleteThreadMock: vi.fn(),
}));
vi.mock('../api/client', () => ({
  streamChat: streamChatMock, fetchHistory: fetchHistoryMock, deleteThread: deleteThreadMock,
}));
```

再把 `beforeEach`（当前第 13-18 行）替换为：

```ts
beforeEach(() => {
  localStorage.clear();
  streamChatMock.mockReset();
  fetchHistoryMock.mockReset();
  deleteThreadMock.mockReset();
  fetchHistoryMock.mockResolvedValue({ thread_id: 't', messages: [] });
  deleteThreadMock.mockResolvedValue({ thread_id: 't', deleted: true });
});
```

- [ ] **Step 2: 写 5 个失败测试**

在 `describe('useChat', ...)` 内部、最后一个用例（当前第 122 行 `});`）**之前**追加：

```ts
  it('流式进行中删除：先 abort 并等流结束，才发 DELETE（顺序是硬约束）', async () => {
    // 用一个悬着的流：只有 abort 时才 reject（模拟真实 AbortError）
    const order: string[] = [];
    let signal: AbortSignal | undefined;
    streamChatMock.mockImplementation((_b: unknown, h: any) => new Promise<void>((_res, rej) => {
      signal = h.signal as AbortSignal;
      signal.addEventListener('abort', () => {
        order.push('aborted');
        const e = new Error('Aborted'); e.name = 'AbortError'; rej(e);
      });
    }));
    deleteThreadMock.mockImplementation(async () => {
      order.push('deleted');
      return { thread_id: 't', deleted: true };
    });

    const { result } = renderHook(() => useChat());
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
    act(() => { result.current.send('q'); });                 // 不 await：让流悬着
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });

    let ok = false;
    await act(async () => { ok = await result.current.deleteChat(); });

    expect(order).toEqual(['aborted', 'deleted']);            // DELETE 不得早于流结束
    expect(ok).toBe(true);
    expect(signal?.aborted).toBe(true);
  });

  it('删除成功：清空消息、返回 true、deleting 归位', async () => {
    streamChatMock.mockImplementation(async (_b: unknown, h: any) =>
      h.onDone({ thread_id: 't', rewrites: 0, grounded: true }));
    const { result } = renderHook(() => useChat());
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
    await act(async () => { result.current.send('q'); });
    expect(result.current.messages).toHaveLength(2);

    let ok = false;
    await act(async () => { ok = await result.current.deleteChat(); });

    expect(ok).toBe(true);
    expect(result.current.messages).toHaveLength(0);
    expect(result.current.deleting).toBe(false);
    expect(result.current.deleteError).toBeNull();
  });

  it('删除失败：保留消息 + 中文提示 + 返回 false；clearDeleteError 可清', async () => {
    streamChatMock.mockImplementation(async (_b: unknown, h: any) =>
      h.onDone({ thread_id: 't', rewrites: 0, grounded: true }));
    deleteThreadMock.mockRejectedValue(new Error('delete thread HTTP 500'));
    const { result } = renderHook(() => useChat());
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
    await act(async () => { result.current.send('q'); });

    let ok = true;
    await act(async () => { ok = await result.current.deleteChat(); });

    expect(ok).toBe(false);
    expect(result.current.messages).toHaveLength(2);          // 悲观：不清空
    expect(result.current.deleteError).toBe('删除失败，请重试');
    expect(result.current.deleting).toBe(false);

    act(() => { result.current.clearDeleteError(); });
    expect(result.current.deleteError).toBeNull();
  });

  it('网络层失败(TypeError)提示「无法连接后端」', async () => {
    deleteThreadMock.mockRejectedValue(new TypeError('Failed to fetch'));
    const { result } = renderHook(() => useChat());
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });

    let ok = true;
    await act(async () => { ok = await result.current.deleteChat(); });

    expect(ok).toBe(false);
    expect(result.current.deleteError).toBe('无法连接后端');
  });

  it('deleted:false(幂等)同样清空，且 thread_id 保持不变', async () => {
    streamChatMock.mockImplementation(async (_b: unknown, h: any) =>
      h.onDone({ thread_id: 't', rewrites: 0, grounded: true }));
    deleteThreadMock.mockResolvedValue({ thread_id: 't', deleted: false });
    const { result } = renderHook(() => useChat());
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
    await act(async () => { result.current.send('q'); });
    const before = result.current.threadId;

    let ok = false;
    await act(async () => { ok = await result.current.deleteChat(); });

    expect(ok).toBe(true);
    expect(result.current.messages).toHaveLength(0);
    // 与 newChat 语义正交：删除不换 id（spec 决策 8）
    expect(result.current.threadId).toBe(before);
  });
```

- [ ] **Step 3: 跑测试确认失败**

```bat
npm run test -- src/test/useChat.test.ts
```

Expected: **FAIL**，新增 5 个用例全部失败，报错形如 `result.current.deleteChat is not a function`。既有 8 个用例应仍通过（证明 Step 1 的 mock 工厂修对了）。

- [ ] **Step 4: 实现 `useChat.ts`**

整文件替换为下面内容。**两处必须注意**：`CLEAR` 与 `LOAD_HISTORY` 分支原来直接返回字面量对象（没 `...state`），State 加了两个字段后会 **TS 报缺字段**，必须补 `...state`。

```ts
import { useCallback, useEffect, useReducer, useRef } from 'react';
import type { Step, Source, ErrorEvent } from '../api/types';
import { streamChat, fetchHistory, deleteThread } from '../api/client';
import { useThreadId } from './useThreadId';

export interface UserMsg { role: 'user'; content: string; }
export interface AssistantMsg {
  role: 'assistant'; steps: Step[]; answer: string; sources: Source[];
  status: 'streaming' | 'done' | 'error'; error?: string;
  /** false = 本轮未命中知识库（答案来自模型通用知识）；streaming 期间为 undefined */
  grounded?: boolean;
}
export type Msg = UserMsg | AssistantMsg;

type State = {
  messages: Msg[];
  streaming: boolean;
  /** 删除请求在飞：弹窗据此禁用按钮、忽略 Esc 与遮罩 */
  deleting: boolean;
  /** 删除失败的中文提示；成功或重新打开弹窗时清空 */
  deleteError: string | null;
};

type Action =
  | { type: 'PUSH_USER'; content: string }
  | { type: 'START_ASSISTANT' }
  | { type: 'ADD_STEP'; step: Step }
  | { type: 'APPEND_TOKEN'; text: string }
  | { type: 'SET_SOURCES'; sources: Source[] }
  | { type: 'FINISH'; grounded?: boolean }
  | { type: 'FAIL'; error: string }
  | { type: 'LOAD_HISTORY'; messages: Msg[] }
  | { type: 'CLEAR' }
  | { type: 'DELETE_START' }
  | { type: 'DELETE_OK' }
  | { type: 'DELETE_FAIL'; error: string }
  | { type: 'CLEAR_DELETE_ERROR' };

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
      // grounded 只在 done 事件里给；abort 路径不传，用 ?? 保住已有值不被 undefined 覆盖
      return { ...state, streaming: false, messages: updateLastAssistant(state.messages, a => ({ ...a, status: 'done', grounded: action.grounded ?? a.grounded })) };
    case 'FAIL':
      return { ...state, streaming: false, messages: updateLastAssistant(state.messages, a => ({ ...a, status: 'error', error: action.error })) };
    case 'LOAD_HISTORY':
      // 仅当当前无消息时回填：避免异步历史冲掉用户刚发起的对话，也优雅处理 StrictMode 双调用
      return state.messages.length === 0 ? { ...state, messages: action.messages, streaming: false } : state;
    case 'CLEAR':
      // ⚠ 必须 ...state：State 新增了 deleting/deleteError，漏掉会 TS 报缺字段
      return { ...state, messages: [], streaming: false };
    case 'DELETE_START':
      return { ...state, deleting: true, deleteError: null };
    case 'DELETE_OK':
      // 悲观时序（spec 决策 9）：只有后端确认成功才清空消息
      return { ...state, messages: [], streaming: false, deleting: false, deleteError: null };
    case 'DELETE_FAIL':
      // 失败保留 messages：删除不可逆，宁可让用户看到原样并重试
      return { ...state, deleting: false, deleteError: action.error };
    case 'CLEAR_DELETE_ERROR':
      return { ...state, deleteError: null };
  }
}

export function useChat() {
  const [state, dispatch] = useReducer(reducer, {
    messages: [], streaming: false, deleting: false, deleteError: null,
  });
  const { threadId, resetThreadId } = useThreadId();
  const abortRef = useRef<AbortController | null>(null);
  /** 在飞的 SSE promise。deleteChat 必须先 await 它再发 DELETE：否则被取消的 astream
   *  可能在 DELETE 之后才写入 checkpoint，留下删不掉的残行（spec §4）。 */
  const streamRef = useRef<Promise<void> | null>(null);

  useEffect(() => {
    let alive = true;
    fetchHistory(threadId)
      .then(h => {
        if (!alive) return;
        const msgs: Msg[] = h.messages.map(m =>
          m.role === 'user'
            ? { role: 'user', content: m.content }
            : { role: 'assistant', steps: [], answer: m.content, sources: m.sources ?? [], status: 'done',
                // 刷新后靠它恢复警示标识；后端未持久化时为 null → 转成 undefined 不渲染
                grounded: m.grounded ?? undefined },
        );
        dispatch({ type: 'LOAD_HISTORY', messages: msgs });
      })
      .catch(() => { /* 新会话或后端未就绪：静默 */ });
    return () => { alive = false; };
  }, [threadId]);

  const send = useCallback((question: string) => {
    const q = question.trim();
    if (!q || state.streaming) return;
    dispatch({ type: 'PUSH_USER', content: q });
    dispatch({ type: 'START_ASSISTANT' });
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    // catch 已消化所有异常（AbortError → FINISH，其余 → FAIL），故 pending 永不 reject，
    // deleteChat 里 await 它不会抛
    const pending: Promise<void> = streamChat({ question: q, thread_id: threadId }, {
      signal: ctrl.signal,
      onStep: e => dispatch({ type: 'ADD_STEP', step: e }),
      onToken: e => dispatch({ type: 'APPEND_TOKEN', text: e.text }),
      onSources: e => dispatch({ type: 'SET_SOURCES', sources: e.sources }),
      onDone: e => dispatch({ type: 'FINISH', grounded: e.grounded }),
      onError: (e: ErrorEvent) => dispatch({ type: 'FAIL', error: e.message }),
    }).catch((err: unknown) => {
      if ((err as Error)?.name === 'AbortError') dispatch({ type: 'FINISH' });
      else dispatch({ type: 'FAIL', error: '无法连接后端' });
    });
    streamRef.current = pending;
    void pending.finally(() => {
      // 只在还是自己时清空：避免旧流晚 settle 把新流的 ref 抹掉
      if (streamRef.current === pending) streamRef.current = null;
    });
  }, [state.streaming, threadId]);

  const abort = useCallback(() => { abortRef.current?.abort(); }, []);

  const newChat = useCallback(() => {
    abortRef.current?.abort();
    dispatch({ type: 'CLEAR' });
    resetThreadId();
  }, [resetThreadId]);

  /** 删除当前对话。返回 true = 后端已确认删除（App 据此决定关不关弹窗）。
   *  与 newChat 语义正交：newChat 换 thread_id 且不动服务端；
   *  deleteChat 清服务端且**保留** thread_id（spec 决策 8）。 */
  const deleteChat = useCallback(async (): Promise<boolean> => {
    dispatch({ type: 'DELETE_START' });
    abortRef.current?.abort();
    // 硬约束：先等在飞的流真正结束，再发 DELETE（spec 决策 7）
    const pending = streamRef.current;
    if (pending) { try { await pending; } catch { /* send() 内已消化 */ } }
    try {
      await deleteThread(threadId);      // deleted:false 同样算成功（幂等）
      dispatch({ type: 'DELETE_OK' });
      return true;
    } catch (err) {
      // fetch 网络层失败抛 TypeError；HTTP 非 2xx 由 deleteThread 抛 Error
      dispatch({ type: 'DELETE_FAIL',
                 error: err instanceof TypeError ? '无法连接后端' : '删除失败，请重试' });
      return false;
    }
  }, [threadId]);

  const clearDeleteError = useCallback(() => dispatch({ type: 'CLEAR_DELETE_ERROR' }), []);

  return {
    messages: state.messages, streaming: state.streaming,
    deleting: state.deleting, deleteError: state.deleteError,
    send, abort, newChat, deleteChat, clearDeleteError, threadId,
  };
}
```

- [ ] **Step 5: 跑测试确认通过**

```bat
npm run test -- src/test/useChat.test.ts
```

Expected: **PASS**，13 个用例（原 8 + 新 5），**且无 `act(...)` 警告**（若有警告说明有 dispatch 落在 act 外，检查 Step 2 里的 `await new Promise(r => setTimeout(r, 0))` 有没有漏）。

全量 + 类型检查：

```bat
npm run test
npx tsc --noEmit
```

Expected: **74 passed**（69 + 5）；`tsc` 零输出。

- [ ] **Step 6: 提交（需用户确认）**

```bat
git add frontend/src/hooks/useChat.ts frontend/src/test/useChat.test.ts
git commit -m "feat(frontend): useChat 新增 deleteChat（abort→await→DELETE 严格串行）"
```

---

### Task 4: `ConfirmDialog` 组件（TDD）

**Files:**
- Test: `frontend/src/test/confirm-dialog.test.tsx`（**新建**）
- Create: `frontend/src/components/ConfirmDialog.tsx`
- Modify: `frontend/src/index.css`（文件末尾追加 keyframes）

**Interfaces:**
- Consumes: 无（纯展示组件，不碰 API）
- Produces: `ConfirmDialog(props)`，props 为 `{ open, title, body, confirmLabel?, cancelLabel?, busyLabel?, busy?, error?, onConfirm, onCancel }`；`data-testid` 四个：`confirm-backdrop` / `confirm-dialog` / `confirm-cancel` / `confirm-ok` / `confirm-error`（共 5 个）。Task 5 的 `App.tsx` 与测试依赖这些名字。

- [ ] **Step 1: 写失败测试（新建文件）**

创建 `frontend/src/test/confirm-dialog.test.tsx`：

```tsx
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ConfirmDialog } from '../components/ConfirmDialog';

const base = {
  open: true,
  title: '删除当前对话？',
  body: '将同时删除服务端保存的历史，此操作不可恢复。',
  onConfirm: vi.fn(),
  onCancel: vi.fn(),
};

describe('ConfirmDialog', () => {
  it('open=false 时不渲染任何内容（含遮罩，避免拦截点击）', () => {
    const { container } = render(<ConfirmDialog {...base} open={false} />);
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByTestId('confirm-backdrop')).not.toBeInTheDocument();
  });

  it('初始焦点落在「取消」按钮（不可逆操作，防 Enter 误删）', () => {
    render(<ConfirmDialog {...base} />);
    expect(screen.getByTestId('confirm-cancel')).toHaveFocus();
  });

  it('Esc 触发 onCancel', () => {
    const onCancel = vi.fn();
    render(<ConfirmDialog {...base} onCancel={onCancel} />);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('点遮罩关闭，点面板本身不关闭（选中文字时不误触）', () => {
    const onCancel = vi.fn();
    render(<ConfirmDialog {...base} onCancel={onCancel} />);
    fireEvent.click(screen.getByTestId('confirm-dialog'));
    expect(onCancel).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('confirm-backdrop'));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('busy 时两按钮禁用、Esc 与遮罩均无效、确认文案变「删除中…」', () => {
    const onCancel = vi.fn();
    render(<ConfirmDialog {...base} busy onCancel={onCancel} />);
    expect(screen.getByTestId('confirm-ok')).toBeDisabled();
    expect(screen.getByTestId('confirm-cancel')).toBeDisabled();
    expect(screen.getByTestId('confirm-ok')).toHaveTextContent('删除中');
    fireEvent.keyDown(window, { key: 'Escape' });
    fireEvent.click(screen.getByTestId('confirm-backdrop'));
    expect(onCancel).not.toHaveBeenCalled();
  });

  it('error 非空时渲染错误行；点确认触发 onConfirm', () => {
    const onConfirm = vi.fn();
    render(<ConfirmDialog {...base} error="删除失败，请重试" onConfirm={onConfirm} />);
    expect(screen.getByTestId('confirm-error')).toHaveTextContent('删除失败，请重试');
    fireEvent.click(screen.getByTestId('confirm-ok'));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

```bat
npm run test -- src/test/confirm-dialog.test.tsx
```

Expected: **FAIL**，报 `Cannot find module '../components/ConfirmDialog'`（组件还不存在）。

- [ ] **Step 3: 新建 `ConfirmDialog.tsx`**

创建 `frontend/src/components/ConfirmDialog.tsx`：

```tsx
import { useEffect, useRef } from 'react';

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  body: string;
  confirmLabel?: string;
  cancelLabel?: string;
  busyLabel?: string;
  busy?: boolean;
  error?: string | null;
  onConfirm: () => void | Promise<void>;
  onCancel: () => void;
}

/** 通用确认模态框（不绑定「删除对话」语义，P3 删单条会话可复用）。
 *
 *  三条安全默认：
 *  ① 初始焦点落在「取消」——不可逆操作，防止用户按 Enter 直接确认删除；
 *  ② busy 期间 Esc / 遮罩 / 两个按钮全部失效——请求在飞时关窗会造成状态错乱；
 *  ③ 点面板本身不关闭（只有点遮罩才关），避免选中文本时误触。
 *
 *  z-50 高于知识库抽屉的 z-40（KnowledgeBaseDrawer.tsx:21），两者同时开时弹窗在上。
 *  Esc 监听写法与 KnowledgeBaseDrawer.tsx:12-17 一致（window keydown + 清理）。 */
export function ConfirmDialog({
  open, title, body,
  confirmLabel = '删除', cancelLabel = '取消', busyLabel = '删除中…',
  busy = false, error = null, onConfirm, onCancel,
}: ConfirmDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    cancelRef.current?.focus();                       // ①
  }, [open]);

  useEffect(() => {
    if (!open || busy) return;                        // ②
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, busy, onCancel]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-slate-900/40"
        data-testid="confirm-backdrop"
        onClick={() => { if (!busy) onCancel(); }}
      />
      <div
        role="dialog" aria-modal="true" aria-labelledby="confirm-dialog-title"
        data-testid="confirm-dialog"
        className="dialog-pop relative w-full max-w-sm rounded-lg bg-white p-5 shadow-xl"
      >
        <h2 id="confirm-dialog-title" className="text-sm font-semibold text-gray-900">{title}</h2>
        <p className="mt-2 text-xs leading-relaxed text-gray-600">{body}</p>
        {error && (
          <p
            data-testid="confirm-error"
            className="mt-3 rounded-md border border-red-200 bg-red-50 px-2 py-1.5 text-xs leading-relaxed text-red-700"
          >{error}</p>
        )}
        <div className="mt-5 flex justify-end gap-2">
          <button
            ref={cancelRef}
            data-testid="confirm-cancel"
            disabled={busy}
            onClick={onCancel}
            className="rounded-md border px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-50"
          >{cancelLabel}</button>
          <button
            data-testid="confirm-ok"
            disabled={busy}
            onClick={onConfirm}
            className="rounded-md bg-red-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-50"
          >{busy ? busyLabel : confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: `index.css` 加弹入动画**

在 `frontend/src/index.css` **末尾**追加（与既有 `kb-slide-in` 同一路子：原生 keyframes，不引动画依赖）：

```css
/* 确认弹窗轻微弹入：与 kb-slide-in 同一套路（原生 keyframes，不引额外动画依赖） */
@keyframes dialog-pop-in {
  from { opacity: 0; transform: scale(0.96); }
  to { opacity: 1; transform: scale(1); }
}
.dialog-pop { animation: dialog-pop-in 0.14s ease-out; }
```

- [ ] **Step 5: 跑测试确认通过**

```bat
npm run test -- src/test/confirm-dialog.test.tsx
npm run test
```

Expected: 单文件 **6 passed**；全量 **80 passed**（74 + 6）。

若「初始焦点」那条失败：多半是 `useEffect` 里 focus 时机问题，确认 `cancelRef` 挂在 `confirm-cancel` 按钮上、且 effect 依赖是 `[open]`。

- [ ] **Step 6: 提交（需用户确认）**

```bat
git add frontend/src/components/ConfirmDialog.tsx frontend/src/test/confirm-dialog.test.tsx frontend/src/index.css
git commit -m "feat(frontend): 新增通用 ConfirmDialog（焦点落取消、busy 锁死、就地显示错误）"
```

---

### Task 5: Header 按钮 + App 接线（TDD）

**Files:**
- Test: `frontend/src/test/app.test.tsx:4-15`（mock 工厂）、`:22-31`（beforeEach）、文件末尾追加 3 用例
- Modify: `frontend/src/components/Header.tsx`（整文件重写，30 → 约 44 行）
- Modify: `frontend/src/App.tsx`（整文件重写，39 → 约 55 行）

**Interfaces:**
- Consumes: Task 3 的 `deleteChat` / `deleting` / `deleteError` / `clearDeleteError`；Task 4 的 `ConfirmDialog`
- Produces: `Header` 新增两个 props —— `onDeleteChat: () => void`、`canDelete: boolean`；新增 `data-testid="btn-delete-chat"`。

- [ ] **Step 1: 先扩 mock 工厂 + 写 3 个失败测试**

同 Task 3：`App`  transitively import `deleteThread`，**mock 工厂不补就会让现有 4 个 App 用例全炒**。

把 `frontend/src/test/app.test.tsx` 的第 4-15 行替换为：

```tsx
const { streamChatMock, fetchHistoryMock, uploadDocumentMock, fetchDocumentsMock, deleteDocumentMock, deleteThreadMock } = vi.hoisted(() => ({
  streamChatMock: vi.fn(), fetchHistoryMock: vi.fn(),
  uploadDocumentMock: vi.fn(), fetchDocumentsMock: vi.fn(), deleteDocumentMock: vi.fn(),
  deleteThreadMock: vi.fn(),
}));
// ⚠ App 依赖 client 的全部导出（聊天 3 + KB 3）；mock 工厂必须全部提供，否则挂载即 TypeError
vi.mock('../api/client', () => ({
  streamChat: streamChatMock,
  fetchHistory: fetchHistoryMock,
  deleteThread: deleteThreadMock,
  uploadDocument: uploadDocumentMock,
  fetchDocuments: fetchDocumentsMock,
  deleteDocument: deleteDocumentMock,
}));
```

在 `beforeEach`（当前第 22-31 行）里补两行（加在 `deleteDocumentMock.mockReset();` 之后、`fetchHistoryMock.mockResolvedValue(...)` 之前）：

```tsx
  deleteThreadMock.mockReset();
  deleteThreadMock.mockResolvedValue({ thread_id: 't', deleted: true });
```

然后在文件末尾追加一个工具函数与 3 个用例（放在 `describe('App 集成', ...)` 内部最后）：

```tsx
  /** 发一条消息并等它完成，让删除按钮从禁用变可用 */
  async function askOnce(question = '什么是 LangChain') {
    streamChatMock.mockImplementation(async (_b: unknown, h: any) => {
      h.onToken({ text: '你好' });
      h.onDone({ thread_id: 't', rewrites: 0, grounded: true });
    });
    fireEvent.change(screen.getByPlaceholderText('输入问题…'), { target: { value: question } });
    fireEvent.click(screen.getByText('发送'));
    await waitFor(() => expect(screen.getByText(question)).toBeInTheDocument());
  }

  it('空会话时删除按钮禁用，发一条消息后启用', async () => {
    render(<App />);
    await flush();
    expect(screen.getByTestId('btn-delete-chat')).toBeDisabled();
    await askOnce();
    await waitFor(() => expect(screen.getByTestId('btn-delete-chat')).not.toBeDisabled());
  });

  it('点删除 → 弹窗 → 确认 → 调 DELETE、消息清空、弹窗关闭', async () => {
    render(<App />);
    await flush();
    await askOnce('待删的问题');
    expect(screen.queryByTestId('confirm-dialog')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('btn-delete-chat'));
    await waitFor(() => expect(screen.getByTestId('confirm-dialog')).toBeInTheDocument());
    expect(screen.getByTestId('confirm-cancel')).toHaveFocus();      // 安全默认：焦点在取消

    fireEvent.click(screen.getByTestId('confirm-ok'));
    await waitFor(() => expect(deleteThreadMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByTestId('confirm-dialog')).not.toBeInTheDocument());
    expect(screen.queryByText('待删的问题')).not.toBeInTheDocument();  // 消息已清
  });

  it('删除失败时弹窗保持打开、显示错误行、消息不清空', async () => {
    deleteThreadMock.mockRejectedValue(new Error('delete thread HTTP 500'));
    render(<App />);
    await flush();
    await askOnce('不能丢的问题');

    fireEvent.click(screen.getByTestId('btn-delete-chat'));
    fireEvent.click(screen.getByTestId('confirm-ok'));

    await waitFor(() => expect(screen.getByTestId('confirm-error')).toHaveTextContent('删除失败，请重试'));
    expect(screen.getByTestId('confirm-dialog')).toBeInTheDocument();     // 没关，可重试
    expect(screen.getByText('不能丢的问题')).toBeInTheDocument();          // 悲观：消息保留
  });
```

- [ ] **Step 2: 跑测试确认失败**

```bat
npm run test -- src/test/app.test.tsx
```

Expected: **FAIL**。新增 3 个用例失败（报 `Unable to find an element by: [data-testid="btn-delete-chat"]`）；**既有 4 个用例仍通过**（证明 mock 工厂补对了）。若既有 4 个也失败，先回 Step 1 检查工厂里是不是漏了 `deleteThread`。

- [ ] **Step 3: 重写 `Header.tsx`**

整文件替换为：

```tsx
/** 顶部栏：标题 + 知识库入口（带进行中角标）+ 新对话 + 删除对话
 *
 *  按钮顺序刻意把危险操作放最右，且与「新对话」相邻，便于对比两者语义：
 *    ＋ 新对话   = 开一段新的（换 thread_id，旧的留着）
 *    🗑 删除对话 = 把当前这段彻底清掉（调 DELETE，保留 thread_id）
 *  窄屏只留图标（hidden sm:inline），避免四个元素挤爆 Header。 */
export function Header({ onNewChat, onOpenKb, onDeleteChat, canDelete, kbBadge }: {
  onNewChat: () => void;
  onOpenKb: () => void;
  onDeleteChat: () => void;
  /** 空会话时禁用删除：没东西可删，不必发一次无意义请求 */
  canDelete: boolean;
  kbBadge: number;
}) {
  return (
    <header className="flex items-center justify-between border-b bg-white px-4 py-3">
      <h1 className="text-sm font-semibold text-gray-800">🤖 LangChain 智能问答</h1>
      <div className="flex items-center gap-2">
        <button
          onClick={onOpenKb}
          className="relative rounded-md border px-3 py-1 text-xs text-gray-600 hover:bg-gray-100"
        >
          📚 知识库
          {kbBadge > 0 && (
            <span
              data-testid="kb-badge"
              className="absolute -right-1.5 -top-1.5 rounded-full bg-blue-500 px-1 text-[10px] leading-4 text-white"
            >↑{kbBadge}</span>
          )}
        </button>
        <button onClick={onNewChat} className="rounded-md border px-3 py-1 text-xs text-gray-600 hover:bg-gray-100">
          ＋ 新对话
        </button>
        <button
          onClick={onDeleteChat}
          disabled={!canDelete}
          data-testid="btn-delete-chat"
          title={canDelete ? '删除当前对话（不可恢复）' : '当前没有可删除的对话'}
          className="rounded-md border border-red-200 px-3 py-1 text-xs text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:border-gray-200 disabled:text-gray-300 disabled:hover:bg-transparent"
        >
          🗑<span className="hidden sm:inline"> 删除对话</span>
        </button>
      </div>
    </header>
  );
}
```

> ⚠ 删除按钮的文本被 `<span>` 拆开（`🗑` + ` 删除对话`），所以测试**必须用 `getByTestId('btn-delete-chat')`**，`getByText('🗑 删除对话')` 匹配不到。既有用例里的 `getByText('📚 知识库')` 不受影响（那个按钮文本仍是单一节点）。

- [ ] **Step 4: 重写 `App.tsx`**

整文件替换为：

```tsx
import { useState } from 'react';
import { useChat } from './hooks/useChat';
import { useKnowledgeBase } from './hooks/useKnowledgeBase';
import { Header } from './components/Header';
import { ChatWindow } from './components/ChatWindow';
import { Composer } from './components/Composer';
import { KnowledgeBaseDrawer } from './components/KnowledgeBaseDrawer';
import { UploadZone } from './components/UploadZone';
import { DocList } from './components/DocList';
import { ConfirmDialog } from './components/ConfirmDialog';

export default function App() {
  const {
    messages, streaming, send, abort, newChat,
    deleteChat, deleting, deleteError, clearDeleteError,
  } = useChat();
  // 知识库状态住在这里（而非抽屉内部），所以关掉抽屉上传仍继续跑（契约 §4.9）
  const kb = useKnowledgeBase();
  // 弹窗开关也住 App 层（与 KB 抽屉同一层级决策）：ConfirmDialog 自身无状态，
  // 失败时靠 deleteError 留在弹窗里显示，成功才关
  const [confirmDelete, setConfirmDelete] = useState(false);
  return (
    <div className="flex h-screen flex-col bg-gray-50">
      <Header
        onNewChat={newChat}
        onOpenKb={kb.openKb}
        kbBadge={kb.activeCount}
        canDelete={messages.length > 0 || streaming}
        onDeleteChat={() => { clearDeleteError(); setConfirmDelete(true); }}
      />
      <ChatWindow messages={messages} />
      <Composer streaming={streaming} onSend={send} onStop={abort} />
      <KnowledgeBaseDrawer open={kb.open} onClose={kb.closeKb}>
        <UploadZone
          uploads={kb.uploads}
          onFiles={kb.addFiles}
          onCancel={kb.cancelUpload}
          onDismiss={kb.dismiss}
        />
        <DocList
          documents={kb.documents}
          builtin={kb.builtin}
          loading={kb.loadingList}
          error={kb.listError}
          deleteError={kb.docError}
          onRefresh={kb.refresh}
          onDelete={kb.removeDoc}
        />
      </KnowledgeBaseDrawer>
      <ConfirmDialog
        open={confirmDelete}
        title="删除当前对话？"
        body="将同时删除服务端保存的历史，此操作不可恢复。"
        busy={deleting}
        error={deleteError}
        onConfirm={async () => { if (await deleteChat()) setConfirmDelete(false); }}
        onCancel={() => setConfirmDelete(false)}
      />
    </div>
  );
}
```

注意：`ConfirmDialog` 渲染在 `KnowledgeBaseDrawer` **之后**，且 `z-50 > z-40`，所以两者同时开时弹窗在上。

- [ ] **Step 5: 跑测试 + 构建**

```bat
npm run test -- src/test/app.test.tsx
npm run test
npm run build
```

Expected:
- 单文件 **7 passed**（原 4 + 新 3）
- 全量 **83 passed**（80 + 3），**无 act 警告**
- `build`：`tsc --noEmit` 零错 + vite 构建成功（模块数约 529，JS 体积变化应 < 2kB）

- [ ] **Step 6: 提交（需用户确认）**

```bat
git add frontend/src/components/Header.tsx frontend/src/App.tsx frontend/src/test/app.test.tsx
git commit -m "feat(frontend): Header 新增删除对话按钮 + App 接入确认弹窗"
```

---

### Task 6: mock server 扩展 + 端到端验证 + 文档

**Files:**
- Modify: `frontend/mock/server.mjs`（5 处）
- Create then delete: `frontend/_e2e_delete.mjs`（临时验证脚本）
- Modify: `frontend/README.md`

**Interfaces:**
- Consumes: Task 1 的契约
- Produces: mock 具备真实的「按 thread 存历史 + 幂等删除」语义，前端无需真后端即可跑全流程

- [ ] **Step 1: `mock/server.mjs` 五处修改**

**① 第 1 行注释**（端点数 6 → 7）：

```js
// 无真实后端时联调用。启动: npm run mock (监听 :8000，实现全部 7 个端点)
```

**② 在 `const sources = [...]`（当前第 13 行）之后插入**：

```js
// 按 thread_id 存历史：让 GET /api/chat/history 与 DELETE /api/chat/threads/{id} 有真实语义。
// Map.delete() 返回「是否真的删掉了」，恰好就是契约里的 deleted 字段。
const threads = new Map();
```

**③ 替换 `GET /api/chat/history` 处理块**（当前第 49-52 行）：

```js
  if (req.method === 'GET' && url.pathname === '/api/chat/history') {
    const tid = url.searchParams.get('thread_id') ?? '';
    // 未知/已删除的 thread → 200 + 空数组（契约：不报 404）
    json(res, 200, { thread_id: tid, messages: threads.get(tid) ?? [] });
    return;
  }
```

**④ 在 `POST /api/chat/stream` 的 `res.end();`（当前第 74 行）之前插入**：

```js
    // 记录本轮问答，让 history 与 delete 有真实语义
    const hist = threads.get(thread_id) ?? [];
    hist.push({ role: 'user', content: question });
    hist.push({
      role: 'assistant',
      content: ungrounded
        ? '⚠ 本回答未命中知识库，基于模型通用知识（模拟）。'
        : tokens.join(''),
      sources: ungrounded ? [] : sources,
      grounded: !ungrounded,
    });
    threads.set(thread_id, hist);
```

**⑤ 在 stream 处理块结束（当前第 76 行 `}`）之后、KB 列表块之前插入**：

```js
  // ---------- 聊天: 删除会话（幂等） ----------
  const delThread = /^\/api\/chat\/threads\/([^/]+)$/.exec(url.pathname);
  if (req.method === 'DELETE' && delThread) {
    const id = decodeURIComponent(delThread[1]);
    // Map.delete 的返回值就是契约的 deleted：本来不存在 → false，仍是 200（幂等，不报 404）
    json(res, 200, { thread_id: id, deleted: threads.delete(id) });
    return;
  }
```

**⑥ 最后一行日志**（当前第 148 行）：

```js
server.listen(PORT, () => console.log(`[mock] SSE 服务已起: http://localhost:${PORT}（7 个端点）`));
```

- [ ] **Step 2: 写临时端到端验证脚本**

创建 `frontend/_e2e_delete.mjs`（**验证完就删**）：

```js
// 临时：验证 mock server 的删除会话语义是否符合契约。用完即删。
const BASE = 'http://localhost:8000';
const T = 't-e2e-' + Date.now();
const j = async (r) => ({ status: r.status, body: await r.json().catch(() => null) });
const results = [];
const check = (name, cond, extra = '') => {
  results.push(cond);
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${extra ? '   ' + extra : ''}`);
};

let r = await j(await fetch(`${BASE}/api/chat/history?thread_id=${T}`));
check('history(未知) → 200 + 空数组', r.status === 200 && r.body.messages.length === 0, JSON.stringify(r.body));

r = await j(await fetch(`${BASE}/api/chat/threads/${T}`, { method: 'DELETE' }));
check('DELETE(未知) → 200 deleted=false（幂等，不报 404）', r.status === 200 && r.body.deleted === false, JSON.stringify(r.body));

const res = await fetch(`${BASE}/api/chat/stream`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ question: 'what is LangChain', thread_id: T }),
});
const text = await res.text();
check('stream 事件序完整 step→token→sources→done',
  /event: step[\s\S]*event: token[\s\S]*event: sources[\s\S]*event: done/.test(text),
  `frames=${(text.match(/^event:/gm) || []).length}`);

r = await j(await fetch(`${BASE}/api/chat/history?thread_id=${T}`));
check('history 现有 user+assistant 两条',
  r.body.messages.length === 2 && r.body.messages[0].role === 'user' && r.body.messages[1].role === 'assistant',
  `roles=${r.body.messages.map(m => m.role).join(',')}`);

r = await j(await fetch(`${BASE}/api/chat/threads/${T}`, { method: 'DELETE' }));
check('DELETE(存在) → 200 deleted=true', r.status === 200 && r.body.deleted === true, JSON.stringify(r.body));

r = await j(await fetch(`${BASE}/api/chat/history?thread_id=${T}`));
check('删除后 history → 200 + 空数组（不是 404）', r.status === 200 && r.body.messages.length === 0);

r = await j(await fetch(`${BASE}/api/chat/threads/${T}`, { method: 'DELETE' }));
check('重复 DELETE → 仍 200 deleted=false', r.status === 200 && r.body.deleted === false);

const T2 = T + '-u';
await (await fetch(`${BASE}/api/chat/stream`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ question: 'ungrounded question', thread_id: T2 }),
})).text();
r = await j(await fetch(`${BASE}/api/chat/history?thread_id=${T2}`));
check('grounded=false 的回答也正确入历史', r.body.messages[1]?.grounded === false,
  `grounded=${r.body.messages[1]?.grounded}`);

const failed = results.filter(x => !x).length;
console.log(`\n端到端：${results.length} 项，${failed} 项失败`);
process.exit(failed ? 1 : 0);
```

- [ ] **Step 3: 起 mock 并跑验证**

后台起 mock（另一个终端）：

```bat
cd /d D:\PycharmProjects\my_langchain_demo\advanced_tutorial\rag_qa_project\frontend && npm run mock
```

再跑验证：

```bat
cd /d D:\PycharmProjects\my_langchain_demo\advanced_tutorial\rag_qa_project\frontend && node _e2e_delete.mjs
```

Expected: **8 项全 PASS**，末尾输出 `端到端：8 项，0 项失败`，退出码 0。

若失败，先看 mock 终端有没有报语法错（五处修改里④⑤ 两块插入位置最容易错：④ 必须在 `res.end()` 之前，⑤ 必须在 stream 块之外）。

- [ ] **Step 4: 浏览器手动验收（走 Vite proxy）**

保持 mock 在跑，另开一个终端：

```bat
cd /d D:\PycharmProjects\my_langchain_demo\advanced_tutorial\rag_qa_project\frontend && npm run dev
```

打开 `http://localhost:5173`，依次验收：

| # | 操作 | 预期 |
|---|---|---|
| 1 | 刚进页面（无消息） | 「🗑 删除对话」**置灰禁用**，hover 提示「当前没有可删除的对话」 |
| 2 | 问一句 | 回答流式出现，删除按钮**变可用** |
| 3 | 点删除 | 模态框弹出，**焦点在「取消」** |
| 4 | 按 Esc | 弹窗关闭，消息**还在** |
| 5 | 再点删除 → 点遮罩 | 弹窗关闭，消息还在 |
| 6 | 再点删除 → 点「删除」 | 弹窗关闭、消息区清空、**thread_id 不变**（DevTools → Application → localStorage 的 `ragqa_thread_id` 跟之前一样） |
| 7 | 刷新页面 | 消息区仍为空（history 已真的被删） |
| 8 | 流式输出**进行中**点删除→确认 | 流被中断、弹窗关闭、消息清空（验 `abort → await → DELETE` 串行） |
| 9 | 点「＋ 新对话」 | 消息清空且 **thread_id 变了**（与删除语义正交） |
| 10 | 窄屏（<640px） | 删除按钮只剩 🗑 图标，Header 不换行 |

- [ ] **Step 5: 删掉临时脚本、关掉两个服务**

删除 `frontend/_e2e_delete.mjs`，并结束 `npm run mock` 与 `npm run dev` 两个进程（按端口找 PID 再 `taskkill /F /PID`）。

- [ ] **Step 6: 更新 `frontend/README.md`**

**① 第 3 行**末尾补一句：

```markdown
单栅聊天 SPA：`fetch + ReadableStream` 消费后端 SSE（step/token/sources/done/error），`thread_id` 驱动服务端多轮记忆，挂载时拉 history 回填。Header 的「📚 知识库」打开右侧抽屉，可上传自己的文档进知识库；「🗑 删除对话」经二次确认后清掉服务端与本地的当前会话。
```

**② 第 15 行** mock 说明的端点数 6 → 7：

```markdown
npm run mock         # 起一个 :8000 的假后端(实现全部 7 个端点，含知识库与会话的内存态增删查)
```

**③ 在「## 知识库」章节之后、「## 测试」之前插入新章节**：

````markdown
## 删除对话

Header 最右的「🗑 删除对话」（空会话时禁用）弹二次确认模态框，确认后：

- 调 `DELETE /api/chat/threads/{thread_id}` 清掉**服务端**的会话状态（checkpointer 里该 thread 的全部 checkpoint）；
- 成功后清空消息区，**但保留同一个 `thread_id`**——下次提问 LangGraph 会自动重建。

与「＋ 新对话」的区别（两者语义正交）：

| 按钮 | 语义 | 服务端 | `thread_id` |
|---|---|---|---|
| ＋ 新对话 | 开一段新的，旧的留着 | 不动 | **换新** |
| 🗑 删除对话 | 把当前这段彻底清掉 | 调 DELETE 真删 | **保留** |

几个行为细节：

- **流式输出中也能删**：严格串行 `abort() → 等流真正结束 → DELETE`。不等的话，被取消的请求可能在删除之后才写入 checkpoint，留下删不掉的残行。
- **删除失败不清空**：只有后端确认成功才清消息（删除不可逆，宁可让用户重试）；失败时弹窗保持打开并显示错误行。
- **幂等**：后端对未知 `thread_id` 返回 `200 {deleted:false}`（不是 404），前端一律当成功处理。
- **安全默认**：弹窗打开时焦点落在「取消」，避免按 Enter 误删；请求在飞时 Esc / 遮罩 / 两个按钮全部失效。

契约见 [`../docs/api/README.md`](../docs/api/README.md) 的「DELETE /api/chat/threads/{thread_id}」章节。
````

**④ 第 41 行**测试说明补上删除对话：

```markdown
npm run test         # Vitest 全量(client 解析 / hooks 状态机 / 知识库队列 / 确认弹窗 / 组件 / App 集成)
```

- [ ] **Step 7: 四方一致性最终校验**

创建临时脚本 `frontend/_verify_consistency.cjs`（用完即删）：

```js
// 临时：契约 ↔ types ↔ client ↔ mock ↔ README 五方一致性。用完即删。
const fs = require('fs');
const R = 'D:/PycharmProjects/my_langchain_demo/advanced_tutorial/rag_qa_project';
const read = p => fs.readFileSync(p, 'utf8');
const yaml = read(R + '/docs/api/openapi.yaml');
const md = read(R + '/docs/api/README.md');
const types = read(R + '/frontend/src/api/types.ts');
const client = read(R + '/frontend/src/api/client.ts');
const mock = read(R + '/frontend/mock/server.mjs');
const feReadme = read(R + '/frontend/README.md');

const rows = [
  ['openapi 有 threads path', yaml.includes('/api/chat/threads/{thread_id}:')],
  ['openapi 有 ChatDeleteResponse', yaml.includes('ChatDeleteResponse:')],
  ['openapi required 两项', /ChatDeleteResponse:[\s\S]{0,120}required: \[thread_id, deleted\]/.test(yaml)],
  ['README 有 DELETE 章节', md.includes('## DELETE /api/chat/threads/{thread_id}')],
  ['README 端点表 7 行', (md.match(/^\| `(GET|POST|DELETE|PATCH)`/gm) || []).length === 7],
  ['README 第1行无手误', md.split(/\r?\n/)[0] === '# LangChain 智能问答 API 契约'],
  ['README 说明幂等 200', md.includes('deleted": false') || md.includes('deleted\": false') || /deleted.*false/.test(md)],
  ['types 有 ChatDeleteResponse', types.includes('export interface ChatDeleteResponse')],
  ['types 字段一致', /ChatDeleteResponse \{ thread_id: string; deleted: boolean; \}/.test(types)],
  ['client 有 deleteThread', client.includes('export async function deleteThread')],
  ['client 路径正确', client.includes('/chat/threads/${encodeURIComponent(threadId)}')],
  ['client 用 DELETE', /deleteThread[\s\S]{0,220}method: 'DELETE'/.test(client)],
  ['mock 有 threads Map', mock.includes('const threads = new Map()')],
  ['mock 有 DELETE 路由', mock.includes('/api\\/chat\\/threads\\/')],
  ['mock deleted 取自 Map.delete', mock.includes('deleted: threads.delete(id)')],
  ['mock 日志 7 个端点', mock.includes('（7 个端点）')],
  ['前端 README 有删除章节', feReadme.includes('## 删除对话')],
];
let bad = 0;
for (const [n, ok] of rows) { if (!ok) bad++; console.log(`${ok ? 'OK  ' : 'FAIL'} ${n}`); }
console.log(`\n一致性：${rows.length} 项，${bad} 项不符`);
```

Run：

```bat
cd /d D:\PycharmProjects\my_langchain_demo\advanced_tutorial\rag_qa_project\frontend && node _verify_consistency.cjs
```

Expected: **17 项全 OK**，`一致性：17 项，0 项不符`。跑完删掉该脚本。

- [ ] **Step 8: 全量最终验证**

```bat
cd /d D:\PycharmProjects\my_langchain_demo\advanced_tutorial\rag_qa_project\frontend && npm run test && npm run build
```

Expected: **83 passed**、build 零错。再用 IDE 的 Problems 面板（或 `GetProblems`）确认 6 个改动/新建的源码文件零报错：`types.ts`、`client.ts`、`useChat.ts`、`ConfirmDialog.tsx`、`Header.tsx`、`App.tsx`。

- [ ] **Step 9: 提交（需用户确认）**

```bat
git add frontend/mock/server.mjs frontend/README.md
git commit -m "feat(mock): 补 DELETE /api/chat/threads 与按 thread 存储的历史"
```

并确认临时脚本未被误提交：

```bat
git status --porcelain
```

Expected: 输出为空（或仅剩 `backend/` 下用户自己的改动），**不得出现 `_e2e_delete.mjs` / `_verify_consistency.cjs`**。

---

## Definition of Done

| # | 验收项 | 如何确认 |
|---|---|---|
| 1 | 契约完整且可解析 | Task 1 Step 6 两段验证输出均符合预期（`threads_path=True`、`endpoint_rows=7`、`stray_at=false`） |
| 2 | 前端全量测试绿 | `npm run test` → **83 passed**（原 66 + 新 17），**无 act 警告** |
| 3 | 既有断言未被修改 | `git diff` 里 `useChat.test.ts` / `app.test.tsx` 只多了 mock 工厂的两行与新用例，**原有用例一行未改** |
| 4 | 类型与构建绿 | `npm run build`（含 `tsc --noEmit`）零错 |
| 5 | 端到端语义正确 | Task 6 Step 3 的 **8 项全 PASS**（尤其：未知 id 删除返 `deleted:false` 而非 404） |
| 6 | 浏览器行为符合设计 | Task 6 Step 4 的 **10 步全部符合**（尤其第 6 步 thread_id 不变、第 8 步流式中可删、第 9 步新对话会换 id） |
| 7 | 五方一致 | Task 6 Step 7 的 **17 项全 OK** |
| 8 | 无依赖新增 | `git diff frontend/package.json` 为空（本期不动依赖） |
| 9 | 聊天链路未误伤 | `ChatWindow.tsx` / `Composer.tsx` / `MessageBubble.tsx` / `AnswerMarkdown.tsx` / `useThreadId.ts` 在 `git diff --stat` 里**不出现** |
| 10 | 无临时文件残留 | `git status --porcelain` 不含 `_e2e_delete.mjs` / `_verify_consistency.cjs` |
| 11 | 后端交接清单已给 | 下方章节完整，用户可据此独立实现 |

---

## 交接给用户（后端实现清单）

以下是 **P1 的后端部分**，不在上述 Task 里（执行者不要碰 `backend/`）。详细理由与证据见 spec §6。

### 1. 依赖

```bat
uv add langgraph-checkpoint-sqlite
```

`requirements.txt` 同步加一行 `langgraph-checkpoint-sqlite`。（已核实：`uv.lock` 里目前 **0 命中** `langgraph-checkpoint-sqlite`，必须新装。`sqlalchemy` / `aiosqlite` / `bcrypt` / `pyjwt` 已在，那些是 P2 的事。）

### 2. `config.py` 新增配置

在 `Settings` 的「---- 路径 ----」段（当前第 51-58 行）末尾加，风格跟 `docs_dir` / `uploads_dir` 一致：

```python
    # 会话状态（LangGraph checkpoint）落盘处。InMemorySaver 重启即失，
    # 换 SqliteSaver 后「删除对话」才有意义、历史才能跨重启存活。
    checkpoint_db: Path = data_dir / "checkpoints.db"
```

### 3. `backend/app/agent/graph.py` 三处改

```python
import sqlite3
from langgraph.checkpoint.sqlite import SqliteSaver   # 取代 InMemorySaver 的 import


@lru_cache(maxsize=1)
def get_checkpointer():
    """SQLite checkpointer 单例。约束 ①②③ 见下方注释（第 ④ 条在 build_graph），缺一即出问题。"""
    # ① 不能用 `with SqliteSaver.from_conn_string(path)`：它是上下文管理器，
    #    在 @lru_cache 函数里 with 会在函数返回时关掉连接，后续请求全部报错。
    # ② check_same_thread=False 必须加：FastAPI 会把同步 checkpointer 操作
    #    丢进线程池执行，不加会报 "SQLite objects created in a thread can only
    #    be used in that same thread"。
    settings.checkpoint_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(settings.checkpoint_db), check_same_thread=False)
    saver = SqliteSaver(conn)
    # ③ 首次使用必须 setup() 建表（官方 add-memory.md:1698-1704：
    #    DB-backed saver 需先跑迁移）。该调用幂等，每次启动执行即可。
    saver.setup()
    return saver
```

`build_graph` 签名把**从未被使用的死参数** `thread_id` 换成 `checkpointer`（当前第 201 行），末行（当前第 222 行）改为：

```python
def build_graph(model=None, retriever=None, checkpointer=None):
    # ↑ 只改签名：把从未被使用的死参数 thread_id 换成 checkpointer。
    # 函数体从 "model = model or get_model()" 到 "b.add_edge(\"generate\", END)"
    # （当前 graph.py:205-221）**一行不改**，只改最后的 return：
    # ④ 默认走 SQLite 单例；测试必须显式传 InMemorySaver（见第 4 条）
    return b.compile(checkpointer=checkpointer or get_checkpointer())
```

`get_graph()`（当前第 231-233 行）保持 `@lru_cache` 不变，只改注释为「SqliteSaver 跨请求、跨重启保留 thread 状态」。

### 4. ⚠ 测试必须注入 InMemorySaver（不做会污染真实数据库）

现有 6 处 `build_graph(...)` 调用**都没传 checkpointer**：`tests/test_graph.py:130, 140, 151, 162, 174` 与 `tests/test_chat_stream.py:51`。默认值一旦变成 `SqliteSaver`，**32 个测试会全部写入真实 `data/checkpoints.db` 并因 thread_id 相同而互相污染**。

每处补上：

```python
from langgraph.checkpoint.memory import InMemorySaver
app = build_graph(model=..., retriever=..., checkpointer=InMemorySaver())
```

同仓库 `pro_rag_service` 已是这个约定（`pro_rag_service/tests/test_graph.py:65`），照抄即可。

### 5. `backend/app/services/chat_service.py` 新增

```python
async def delete_chat_thread(thread_id: str) -> dict:
    """删除一个会话的全部 checkpoint。幂等：不存在也返回 deleted=False。"""
    graph = get_graph()
    config = {"configurable": {"thread_id": thread_id}}
    # delete_thread 返回 None，拿不到"是否真删了"，所以先探测存在性
    existed = await asyncio.to_thread(graph.checkpointer.get_tuple, config) is not None
    # SQLite 操作是阻塞的，丢线程池，别卡住事件循环
    await asyncio.to_thread(graph.checkpointer.delete_thread, thread_id)
    return {"thread_id": thread_id, "deleted": existed}
```

（需在文件顶部补 `import asyncio`。）

### 6. `backend/app/api/chat_router.py` 新增

```python
@router.delete("/threads/{thread_id}")
async def delete_thread(thread_id: str):
    """删除会话（契约 DELETE /api/chat/threads/{thread_id}）。

    幂等：未知/已删过的 id 返回 200 + deleted=false，**不是 404**——
    thread_id 由前端本地生成，"不存在"是常态（清 localStorage / 换浏览器 / 重复点）。
    用 str 而非 UUID，与现有 GET /history 一致（否则要多处理一个 422 分支）。
    """
    return await chat_service.delete_chat_thread(thread_id)
```

顺手清掉两个旧端点里已失效的 `# TODO` 注释与多余的 `pass`（当前第 12、23、25 行）。

### 7. `.gitignore` 补一条

`advanced_tutorial/.gitignore` 现在只排了 `*.sqlite3`（第 38 行）与精确文件名 `.llm_cache.db`（第 47 行），**没有 `*.db` 通配** → 新的 `data/checkpoints.db` 会被提交进仓库。在「向量库与派生数据」段补：

```
*.db
```

### 8. 验收清单（手工，7 步）

| # | 步骤 | 预期 |
|---|---|---|
| 1 | 起后端 → 问一句 → `GET /api/chat/history?thread_id=X` | `messages` 有 2 条 |
| 2 | **重启后端进程** → 再查 history | **仍有 2 条**（P1 核心验收：持久化生效） |
| 3 | `DELETE /api/chat/threads/X` | `200 {"deleted": true}` |
| 4 | `GET /api/chat/history?thread_id=X` | `200 {"messages": []}` |
| 5 | 再 `DELETE` 同一 id | `200 {"deleted": false}`（幂等，非 404） |
| 6 | 同一 id 再问一句 | 正常作答，且 history 只有本轮 2 条（旧历史确已清除） |
| 7 | `git status` | 不显示 `data/checkpoints.db` |

### 9. 建议的 pytest 用例（5 个）

1. `test_delete_thread_returns_deleted_true` — 先跑一轮对话，删除返回 `deleted=True`
2. `test_delete_thread_is_idempotent` — 连删两次，第二次 `deleted=False`，均不抛异常
3. `test_history_empty_after_delete` — 删除后 `get_chat_history` 返回空 `messages`
4. `test_delete_unknown_thread_returns_false` — 从未使用过的 uuid → `deleted=False`
5. `test_sqlite_saver_survives_rebuild` — 用 `tmp_path` 下的 db 文件建 saver，写入后**重建** saver 与图，历史仍在（验证真持久化，而非内存假象）

测试里用 `monkeypatch.setattr(chat_service, "get_graph", lambda: app)`（沿用 `tests/test_chat_stream.py:53` 的既有手法），避免碰真实 db 文件。

### 10. 一个已知限制

SQLite 单文件 + 写锁，**多 worker 部署会锁库**（`uvicorn --workers 2` 以上）。当前单 worker 开发无影响；将来上多 worker 按官方建议迁 `PostgresSaver`（`persistence.md:67-70`），`delete_thread` 是 saver 通用接口，**本契约与前端零改动**。

---

## 后续阶段

- **P2 账号鉴权**：`docs/superpowers/specs/2026-09-13-p2-account-auth-design.md`（会给本期三个 chat 端点加鉴权与归属校验）
- **P3 会话列表 + KB 隔离**：`docs/superpowers/specs/2026-09-13-p3-conversation-list-kb-isolation-design.md`（会把本期保留的单一 `thread_id` 扩成侧栏多会话，并复用本期的 `ConfirmDialog`）

本期刻意**不做**的（spec §2 非目标）：会话列表、账号、删除单条消息、toast 系统、批量清空、KB 任何改动。

