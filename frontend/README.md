# 前端 · LangChain 智能问答（React + Vite + TS + Tailwind）

单栏聊天 SPA：`fetch + ReadableStream` 消费后端 SSE（step/token/sources/done/error），`thread_id` 驱动服务端多轮记忆，挂载时拉 history 回填。Header 的「📚 知识库」打开右侧抽屉，可上传自己的文档进知识库。

## 环境
- Node v22、npm。

## 开发
```bash
npm install
npm run dev          # http://localhost:5173，/api 经 Vite proxy 转发到 :8000
```
后端未就绪时，用 mock SSE 服务联调：
```bash
npm run mock         # 起一个 :8000 的假后端(实现全部 6 个端点，含知识库内存态增删查)
```

## 构建
```bash
npm run build        # tsc --noEmit 类型检查 + vite build → dist/
npm run preview      # 预览构建产物
```
生产由 FastAPI `StaticFiles` 挂载 `dist/`，单容器单端口、同源 `/api`。

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

## 测试
```bash
npm run test         # Vitest 全量(client 解析 / hooks 状态机 / 知识库队列 / 组件 / App 集成)
npm run test:watch
```

## 契约
接口契约见 [`../docs/api/README.md`](../docs/api/README.md) 与 `../docs/api/openapi.yaml`。
**改字段/事件先改契约，再改 `src/api/types.ts` 与 `client.ts`**，保持三者一致。
