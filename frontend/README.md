# 前端 · LangChain 智能问答（React + Vite + TS + Tailwind）

单栏聊天 SPA：`fetch + ReadableStream` 消费后端 SSE（step/token/sources/done/error），`thread_id` 驱动服务端多轮记忆，挂载时拉 history 回填。Header 的「📚 知识库」打开右侧抽屉，可上传自己的文档进知识库；「🗑 删除对话」经二次确认后清掉服务端与本地的当前会话。

首次进入是登录页（可切到注册）—— P2 起 chat 与 kb 端点都要求 Bearer token，token 存 localStorage 且过期会自动登出，见「登录与鉴权」。

## 环境
- Node v22、npm。

## 开发
```bash
npm install
npm run dev          # http://localhost:5173，/api 经 Vite proxy 转发到 :8000
```
后端未就绪时，用 mock SSE 服务联调：
```bash
npm run mock         # 起一个 :8000 的假后端(实现全部 10 个端点：鉴权 / 知识库 / 会话，均为内存态)
                     # 演示账号 hao / abcd1234；chat 与 kb 端点会校验 Bearer 头，缺失返 401
```

## 构建
```bash
npm run build        # tsc --noEmit 类型检查 + vite build → dist/
npm run preview      # 预览构建产物
```
生产由 FastAPI `StaticFiles` 挂载 `dist/`，单容器单端口、同源 `/api`。

## 登录与鉴权

首次进入是登录页（可切到注册）。登录成功后 `access_token` 存进 `localStorage`（键 `ragqa_token`），此后所有请求自动带上 `Authorization: Bearer <token>`。

**三个文件的分工**：

| 文件 | 职责 |
|---|---|
| `src/api/tokenStore.ts` | 只做 token 的读写与清空；键名只在这里出现一次，将来换 httpOnly cookie 时改动面就是它 + `request()` |
| `src/api/client.ts` | 所有 fetch 的唯一出口 `request()`：注入 Authorization + 集中处理 401 |
| `src/hooks/useAuth.ts` | 状态机 `loading / anon / authed`，含启动校验与 login / register / logout |

**为什么必须收敛到 `request()`**：改造前有 6 处 fetch 各自拼 URL 与请求头，漏一处就是一个「带着过期 token 静默失败」的 bug。收敛之后 401 处理只有一份实现。

**401 在本项目有两种含义，必须分流**：

| 场景 | 带 token？ | 处理 |
|---|---|---|
| token 过期 / 被篡改 / 后端换了 secret | 是 | 清 token → 触发回调 → 自动回登录页 |
| 登录时用户名或密码错 | 否 | 只把后端 `message` 显示在登录页错误行，**不清 token、不登出** |

不加这条分流的话，用户输错一次密码就会被当成「会话过期」。实现见 `client.ts` 的 `request()`，用例见 `src/test/client.test.ts`。

**启动时必须 `fetchMe()` 而不是直接信任 localStorage**：token 可能已过期、被改过、或后端换了 secret。不验证就会出现「界面显示已登录、但每个请求都 401」的错位状态。校验期间渲染「正在校验登录状态…」而不是先放行主界面，避免闪一下再被拽回登录页。

**`401` 与 `403` 的区别**：`401` = 没登录或凭证无效（才回登录页）；`403` = 登录了但资源不属于你（只在聊天区提示，**不清空当前消息**）。

**登出**：Header 最右的「🚪 退出」按钮，接 `useAuth.logout()` —— 只清本地 token、不调后端（JWT 无状态，契约里没有 logout 端点）。副作用是 token 在 7 天有效期内仍然有效，这是已知风险（spec §11），不是这里的缺陷。

几个刻意的细节：

- **正在生成时先 `abort()` 再退出**：否则那条流还在跑（后端白跑一次 LLM 调用），而组件已卸载，这半截回答再也回不到界面上。
- **退出按钮不随空会话禁用**（与「🗑 删除对话」不同）：空会话也应该能退出。
- **退出不丢历史**：`thread_id` 存在 localStorage 里、清 token 时不动它，所以重新登录后仍回到同一段会话（服务端的 checkpoint 也还在）。
- 它与「删除对话」之间有一道竖线隔开 —— 两者相邻而后者不可逆，隔一下降低误点概率。
- Header 会显示当前用户名（窄屏隐藏），多账号联调时能一眼看出登的是谁。

契约见 [`../docs/api/README.md`](../docs/api/README.md) 的「鉴权」章节。

## 知识库

Header 的「📚 知识库」按钮打开右侧抽屉：拖拽/点选上传、看实时进度、管理已上传文档。

- **支持格式**：`.md` / `.txt` / `.pdf`（大小写不敏感）；**单个 ≤ 20 MB**；**一次最多选 5 个**。
- **串行上传**：同一时刻只有一个文件在传（嵌入是分批串行的，并发只会互抢额度、进度乱跳）；每个文件一条独立 SSE 流。
- **前端预校验**：不合格的文件**不发请求**，但仍会出现在队列里并标出原因（如“不支持的格式”），可点 ✕ 移除。
- **关抽屉不中断上传**：上传状态住在 `App` 层（`useKnowledgeBase`），关掉抽屉后台继续跑，Header 角标 `↑N` 提示进行中数量；重开抽屉可见进度。
- **同名视为替换**：上传同名文件会覆盖旧版，条目提示“已更新（替换同名文档）”。
- **删除是乐观的**：点 🗑 立即从列表消失，后端失败则自动回滚并提示。
- **预置文档不可删**：列表只展示用户上传项（`origin="upload"`），顶部一行只读摘要告知预置文档规模。

契约见 [`../docs/api/README.md`](../docs/api/README.md) 的「知识库端点」章节；上传的 SSE 样例见 `../docs/api/kb-sse-example.txt`。

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

## 会话列表（侧栏）

左侧栏列出当前用户的会话（按 `updated_at` 倒序，最多 50 条），点标题即切换。

- **折叠**：右侧外沿的 `«` 把手折叠/展开，状态存 `localStorage['ragqa_sidebar_collapsed']`。折叠后侧栏宽度为 0，把手**挂在侧栏外**——放在内部的话折叠后就点不到了。
- **切换会话**：先 `abort()` 在飞的流再换 `thread_id`。**中断是必须的**——SSE 回调直接 dispatch 到「最后一条 assistant 消息」，不中断的话旧会话的 token 会追加到新会话的气泡里。
- **重命名**：hover 出 ✎ → 标题变输入框（预填并全选）→ `Enter` 提交 / `Esc` 取消 / 失焦也提交。**空标题与超 60 字由前端先拦下**（明知会被 422 就别白发请求），失败则回滚原标题并在**那一行**显示后端 message。
- **删除**：hover 出 🗑 → 复用同一个二次确认弹窗。删**当前**会话走 `useChat.deleteChat`（先 abort、等流真正结束再 DELETE，成功后清空消息区并切到列表第一条）；删**其它**会话只动列表，聊天区不受影响。两条路径都 `refresh()` 校准 `total`。
- **新会话何时进侧栏**：后端是在**首次提问时**才 upsert 出 `conversations` 行，所以刷新时机是 `done` 事件之后，而不是点「＋ 新对话」的时候（靠 `useChat({ onThreadTouched })` 传回调，不用全局事件总线）。
- **被截断的提示**：`total` 大于实际条数时显示「仅显示最近 50 条」——只看数组长度是看不出被截断的。
- **`LOAD_HISTORY` 的守卫分两半**（`useChat.ts`）：切换会话时**必须替换**（否则界面一直显示上一个会话）；同一会话的迟到响应仍**不冲掉**用户刚发起的对话。

> ⚠ **本期只交付前端**：`GET /api/chat/threads` 与 `PATCH /api/chat/threads/{id}` 这两个端点的**后端实现尚未落地**（P3 后端还包含「检索按用户过滤」「KB 归属」「旧文档迁移脚本」，另行开工）。所以现在只有 `npm run mock` 能跑通侧栏；直连真后端时侧栏会显示「会话列表加载失败」+ 重试按钮，**聊天本身不受影响**。

## 测试
```bash
npm run test         # Vitest 全量(client 解析与 401 分流 / useAuth 状态机 / 登录页 / 知识库队列 / 确认弹窗 / 组件 / App 集成)
npm run test:watch
```

## 契约
接口契约见 [`../docs/api/README.md`](../docs/api/README.md) 与 `../docs/api/openapi.yaml`。
**改字段/事件先改契约，再改 `src/api/types.ts` 与 `client.ts`**，保持三者一致。
