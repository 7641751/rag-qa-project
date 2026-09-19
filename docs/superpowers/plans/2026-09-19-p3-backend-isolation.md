# P3 后端：知识库用户隔离 + 会话列表 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让检索与知识库按用户隔离（预置文档全局共享、上传件仅本人可见），并新增会话列表/重命名两个端点。

**Architecture:** 单 Chroma collection + metadata `user_id`，检索时用 `filter` 走 Chroma 的 `where`。`retrieve` 节点从注入的 **vectorstore**（不再是 retriever 单例）直接 `similarity_search`，过滤条件由 `kb_filter(user_id)` 生成。KB 链路的四个查询函数都加 `user_id` 参数，命中 0 条天然得 404（不泄露存在性）。

**Tech Stack:** FastAPI / LangGraph / Chroma / SQLAlchemy(async) / pytest / aiosqlite

**设计依据：** `docs/superpowers/specs/2026-09-13-p3-conversation-list-kb-isolation-design.md`（下称 spec）。本计划只做它标注为**后端**的部分（§5 / §6 / §7.1 / §9 / §7.5 的契约文件）；§10 前端已于 `4e00fcc` 完成。

---

## 执行状态（2026-09-19 **已完成**）

| Task | 提交 | 结果 |
|---|---|---|
| 0 收尾图改造 | `d589291` | ✅ 修循环导入（起始有 5 个 collection error）+ 删 `get_retriever` + `FakeVectorStore` |
| 1 filter 三条防线 | `c13f5b4` | ✅ 含变异检查（喂入 `origin=builtin` 立即红） |
| 2 user_id 进 state | `57e6e71` | ✅ |
| 3+4+5 KB 归属隔离 | `6fe765b` | ✅ 四查询函数 + meta + 三端点；★ 翻转了 `test_kb_requires_login` 的越权断言（200 → 404） |
| 6+7 会话列表/重命名 | `b42c03a` | ✅ 含 onupdate 抑制（变异检查已验证） |
| 8 端点级隔离测试 | `6090779` | ✅ §12.1 #8/#9 |
| 9 迁移脚本 | `5e449f8` | ✅ 含跑 CLI 才发现的两个 bug |
| 10 契约与文档 | `bf80117` | ✅ |
| 11 手工验收 | — | ✅ **14/14**（真实 MySQL + 真实 Chroma） |

**测试**：后端 起始 `5 errors during collection` → **172 passed, 1 skipped**；前端 **148 passed / 14 files**。

### 执行中的关键实测发现

1. **循环导入（Task 0，起始阻断）**：`graph.py` 顶层 `import BUILTIN_KB` 与 `upload_function_tools:29` 的反向 import 成环 → 5 个测试文件连收集都失败。改为 `kb_filter` 内延迟 import。
2. **`build_graph` 的 `get_retriever()`（Task 0）**：`VectorStoreRetriever` **没有** `similarity_search`（实测），而 `chat_service.py:20,88` 都是裸 `get_graph()` → 线上问任何问题都会 `AttributeError`。
3. **`onupdate` 会刷新 `updated_at`（Task 6）**：朴素 `row.title = x` 会让「重命名」把会话顶到列表最前，破坏排序语义。用 Core update 显式写回 `updated_at` 抑制（变异检查已验证测试有效）。
4. **ISO 8601 缺 `Z`（Task 7）**：库里是 naive UTC，Pydantic 默认输出不带 `Z`，而 JS `new Date()` 会把无 `Z` 形式按**本地时区**解析 → 相对时间整体偏一个时区。加 `field_serializer` 补 `Z`。
5. **迁移脚本的两个 CLI bug（Task 9，测试覆盖不到）**：① 中文 Windows 控制台 GBK 下打印 `✔`/`✘` 抛 `UnicodeEncodeError`（连「拒绝执行」提示都打不出来）；② `asyncio.run` 返回后 aiomysql 连接在 `__del__` 里 close 抛 `Event loop is closed`，使脚本「成功却带着 traceback 结束」。
6. **数据不一致（Task 9 实测）**：向量库有 **21** 个上传文档，而 `data/uploads/` 只有 **20** 个原件 —— 多出的 `5230cdcc-…__可重入锁.md`（7 段）**只有向量、没有原件**。spec 的「20 个」是从磁盘数的。迁移会一并补它的 `user_id`（无害），但它没有原件，`ingest.py --force` 重建后会**永久丢失**。

### 验收证据（Task 11，真实 MySQL + 真实 Chroma）

```
PASS  新用户会话列表 = 200 {threads:[],total:0}
PASS  新用户 KB 列表为空，但 builtin 全局可见(88 篇)
PASS  PATCH 不存在的会话 -> 404 NOT_FOUND
PASS  PATCH 标题空/纯空格/超60 -> 422 契约错误体
PASS  无 token -> 401 UNAUTHORIZED
    [retrieve] 命中 4 段: ['Checkpointers', 'Checkpointers', 'Checkpointers', 'Memory']
    [grade] 4/4 段判定为相关
PASS  ** 提问 grounded=True（88 篇预置仍可检索）**
PASS  提问后会话进列表：title=首问前20字、时间戳带 Z
PASS  PATCH 本人会话 -> 200 且标题已 trim
PASS  重命名不改 updated_at（排序语义）
PASS  ** B 的会话列表为空（看不到 A 的）**
PASS  ** B 重命名 A 的会话 -> 403 FORBIDDEN**
PASS  ** B 的 KB 列表为空，builtin 仍 88 篇**
PASS  DELETE 本人会话 -> 200 deleted=true
14/14 passed
```

> 验收脚本在真实库里建了两个用户（`accA*` / `accB*`，id 6/7）用于验证隔离，需要清理可直接删 `users` 表对应行。

### 迁移已执行（2026-09-19 追加）

按 spec §13 的顺序跑完，并额外清掉一处数据不一致：

| 步骤 | 结果 |
|---|---|
| `--dry-run`（迁移前） | 待迁移 **820 段 / 21 个文档** → 用户 `61014(id=1)` |
| `--yes` | 已写入 820 段 / 21 个文档 |
| `--dry-run`（幂等验证） | **0 段 / 0 个文档** ✓ |
| 删除孤儿 | `delete_upload_doc('5230cdcc-…', 1)` → **7 段**（走应用自己的删除路径） |
| 迁移后总量 | **813 段 / 20 个文档**，与 `data/uploads/` 的 20 个原件一致 ✓ |
| 预置文档 | 仍 **2885 段**，未受影响 ✓ |

**孤儿取证（删前）**：`5230cdcc-…__可重入锁.md` 的 7 段 chunk 编号是 **30–36**，不是从 0 开始 —— 说明该文档原本 ≥37 段、**前 30 段早已丢失**，是某次未完成清理留下的碎片，且落盘原件已不在。备份留在 `data/orphan-backup-5230cdcc.json`（ids + metadatas + **原文**，9373 字节），需要时能把那几节内容取回来。

**顺序为何必须「先迁移、再删除」**：孤儿没有 `user_id`，而 P3 的删除过滤条件是 `origin + user_id` —— 迁移之前它**根本删不掉**（命中 0 条）。这是「所有上传件的删/查都带 `user_id`」这条设计的一个直接推论。

**迁移目的已验证**（spec §13 第 8 步，用 `kb_filter` 做三视角对比）：

```
A) 上传者 user 1    命中 10 段，其中上传件 10 段 → ['布隆过滤器.md', '秒杀问题概述.md', '第2节. RAG Agent.md']
B) 另一用户 user 2  命中 10 段，其中上传件  0 段 → []
C) 无登录态 (CLI)   命中 10 段，其中上传件  0 段 → []
   → 用户 2 与无登录态的结果集合完全相同（都只有预置文档）
```



---

## Task 0：收尾图改造，把基线修绿

`graph.py` / `schemas.py` 在上次提交 `339f3da` 之后被改到一半（未提交）。spec §5.4 的影响面清单列了 8 处，目前差这些。

**Files:**
- Modify: `rag_qa_project/backend/app/agent/graph.py`
- Modify: `rag_qa_project/tests/test_graph.py:95-113,130,140,151,162,174`
- Modify: `rag_qa_project/tests/test_chat_stream.py:36,57-58`

**Step 0.1：先把 RED 证据钉住**

Run: `python -m pytest tests/test_graph.py -q --no-header`
Expected: `5 errors`，全部是 `TypeError: build_graph() got an unexpected keyword argument 'retriever'`

**Step 0.2：修 `graph.py:237` 的线上阻断**

```python
# 改前
vectorstore = vectorstore or get_retriever()
# 改后
vectorstore = vectorstore or get_vectorstore()
```

**Step 0.3：删 `get_retriever()`（`:34-35`）**

spec §5.3 明确要求删除：它唯一的用途就是喂给 `build_graph`，改造后无调用方。**先 grep 确认无其他调用方**：

Run: `rg "get_retriever" -g '*.py'`
Expected: 改完只剩 0 处（`graph.py` 内）

**Step 0.4：`kb_filter` 前置 + `BUILTIN_KB` 导入上移**

现状 `graph.py:269` 是文件中部的 `from backend.tools.upload_function_tools import BUILTIN_KB`，`:272` 的 `kb_filter` 在 `get_graph()` 之后。把两者移到文件顶部 import 区（`get_vectorstore` 之后），并把 `kb_filter` 的缩进从 3 空格改成 4 空格。

⚠ **不能真的在模块顶层 import `upload_function_tools`** —— 它 `:29` 反过来 import `graph.get_vectorstore`，会形成循环。实测可行是因为 Python 在 `get_vectorstore` 定义之后才执行那行 import。所以：**保留函数内延迟 import**，只把它移到 `kb_filter` 内部：

```python
def kb_filter(user_id: int | None) -> dict | None:
    """检索范围：预置官方文档全局共享 + 本人上传件。

    ⚠ Chroma 的 $and / $or **至少要两个子条件**，单条件包一层会抛 ValueError
      （upload_function_tools.py:17-18、项目 README:438）。所以无 user_id 时直接返回
      裸条件，不能写 {"$or": [{"kb": BUILTIN_KB}]}。
    ⚠ BUILTIN_KB 必须函数内 import：upload_function_tools 反过来 import 本模块的
      get_vectorstore，模块顶层 import 会成环。
    """
    from backend.tools.upload_function_tools import BUILTIN_KB

    if user_id is None:  # CLI run.py 等无登录态的调用方：只检索预置文档
        return {"kb": BUILTIN_KB}
    return {"$or": [{"kb": BUILTIN_KB}, {"user_id": user_id}]}
```

**Step 0.5：docstring 三处去 retriever 化**

- `:11` 模块 docstring：`build_graph(model=None, retriever=None, checkpointer=None)` → `vectorstore=None`，「内存检索器」→「内存向量库」
- `:125` `_make_nodes` docstring：「把 model / retriever 注入节点函数」→「model / vectorstore」
- `:233` `build_graph` docstring：「model/retriever/checkpointer 可注入」→「model/vectorstore/checkpointer」

**Step 0.6：测试替身升级（spec §5.5，本期最大的测试收益）**

`FakeRetriever` 只有 `invoke(query)`，**看不见 filter** —— 这正是隔离逻辑此前完全无法测试的原因。在 `tests/test_graph.py` 里把 `FakeRetriever` 替换为：

```python
class FakeVectorStore:
    """记录每次调用收到的 filter，让「按用户隔离」这件事第一次变得可测。"""

    def __init__(self, docs=None):
        self.docs = list(docs) if docs is not None else [
            Document(page_content="LangGraph 用 checkpointer 在每个 super-step 后持久化状态，"
                                  "从而支持断点续跑与时间旅行。",
                     metadata={"title": "Persistence", "source": "langgraph/persistence.md"}),
            Document(page_content="LangChain 消息通过 add_messages reducer 累加，"
                                  "并按消息 id 自动去重/覆盖。",
                     metadata={"title": "Messages", "source": "langchain/messages.md"}),
        ]
        self.calls: list[dict] = []

    def similarity_search(self, query: str, k: int = 4, filter=None, **kwargs):
        self.calls.append({"query": query, "k": k, "filter": filter})
        return list(self.docs)[:k]
```

保留 `FakeRetriever` **不删**（`test_chat_stream.py` 也 import 它），但把它标记为已弃用，或同步替换。倾向：直接改名为 `FakeVectorStore` 并同步改两处 import。

**Step 0.7：6 处调用点换参数名**

`test_graph.py:191,202,211,224,236`（5 处 `_make_nodes(_model(), FakeRetriever())`）与 `:130,140,151,162,174`（`build_graph(..., retriever=...)`），以及 `test_chat_stream.py:57-58`。

Run: `rg "FakeRetriever|retriever=" tests/ -n` 逐一改净。

**Step 0.8：验证全绿**

Run: `python -m pytest tests -q --no-header`
Expected: 全 passed，0 error（数量以实测为准，P2 末为 102 passed）

**Step 0.9：Commit**

```bash
git add rag_qa_project/backend/app/agent/graph.py rag_qa_project/tests/test_graph.py rag_qa_project/tests/test_chat_stream.py
git commit -m "fix(p3): 收尾 retrieve 节点的 vectorstore 改造（修 build_graph 的 get_retriever 阻断）" -- <paths>
```

> 用 pathspec 形式，理由见 P2 计划（索引里可能预置了暂存项）。

---

## Task 1：filter 的三条防线（spec §12.1 #1/#2/#3）

**Files:** Modify `rag_qa_project/tests/test_graph.py`（新增用例）

**Step 1.1：写失败测试**

```python
# ============================ P3: 检索按用户过滤 ============================
def test_retrieve_filter_with_user_id():
    """有 user_id → 预置文档 + 本人上传件。"""
    vs = FakeVectorStore()
    retrieve, _, _, _ = _make_nodes(_model(), vs)

    retrieve({"question": "q", "documents": [], "generation": "", "rewrites": 0, "user_id": 7})

    assert vs.calls[0]["filter"] == {"$or": [{"kb": "langchain_docs"}, {"user_id": 7}]}


def test_retrieve_filter_without_user_id():
    """无 user_id（CLI / 未登录）→ 只见预置文档。

    ⚠ 断言的是**裸条件**而不是 {"$or": [{"kb": ...}]}：Chroma 的 $or 至少要两个
    子条件，单条件包一层会抛 ValueError（spec §5.2）。
    """
    vs = FakeVectorStore()
    retrieve, _, _, _ = _make_nodes(_model(), vs)

    retrieve({"question": "q", "documents": [], "generation": "", "rewrites": 0})

    assert vs.calls[0]["filter"] == {"kb": "langchain_docs"}
    assert "$or" not in vs.calls[0]["filter"]


def test_kb_filter_never_uses_origin_builtin():
    """钉住 spec §5.1 的纠错：预置文档**没有** origin 字段。

    初稿曾写 {"$or":[{"origin":"builtin"},...]}，若那样实现，88 篇官方文档会全部
    从检索结果消失且不报错。这条测试就是防止它被改回去。
    """
    from backend.app.agent.graph import kb_filter
    import json

    for uid in (None, 7):
        assert "origin" not in json.dumps(kb_filter(uid))
```

**Step 1.2：跑测试确认通过**（实现已在 Task 0 就位，这三条是**回归防线**，RED 阶段应为 PASS；若 FAIL 说明 Task 0 没改对）

Run: `python -m pytest tests/test_graph.py -q -k "filter"`

**Step 1.3：Commit**

---

## Task 2：chat 链路把 user_id 注入 state

**Files:**
- Modify: `rag_qa_project/backend/app/services/chat_service.py:19-23,139-153`
- Test: `rag_qa_project/tests/test_chat_stream.py`

**Step 2.1：写失败测试**

在 `test_chat_stream.py` 加：断言检索收到的 filter 带上了调用方的 user_id（用 `FakeVectorStore` 直接测 `build_graph(vectorstore=vs)` + `invoke(..., user_id=7)`）。

**Step 2.2：改 `stream_chat` 签名并写入 state**

```python
async def stream_chat(chat_request: ChatRequest, user_id: int | None = None):
    graph = get_graph()
    # user_id 必须进 state：retrieve 节点靠它生成本次检索的 filter。
    # 显式传 None 也写进去（而不是省略键）—— RAGState 是 TypedDict，缺键时
    # state.get("user_id") 返回 None 一样安全，但写进去让「谁注入了什么」在日志里可见。
    state = {"question": chat_request.question, "documents": [],
             "generation": "", "rewrites": 0, "user_id": user_id}
```

**Step 2.3：`prepare_stream` 透传**

`chat_service.py:153`：`return stream_chat(chat_request)` → `return stream_chat(chat_request, user_id)`

**Step 2.4：跑全套**（`test_chat_stream.py` 里若有直接调 `stream_chat` 的地方要同步）

Run: `python -m pytest tests -q --no-header`

**Step 2.5：Commit**

---

## Task 3：KB 四个查询函数加 user_id（spec §6）

**Files:**
- Modify: `rag_qa_project/backend/tools/upload_function_tools.py:116-193`
- Modify: `rag_qa_project/tests/test_kb_upload.py`（调用点）
- Test: 新建 `rag_qa_project/tests/test_kb_isolation.py`

**Step 3.1：写失败测试（隔离用例）**

```python
def test_same_filename_different_user_does_not_delete_others():
    """spec §1 问题 2 的回归测试（数据丢失级）。

    A、B 各传 笔记.md；A 再传一次同名 → **B 的文档与落盘原件都必须还在**。
    """
    # 断言：find_upload_doc_ids("笔记.md", user_id=A) 只返回 A 的 doc_id
```

其余用例见 Task 8。

**Step 3.2：四个函数签名与 where 条件**

| 函数 | 新签名 | 新 where |
|---|---|---|
| `find_upload_doc_ids` | `(filename, user_id)` | `{"$and":[{"filename":X},{"origin":"upload"},{"user_id":uid}]}` |
| `get_upload_doc` | `(doc_id, user_id)` | `{"$and":[{"doc_id":X},{"origin":"upload"},{"user_id":uid}]}` |
| `list_upload_docs` | `(user_id)` | `{"$and":[{"origin":"upload"},{"user_id":uid}]}` |
| `delete_upload_doc` | `(doc_id, user_id)` | `{"$and":[{"doc_id":X},{"origin":"upload"},{"user_id":uid}]}` |

**不改**：`builtin_stats()`（全局统计）、`remove_upload_copies()`（doc_id 是 uuid，天然唯一）、`_safe_disk_name` / `save_upload_copy`。

⚠ 四个函数的 `where` 都保持 **≥2 个子条件**（`$and` 的硬要求）。`list_upload_docs` 从单条件 `{"origin":"upload"}` 变成 `$and` 两条件是**必要的**，不是风格问题。

**Step 3.3：同步更新 `upload_function_tools.py:11-18` 的模块 docstring 三条硬约定**

第 2、3 条要补上 `user_id`：metadata 必填项多了 `user_id`；「凡是删/查用户上传的过滤条件都带 `origin="upload"`」要改成「都带 `origin="upload"` **与 `user_id`**」。

顺带按 spec §9.2 补一句：`function_tools.py:11` 那句「补 metadata 等于整块重新嵌入」**只对 LangChain 的 `add_documents` 路径成立**，raw `_collection.update()` 只传 ids+metadatas 时零嵌入（`CollectionCommon.py:388-399`）。

**Step 3.4：跑全套，修所有调用方报错**

Run: `python -m pytest tests -q --no-header`（预期大量 `TypeError: missing 1 required positional argument`，逐个修）

**Step 3.5：Commit**

---

## Task 4：上传写入 user_id，同名替换只在本人范围

**Files:**
- Modify: `rag_qa_project/backend/app/services/upload_service.py:43-44,79-87,125-155`
- Test: `rag_qa_project/tests/test_kb_upload.py`

**Step 4.1：`stream_upload` 与 `sse_upload` 加 `user_id` 参数**

```python
async def stream_upload(filename: str, raw: bytes, doc_id: str,
                        uploaded_at: str, replaced: bool, user_id: int):
```
```python
async def sse_upload(file: UploadFile = File(...), user_id: int = 0):
```

⚠ `sse_upload` 的 `user_id` 有默认值 `0` 只为兼容既有测试的调用方式；**路由必须显式传入** `user.id`（Task 5）。若不加默认值，FastAPI 会把它当成 query 参数。

**Step 4.2：`meta` 加 `"user_id"`**

```python
        meta = {
            "doc_id": doc_id,
            "filename": filename,
            "origin": "upload",
            "user_id": user_id,          # P3: 检索过滤与归属判断的唯一依据
            "uploaded_at": uploaded_at,
            ...
        }
```

**Step 4.3：`_rollback` 与同名替换传 user_id**

- `_rollback(doc_id)` → `_rollback(doc_id, user_id)`，内部 `delete_upload_doc(doc_id, user_id)`
- `sse_upload` 里 `find_upload_doc_ids(filename)` → `find_upload_doc_ids(filename, user_id)`（**这一处就是「A 删 B 同名文档」的修复点**）
- `for old_id in old_ids:` 里的 `delete_upload_doc(old_id)` → 加 `user_id`

**Step 4.4：测试**

```python
def test_upload_writes_user_id_metadata():
    """上传后每段 metadata 都含 user_id。"""
```

Run: `python -m pytest tests/test_kb_upload.py -q --no-header`

**Step 4.5：Commit**

---

## Task 5：kb_router 三端点传 current_user.id

**Files:**
- Modify: `rag_qa_project/backend/app/api/kb_router.py:28-67`

**Step 5.1：三处改动**

- `upload_documents(_user, file)` → `upload_documents(user: CurrentUser, file)`，调 `upload_service.sse_upload(file, user.id)`
- `get_documents(_user)` → `get_documents(user: CurrentUser)`，`list_upload_docs(user.id)`
- `delete_document(doc_id, _user, db_session)` → `user: CurrentUser`，`get_upload_doc(doc_id, user.id)` / `delete_upload_doc(doc_id, user.id)`

**Step 5.2：删掉 `:18` 未使用的 `from backend.app.services import conversation_service` 与 `DbSession`/`db_session`**

⚠ 先确认 `delete_document` 真的不需要 db —— 本期 KB 不查 MySQL（归属靠 Chroma metadata），所以 `DbSession` 与 `db_session` 是死参数。**改签名会动 FastAPI 的依赖注入，删之前跑一次 `tests/test_kb_requires_login.py` 确认无依赖。**

**Step 5.3：更新 `:25-27` 那段「本期不校验归属」的注释**（P3 已实现归属，注释必须改，否则误导）

**Step 5.4：跑 `test_kb_requires_login.py`**

⚠ 这个文件有 `test_kb_endpoints_require_login_but_not_ownership`，它**刻意钉住了「B 能删 A 的文档 → 200」这个已知缺口**。P3 修好后这条**必须变红** —— 按 spec §12.1 #10，它应改成断言 **404**。这是本计划**唯一一处故意翻转既有断言**的地方，翻转时要在注释里写明原因。

**Step 5.5：跑全套 + Commit**

---

## Task 6：会话列表/重命名的服务层

**Files:**
- Modify: `rag_qa_project/backend/app/services/conversation_service.py`
- Test: 新建 `rag_qa_project/tests/test_conversation_threads.py`

**Step 6.1：写失败测试（spec §12.1 #5/#6/#7 的服务层部分）**

**Step 6.2：实现 `list_conversations`**

```python
LIST_LIMIT = 50
RENAME_MAX_CHARS = 60


async def list_conversations(db: AsyncSession, user_id: int) -> tuple[list[Conversation], int]:
    """当前用户的会话，按 updated_at 倒序，最多 LIST_LIMIT 条。

    返回 (rows, total)：total 是**真实总数**，前端据此显示「仅显示最近 50 条」——
    只回 50 条而不给 total，前端无从判断是被截断还是真的只有这么多。
    走 ix_conversations_user_updated (user_id, updated_at DESC)，与 ORDER BY 完全对齐。
    """
    total = await db.scalar(
        select(func.count()).select_from(Conversation).where(Conversation.user_id == user_id))
    rows = (await db.scalars(
        select(Conversation).where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc()).limit(LIST_LIMIT))).all()
    return list(rows), int(total or 0)
```

**Step 6.3：实现 `rename_conversation`**

```python
async def rename_conversation(db: AsyncSession, thread_id: str,
                              user_id: int, title: str) -> Conversation:
    """重命名。非本人 → 403，不存在 → 404（spec §11 错误矩阵）。

    ⚠ 顺序不能反：「先查存在、再判归属」会让「别人的会话」返回 404 而不是 403，
    与契约不符；「先判归属」则必须拿到行，所以合并成一次 db.get + 两次分支。
    ⚠ 只改 title，**不动 updated_at**：重命名不是新活动，不该把会话顶到列表最前。
    """
    row = await db.get(Conversation, thread_id)
    if row is None:
        api_error(404, "NOT_FOUND", f"会话不存在: {thread_id}")
    if row.user_id != user_id:
        api_error(403, "FORBIDDEN", "该会话不属于当前用户")
    row.title = title
    await db.flush()
    await db.commit()
    return row
```

**Step 6.4：跑测试 + Commit**

---

## Task 7：两个新端点

**Files:**
- Modify: `rag_qa_project/backend/app/agent/schemas.py`（+3 个模型）
- Modify: `rag_qa_project/backend/app/services/chat_service.py`（+2 个函数）
- Modify: `rag_qa_project/backend/app/api/chat_router.py`（+2 个端点）
- Test: `rag_qa_project/tests/test_chat_threads.py`

**Step 7.1：schemas 加契约模型**

```python
class ConversationSummary(BaseModel):
    """会话列表项（契约 components.schemas.ConversationSummary）。"""
    thread_id: str
    title: str
    created_at: datetime
    updated_at: datetime


class ThreadListResponse(BaseModel):
    threads: list[ConversationSummary]
    total: int = Field(description="真实总数；threads 最多 50 条，total 更大即被截断")


class RenameRequest(BaseModel):
    """PATCH /api/chat/threads/{thread_id} 请求体。"""
    title: str = Field(description="新标题", min_length=1, max_length=RENAME_MAX_CHARS)


class RenameResponse(BaseModel):
    thread_id: str
    title: str
```

⚠ `min_length=1` 只拦得住空串，拦不住 `"   "`（纯空格）。契约要求「标题空 → 422」，所以要加 `@field_validator` 做 strip 后判空，**并且把 strip 后的值写回**（否则库里存的是带空白的标题）。

⚠ `created_at`/`updated_at` 用 `datetime`：SQLAlchemy 的 `DateTime` 取出的是 naive datetime（UTC），Pydantic 会序列化成 `2026-09-19T07:26:28`（无 `Z`）。而**契约与前端 mock 都是带 `Z` 的 ISO 8601**。需要在模型上加 serializer 补 `Z`，否则前端 `Date.parse` 在某些引擎上会按本地时区解析，相对时间显示错乱。

**Step 7.2：chat_service 两个薄封装**

**Step 7.3：chat_router 两个端点**

```python
@router.get("/threads", response_model=ThreadListResponse)
async def list_chat_threads(user: CurrentUser, db: DbSession): ...


@router.patch("/threads/{thread_id}", response_model=RenameResponse)
async def rename_chat_thread(thread_id: str, body: RenameRequest,
                             user: CurrentUser, db: DbSession): ...
```

⚠ 路由顺序：`/threads` 必须在 `/threads/{thread_id}` 之前？其实不冲突（一个无尾段、一个有），但 `DELETE /threads/{thread_id}` 已存在，PATCH 加在同一前缀下没问题。

**Step 7.4：测试（spec §12.1 #4/#5/#6/#7）**

- `test_list_threads_only_own`
- `test_list_threads_ordered_by_updated_at_desc`
- `test_list_threads_limit_50_and_total`（造 55 条）
- `test_rename_own_thread` / `test_rename_other_users_thread_403` / `test_rename_missing_404`

⚠ 契约 §7.1 写的是**重命名非本人 → 403**；而 Task 6 的实现也是 403。两条要一致，别一个 403 一个 404。

**Step 7.5：Commit**

---

## Task 8：KB 端点级隔离测试（spec §12.1 #8/#9/#10/#11）

**Files:** 新建/扩充 `rag_qa_project/tests/test_kb_isolation.py`

四个用例：`test_upload_writes_user_id_metadata`、`test_list_docs_only_own` + `test_delete_other_users_doc_404`、`test_builtin_stats_is_global`、`test_same_filename_different_user_does_not_delete_others`。

**Step 8.5：Commit**

---

## Task 9：旧数据迁移脚本（spec §9）

**Files:**
- Create: `rag_qa_project/scripts/migrate_kb_user_id.py`
- Test: 新建 `rag_qa_project/tests/test_migrate_kb.py`

**六条硬约束（spec §9.2，逐条都要落到代码里）：**

1. 走 raw collection：`get_vectorstore()._collection.update(ids=..., metadatas=...)`，**不用** `add_documents`（那个会重嵌入，2885 段全量跑一次是真实额度）
2. `update` 是**整体替换** metadata → 必须先读旧值再 `{**old, "user_id": uid}`，否则 `doc_id`/`filename`/`origin` 全丢
3. 分批 500
4. 幂等：跳过已有 `user_id` 的段
5. 归属对象：`SELECT id, username FROM users ORDER BY id LIMIT 1`，启动时打印并要求 `--yes`（或 `--user <username>`）
6. `--dry-run` 只打印「将影响 N 段向量 / M 个文档 / 归属给 X」

**Step 9.x：测试** `test_migration_script_is_idempotent`（临时 Chroma 目录，跑两次，第二次 0 段变更，且 metadata 未丢字段）

---

## Task 10：契约与文档（spec §7.5 / §14）

**Files:**
- Modify: `docs/api/openapi.yaml`：`/api/chat/threads`（get）+ `/api/chat/threads/{thread_id}`（patch）；`components.schemas` 加 4 个模型
- Modify: `docs/api/README.md`：端点表 +2 行；metadata 表加 `user_id` 行；新增「会话列表」「多用户与数据隔离」两章
- Modify: `rag_qa_project/README.md`：CLI `run.py` 只检索预置文档（`user_id=None`），这是刻意行为
- Modify: `_build_advanced.py:1093`：notebook 文案里的 `build_graph(model=None, retriever=None)` → `vectorstore=None`

YAML 改完必须用 `python -c "import yaml; yaml.safe_load(open(...))"` 验语法。

---

## Task 11：手工验收（spec §13）

**上线顺序（顺序错了会丢数据可见性）：**

1. 确认至少注册了一个用户
2. `python scripts/migrate_kb_user_id.py --dry-run` → 核对 **20 个文档**
3. `python scripts/migrate_kb_user_id.py --yes`
4. 再 `--dry-run` → 应为 0 段待迁移
5. 部署后端（本次就是它）
6. 跑 12 个后端用例
7. 前端已交付，跑 vitest + build
8. **手工验收的关键一条**：用户 B 的 KB 列表为空、检索不到 A 的文档，但**能检索到 88 篇官方文档**（验证 §5.1 的纠错没走偏）

> 第 3 步与第 5 步不能颠倒：先部署后迁移，会有一段时间所有上传件对所有人不可见。

---

## 依赖图

```
Task 0（修基线，阻断） → Task 1（filter 防线）
Task 0 → Task 2（user_id 进 state）
Task 2 → Task 3（KB 四函数） → Task 4（上传写 user_id） → Task 5（路由传 uid）
Task 3/5 → Task 8（KB 隔离测试）
Task 6 → Task 7（两个端点）
Task 9（迁移，独立，但上线时必须先于部署）
Task 10（契约文档，可与 6-9 并行）
Task 11（验收，依赖全部）
```

**Task 6 与 Task 3 可并行**（一个查 MySQL、一个查 Chroma，无交集）。

---

## 范围外（不在本计划）

- 前端 §10：已于 `4e00fcc` 完成
- 会话搜索 / 真分页 / 归档置顶 / 分享导出 / 多人协作（spec §2 非目标）
- 每用户独立 collection（spec 决策 3 否决）
- 预置文档 metadata 统一（spec §14 技术债，需 `ingest.py --force` 全量重嵌）
