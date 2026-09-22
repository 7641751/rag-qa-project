# 简历化改进实施计划（实习 · 大模型应用开发 · 偏 Java 后端）

> **执行方式：** 按 P0 → P1 → P2 顺序推进，每项都有「改动点 + 验收命令 + 预期输出」。
> 验收标准全部是**可复现的命令**，不是主观描述 —— 简历上的每个数字都必须能用一条命令跑出来。

**Goal:** 把 rag_qa_project 从「一个技术含量很高但包装滞后两个版本的课程作业」，改造成「一个能扛住面试官逐行追问的实习简历项目」。

**前提判断（读之前先看这条）：** 目标岗位偏 Java 后端，但本项目是 Python 全栈。
**不建议用 Java 重写**（理由见 §1 决策点 D1）。本项目的定位是「证明 AI 应用落地能力 + 工程素养」，
Java 能力应由**另一个项目**承载，而不是把已有成果推倒重来。

**Tech Stack:** LangGraph / FastAPI / React / Chroma / MySQL / Redis / Docker Compose / GitHub Actions

---

## §决策与进度（2026-09-22 更新）

### 已拍板的决策

| 决策 | 结论 | 影响 |
|---|---|---|
| **D1** Java 重写 | ❌ **不重写**。本项目定位为「AI 应用落地 + 工程素养」证明，Java 能力由**另一个项目**承担 | 解放约 60h，全部投入包装与深度。**但要补一个 Java 项目**，否则「你的 Java 项目在哪」答不上来 |
| **D2** 独立仓库 | ✅ **要抽** | 见下方「D2 前置条件清单」——**有两处代码必须先改，否则抽出来启动即失败** |
| **D3** 部署 | 🔄 **进行中**（用户已在服务器上跑 docker 构建） | 部署完成后补：4 张截图 + 公网 TTFT 实测 |

### 已完成

| Task | 状态 | 验证方式 |
|---|---|---|
| P0-2 前端测试锁 `NODE_ENV` | ✅ | 在 `NODE_ENV=production` 的 shell 下 `npm run test` → **148 passed / 14 files** |
| P1-1 兜底分支 token 帧 | ✅ **无需改动** | 见下方「重要发现 ①」——早已修复，只是 README 没跟上 |
| P0-1 README 对齐 | ✅ | 技术栈表 / 端点数 / 测试数 / 项目结构 / HTTP 接口表 / FAQ / 已知问题 全部重写；`grep` 扫描无残留过期标记 |
| P0-3 CI | ✅ | 新增 `.github/workflows/ci.yml`（放在项目内 → monorepo 里不触发，抽独立仓库后自动生效） |
| D2 前置：`.gitignore` | ✅ | 新建 `rag_qa_project/.gitignore`（独立仓库**必须**自带，否则 `.env` 密钥有误提交风险）；`git ls-files -i -c --exclude-standard` 确认零误伤 |
| D2 前置：`config.py` 的 `.env` 定位 | ✅ | 抽出 `_find_env_file()`：由外向内四级回退 + `RAGQA_ENV_FILE` 逃生舱；全量测试无回归 |
| P2-1 清理死代码 | ✅ | `_big_sample.md` → `tests/fixtures/`；`_sse_upload_demo.py` → `docs/examples/sse_upload_demo.py`；删 `_verify_prefix.py` / `data/knowledge.json` / 误入的 `mermaid1789*.mermaid` |

**当前实测基线**（2026-09-22）：

```
后端：257 passed, 1 skipped, 4 deselected      （20s，全离线）
前端：148 passed / 14 files                     （4s，且已能在 NODE_ENV=production 下跑通）
tsc --noEmit：通过
```

### 待办

| Task | 阻塞于 |
|---|---|
| **D2 前置：`ingest.py` 的 `.env` 定位** | ⚠️ 需用户决策，见下方「重要发现 ②」 |
| P0-4 抽独立仓库 | 上面这条改完 |
| P0-5 部署验收（截图 + TTFT） | 用户服务器构建完成 |
| P1-2 评测集补强 + e2e 指标 | 无（需 API 额度） |
| P1-3 检索优化 + 回归验证 | P1-2 |
| P2-2 依赖统一 / P2-3 lint / P2-4 可观测 / P2-5 LICENSE | 无 |

---

### 重要发现（执行过程中新增，原计划未覆盖）

#### ① README「已知问题 §1」是**假的过期条目**

原计划把「兜底分支不产生 `token` 帧」列为待修 bug。**实际早已修复**，而且是双保险：

1. `graph.py` 的 `_answer_without_kb` 真调 model 用自有知识作答，并自声明未经验证；
2. `chat_service.py` 再兜一层 —— 全程没推过 token 却拿到了 `generation`，就在 `done` 前补发
   `step(generate)` + 一帧 `token`；
3. `tests/test_chat_stream.py::test_fallback_path_emits_token_frame_and_marks_ungrounded`
   作为「★ 核心回归线」把它钉死。

**教训**：README 与代码脱节不只是「少写了新功能」，还会**反过来报告一个已经不存在的 bug** ——
面试官看到 README 说有问题、代码里却修好了，会怀疑你没读过自己的代码。

#### ② `ingest.py` 与 `config.py` 出现了**两个 `.env` 定位器**，且 `ingest.py` 那个在独立仓库下会失效

并发提交 `1dd879d fix(deploy): ingest.py 的 .env 定位兼容容器布局（修复云上提问必炸）`
新增了 `ingest.py::_repo_env_file()`：

```python
def _repo_env_file(src: Path) -> Path | None:
    try:
        return src.resolve().parents[2] / ".env"   # 写死「上两层」
    except IndexError:
        return None
```

- ✅ **容器安全**（`/app/` 层级不足时返回 `None` 而不是 `IndexError`）
- ❌ **独立仓库不安全**：抽成独立仓库后 `PROJECT_DIR` 就是仓库根，`parents[2]` 会指到**仓库外面**
- ⚠️ **重复**：`config.py` 已有更健壮的 `_find_env_file()`（四级回退 + 逃生舱），
  而 `ingest.py:45` 本来就有 `from config import settings` —— 所以那个 `_repo_env_file` 是**冗余的**

**建议改法**（`ingest.py` 删掉 `_repo_env_file` 与 `load_dotenv`，统一由 `config` 加载）：

```python
# ingest.py 顶部
from config import settings, ENV_FILE   # config 内部已 load_dotenv（含容器与独立仓库布局）
```

⚠️ **但这会使其新增的 `tests/test_ingest_env.py` 失效**（它直接测 `_repo_env_file` 的语义）。
那两条测试的**意图**（容器布局不许崩）仍然宝贵，应改测 `config._find_env_file()`。
**因为那是另一个会话刚提交的代码，本方案不动它，留给用户决策。**

> 另一个会话的提交信息里也记了同源隐患：「run.py 也有 `parents[2]`，但不在容器运行时路径上」——
> 抽独立仓库时 `run.py` 要一并检查。

#### ③ 测试数量是**易变量**，README 不该硬编码

本会话期间只因一次并发提交，后端用例数就从 260 变成 262。所以 README 里所有数字都改成了
**「数值 + 实测日期」**的写法（如「后端 262 例，2026-09-22 实测」），并注明用哪条命令重新生成
—— 硬编码裸数字正是这份 README 当初脱节的根因。

---

## §0 现状体检（全部为实测数据，非估计）

| 维度 | 实测 |
|---|---|
| 后端测试 | `pytest tests --collect-only -q` → **260 例**（256 collected + 4 deselected） |
| 前端测试 | `npx vitest run` → **148 例 / 14 文件**（需 `NODE_ENV=test`，见 P0-2） |
| HTTP 端点 | `docs/api/README.md` 契约表 → **12 个**（含 2 个 SSE 流） |
| 代码规模 | 后端 ≈ 1.9k 行 Python（28 文件）/ 前端 ≈ 2.4k 行 TS/TSX（39 文件） |
| 检索评测 | `data/eval_results.json`：23 条 query，corpus 3755 段（预置 2885 + 上传 870） |
| 设计文档 | `docs/superpowers/specs/` 6 篇 + `plans/` 7 篇 |
| git | 仓根为 `advanced_tutorial/`，remote `7641751/advanced_tutorial.git` |
| CI | **无**（无 `.github/`） |
| 部署 | **未上线**（`2026-09-22-cloud-deploy.md` 已写好但未执行） |
| 截图 | **0 张**（所有 .md 中无任何图片引用） |

### 已经能打的部分（不要再投入时间）

- LangGraph CRAG 四节点 + 条件路由 + 重写回环，双 `stream_mode` 且**过滤掉了 grade/rewrite 的结构化 JSON**，只留 generate 的 token —— 这个坑很多同类项目都踩了
- 多租户数据隔离：JWT + MySQL 归属表，**删除越权返回 404 而非 403**（不泄露资源存在性），且 `thread_id` 场景**刻意用 403**（id 前端已知，不构成泄露）—— 两处相反的选择都有论证，这是能扛追问的设计
- Redis cache-aside + 降级 + Streams 消费组/`event_id` 幂等/死信 + 一键回滚开关
- 测试全离线（DI 注入 Fake 模型/向量库 + `tmp_path` 真 Chroma），零 API 额度
- `eval_retrieval.py` 的四配置对照 + RRF 权重扫描 + 精排净代价分析

---

## §1 战略决策点（先定这三个，再动手）

### D1：要不要用 Java 重写？—— **建议不重写**

| 方案 | 成本 | 收益 | 判断 |
|---|---|---|---|
| A. 全量 Java 重写（LangChain4j + Spring AI + Spring Boot） | 60h+，且生态成熟度低（流式 SSE、checkpointer、结构化输出都要自己造） | 「Java + AI」双标签 | ❌ 实习时间不够，半成品比没有更糟 |
| B. 加一个 Java 侧子服务对接（如 Spring Boot 写「文档上传 → 调 Python ingest」） | 15h+ | 简历多一行 Java | ⚠️ 边际收益低，容易被识破是凑数 |
| **C. 不重写，明确定位为「AI 应用落地 + 工程素养」证明，Java 能力由另一项目承载** | **0h** | 项目本身已足够强，时间全投入包装与深度 | ✅ **推荐** |

**选 C 的话，面试话术必须准备好**（见 §5），核心是：把项目里的工程决策用 Java 语境复述。
例如 cache-aside 那套 —— 「原理和 Spring 里 `@Cacheable` + 自定义 `CacheErrorHandler` + 配置开关一致，
只是我在 Python 里手写了一遍，所以我清楚它降级时的每一个失败点」，这比「我写过 `@Cacheable`」有说服力得多。

### D2：仓库要不要独立？—— **建议独立**

现状：仓根是 `advanced_tutorial/`，里面是 notebook 教程 + 项目；README 还写着「本项目是 advanced_tutorial 模块 2 的实践载体」。
**面试官点开链接看到的是一堆教学 notebook，项目埋在第三层目录。**

两种做法：

- **D2-a（推荐，1h）**：抽独立 repo。保留 `git log`（P1→P4 的 34 次提交历史本身就是加分项，别丢）：
  ```bash
  cd d:\PycharmProjects\my_langchain_demo
  git subtree split --prefix=advanced_tutorial/rag_qa_project -b ragqa-standalone
  # 新仓库初始化后：git pull ../my_langchain_demo ragqa-standalone
  ```
  或 `git clone` + `git filter-repo --path advanced_tutorial/rag_qa_project/ --path-rename ...`（更干净）
- **D2-b（保守，20min）**：保持 monorepo，但把 `rag_qa_project/README.md` 作为仓根 README，并加「本项目独立于教程」的说明

#### D2 前置条件清单（缺一即抽出后启动失败）

`git subtree split` 只带走**被 prefix 覆盖的已跟踪文件**，且所有「相对上层目录」的假设都会失效。
逐条核对：

| # | 项 | 状态 | 说明 |
|---|---|---|---|
| 1 | `rag_qa_project/.gitignore` | ✅ 已建 | 仓根那份不会被带走，而它负责忽略 `.env` —— 漏了就有密钥误提交风险 |
| 2 | `config.py` 的 `.env` 定位 | ✅ 已改 | `_find_env_file()` 四级回退，独立仓库布局自动落到 `PROJECT_DIR/.env` |
| 3 | `ingest.py` 的 `.env` 定位 | ⚠️ **待改** | 其 `_repo_env_file` 写死 `parents[2]`，独立仓库下会指到仓库外（见「重要发现 ②」） |
| 4 | `run.py` 的 `.env` 定位 | ⚠️ **待查** | 并发提交信息里提到它也有 `parents[2]` |
| 5 | CI 的 `working-directory` | ✅ 已写对 | `ci.yml` 里用的是独立仓库布局（根目录 + `frontend/`） |
| 6 | README 里的路径 | ✅ 已改 | 所有 `advanced_tutorial/rag_qa_project/` 前缀已去掉，改为「以项目根为基准」 |
| 7 | `pyproject.toml` / `.venv` | ⚠️ **注意** | 依赖管理现在在**仓库根**（`my_langchain_demo/pyproject.toml`）。独立仓库需要一个自己的 `pyproject.toml`，否则 `uv sync` 无从下手 |
| 8 | `.env` 迁移 | ⚠️ **注意** | 开发用的 `.env` 在 `my_langchain_demo/.env`，抽仓库后需复制到新仓库根并调整（`docker-compose` 用的是另一份） |

**抽取命令**（保留 34 次提交历史）：

```bash
cd d:/PycharmProjects/my_langchain_demo
git subtree split --prefix=advanced_tutorial/rag_qa_project -b ragqa-standalone
mkdir ../rag_qa_project && cd ../rag_qa_project
git init && git pull ../my_langchain_demo ragqa-standalone
```

**抽完立刻自检**（三条都必须过）：

```bash
python -c "from config import ENV_FILE; print(ENV_FILE)"     # 必须指向新仓库根，不是仓库外
python -m pytest tests -q                                    # 必须 257 passed, 1 skipped, 4 deselected
cd frontend && npm run test && cd ..                         # 必须 148 passed
```

### D3：要不要买服务器？—— **要，且优先级最高**

`docs/superpowers/plans/2026-09-22-cloud-deploy.md` 已经写得非常完整（连 swap、镜像源、故障表都有）。
实习简历上「线上地址可访问，注册即用」这一行的分量，超过再补三个功能模块。

成本：阿里云/腾讯云学生机约 ¥10/月，或新用户免费试用 1–3 个月。
验收标准沿用原计划：`http://<IP>:8080` 打开、注册登录、逐字流式、重启不丢数据。

---

## §2 关键发现：一个必须修的技术问题（这是本项目最大的加分点）

### 2.1 事实一：离线评测显示检索**没有问题**

从 `data/eval_results.json`（23 条 query，k=20 → top_n=5）提取：

| 配置 | recall@5 | MRR | hit@1 | p50 | p95 |
|---|---|---|---|---|---|
| **dense** | **100.0%** | **0.978** | **95.7%** | 338ms | 465ms |
| bm25 | 69.6% | 0.588 | 52.2% | 11ms | 16ms |
| hybrid (0.6/0.4) | 100.0% | 0.771 | 60.9% | 351ms | 546ms |

**结论（这是极好的简历素材）**：混合检索相对 dense **recall 零收益**，却把 **hit@1 从 95.7% 打到 60.9%**、MRR 从 0.978 打到 0.771。
所以「不引入 BM25」是一个**数据驱动的正确决策**，不是偷懒。

> 简历可以写：「用 23 条标注 query 做离线对照评估，证明 hybrid（RRF）检索在本文档语料上 recall 零增益、
> 且排序质量显著劣化（hit@1 95.7%→60.9%），据此决策不引入 BM25，避免增加索引常驻内存与上传后重建成本。」

### 2.2 事实二：线上实际体验**很差**

`README.md`「已知问题 §2」自己记录：问「RecursiveCharacterTextSplitter 怎么切分 markdown」时，
`top_k=4` 命中的全是 *Build a semantic search engine* 这类教程页，`grade` 连续三轮判 0/4 相关，重写耗尽后走兜底。

### 2.3 两个事实的矛盾 → 真正的根因

**评测口径是「文档级」，线上瓶颈是「段级」。**

- `eval_retrieval.py` 的判定（`is_hit`）：`top-k` 里**任一段**来自 `expected_sources` 之一，就算整条 query 命中
- 线上 `grade_documents`：LLM **逐段**判断这一段能不能回答问题

所以「命中文档 X」不等于「命中的那一段有用」。命中的是教程页里**顺带提到** splitter 的段落，
grade 判 False 是**正确的**——检索确实没找到讲这个问题的段落，只是找到了同主题文档。

**再叠加一层**：评测集有选择偏差。23 条 query 分布为 `term_en 8 / semantic_zh 8 / upload_zh 7`，
逐条看下来 **term_en 全是 LangGraph 核心 API 名**（`check_same_thread`、`GRAPH_RECURSION_LIMIT`、
`add_messages`…），`semantic_zh` 也全是 LangGraph 概念（长期偏好、人工审核、子图共享状态…）。

**这些恰好是「术语可精确匹配、文档主题明确」的简单 query。** 评测集里**没有一条**是
「概念分散在多个教程页、需要跨文档综合」的困难 query —— 而线上翻车的那条正是这种。

### 2.4 所以真正的改进方向不是「上更高级的检索」

| 方向 | 依据 |
|---|---|
| ❌ 上 hybrid / BM25 | §2.1 已用数据否决（hit@1 掉 35 个百分点） |
| ❌ 上云端精排 | `eval_retrieval.py` 已备好评估代码，但在 hit@1 已经 95.7% 的前提下，精排的 RTT 只会加在 SSE 首 token 之前，收益天花板极低 |
| ✅ **补评测集的困难样本 + 段级标注** | §2.3：现有评测集测不出真问题 |
| ✅ **加端到端指标（兜底率 / 重写次数 / TTFT）** | §2.3：文档级 recall 100% 却线上答不出来，说明缺的是 e2e 指标 |
| ✅ **放宽 grade 判定 / 调 top_k** | README FAQ Q2 已提示 `grade_strict`；`top_k=4` 在 grade 误杀时没有幸存余量 |

> **这一节就是本项目在面试里最值钱的 10 分钟。** 「我发现离线指标 100% 但线上体验很差，
> 排查后发现是评测口径（文档级）与线上判定（段级）不一致，叠加了评测集的选择偏差」——
> 这比「我用了混合检索 + 重排」高一个段位，因为它展示的是**指标设计与归因能力**。

---

## §3 P0：致命项（不修会直接扣分，合计约 5h）

### Task P0-1：README 与代码对齐（约 2–3h）

**Files:**
- Modify: `README.md`（多处，见下表）
- Modify: `docs/api/README.md:271`（`kb_count` 示例值 2885 → 3755）

**具体改动（每一处都已定位到行号）：**

| 位置 | 现在写的 | 应改为 |
|---|---|---|
| `README.md:17` | `InMemorySaver` 做服务端会话记忆 | `SqliteSaver`（`data/checkpoints.db`），单 worker 假设 |
| `README.md:21` | 「6 个端点，其中 2 个是 SSE 流」 | 「12 个端点，其中 2 个是 SSE 流」 |
| `README.md:23` | 可观测：LangSmith | 补 `/api/health` 就绪探针；LangSmith 保留 |
| `README.md:24` | 测试：pytest / vitest「全部离线跑」 | 补真实数量：后端 260 / 前端 148 |
| `README.md:13-24` 技术栈表 | 无 MySQL / JWT / Redis / Docker | **新增 4 行**（见下方代码块） |
| `README.md:456` | `# 25 passed（11 个工作流 + 14 个上传链路），约 6 秒` | `# 260 collected（256 + 4 deselected），约 XX 秒`（跑一次填真实值） |
| `README.md:461` | `# 55 passed（7 个文件，jsdom），约 4 秒` | `# 148 passed（14 个文件，jsdom），约 4 秒` |
| 架构章节 | 只有 CRAG 图 + 全栈数据流 | **新增「演进路线 P1→P4」**，说明每一期解决了什么问题 |
| 新增章节 | — | **「检索评估」**，贴 §2.1 那张表 + 结论 |
| 顶部 | — | 加 CI 徽章（P0-3 完成后）+ 线上地址（D3 完成后） |

技术栈表补的 4 行：

```markdown
| 鉴权 | JWT（pyjwt）+ bcrypt | `RAGQA_JWT_SECRET` ≥32 字节；401/403 分工见契约文档 |
| 关系库 | MySQL 8 + SQLAlchemy async（aiomysql） | 用户 / 会话归属 / 问答事件（`qa_events`）；单 worker 假设见开发约定 |
| 缓存 / MQ | Redis 7（可选依赖） | cache-aside 读缓存 + Streams 事件流 + 死信；`RAGQA_REDIS_CACHE_ENABLED=false` 一键回滚 |
| 部署 | Docker Compose | MySQL + Redis + backend + frontend(nginx 反代 /api，SSE 已关缓冲) |
```

**验收命令：**

```bash
cd advanced_tutorial/rag_qa_project
grep -n "6 个端点\|25 passed\|55 passed\|InMemorySaver" README.md
# 预期：无输出（全部已改）
python -m pytest tests --collect-only -q | tail -1
# 预期：260/260 tests collected  ← README 里的数字必须与之逐字一致
```

**另需**：清理 README 里的陈旧路径引用（若执行 D2-a 抽了独立 repo，所有 `advanced_tutorial/rag_qa_project/` 前缀要去掉）。

---

### Task P0-2：前端测试在 `NODE_ENV=production` 下全红（约 15min）

**实测复现：**

```bash
cd advanced_tutorial/rag_qa_project/frontend
$env:NODE_ENV='production'; npx vitest run
# 实测：Test Files 13 failed | 1 passed (14)   Tests 125 failed | 23 passed (148)
# 错误：Error: act(...) is not supported in production builds of React.
```

**根因**：`vite.config.ts` 的 `test` 段没有锁死 mode，React 被解析到 production 构建，`@testing-library/react` 的 `act()` 直接抛错。

**已实测验证**：加上 `NODE_ENV=test` 后 → **14 passed / 148 passed**。所以这不是代码回归，是环境脆弱性。

**Files:**
- Modify: `frontend/vite.config.ts:12-16`

```ts
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
    mode: 'test',           // ← 新增：钉死 mode，避免继承 shell 的 NODE_ENV
  },
```

- Modify: `frontend/package.json:10`（双保险，跨平台需 `cross-env`）

```json
    "test": "cross-env NODE_ENV=test vitest run",
```

> ⚠️ 若不想引入 `cross-env` 依赖，只加 `mode: 'test'` 即可 —— 但**必须实测**在 `NODE_ENV=production` 的 shell 下也是全绿，
> 因为 CI 或面试官机器上的环境变量不可控。

**验收命令：**

```bash
cd frontend
$env:NODE_ENV='production'; npm run test
# 预期：Test Files 14 passed (14)    Tests 148 passed (148)
```

---

### Task P0-3：加 CI（约 1–2h）

**Files:**
- Create: `.github/workflows/ci.yml`
- Modify: `README.md`（顶部加徽章）

**关键点**：仓根是 `advanced_tutorial/`（若执行 D2-a 抽了独立 repo 则仓根即项目根，路径要跟着改）。

```yaml
name: CI

on:
  push: { branches: [main, master] }
  pull_request:

jobs:
  backend:
    runs-on: ubuntu-latest
    defaults: { run: { working-directory: advanced_tutorial/rag_qa_project } }
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.14' }
      - run: pip install -r requirements.txt
      - run: python -m pytest tests -v        # 260 例，全离线，零 API 额度

  frontend:
    runs-on: ubuntu-latest
    defaults: { run: { working-directory: advanced_tutorial/rag_qa_project/frontend } }
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: '20', cache: npm, cache-dependency-path: advanced_tutorial/rag_qa_project/frontend/package-lock.json }
      - run: npm ci
      - run: npx tsc --noEmit                # 类型检查与测试分开跑，失败定位更快
      - run: npm run test
```

> ⚠️ 两个坑：① `requirements.txt` 里含 `sentence-transformers` / `langchain-huggingface`，会拖入 torch（CI 下载 ~2GB，慢且可能超时）——
> Dockerfile 已经用 `grep -vE` 剔除了这两行，CI 应复用同一策略；② 若前端测试用例真的依赖 API 额度，CI 会失败 —— 但按设计它是全离线的，正好借此验证这条基线。

**验收**：push 后 Actions 两个 job 全绿；README 顶部徽章可点。

---

### Task P0-4：独立仓库（决策 D2，约 1h）

**验收**：新 repo 的根目录**直接**是项目内容（`README.md` / `backend/` / `frontend/` / `docker-compose.yml`），
`git log --oneline | wc -l` ≥ 30（P1→P4 的提交历史完整保留）。

---

### Task P0-5：跑通部署（决策 D3，约 2–4h）

直接执行 `docs/superpowers/plans/2026-09-22-cloud-deploy.md`，不重复写。

**唯一要补的**：原计划 Task 5 的验收清单里没有 **D3 的简历化要求** —— 部署完成后补两件事：

1. **截图**（用 `agent-browser` skill 自动化）：登录页 / 流式回答中的步骤追踪 / 知识库抽屉上传进度 / 会话侧栏，共 4 张 → 放进 README
2. **公网延迟实测**（简历数字来源）：
   ```bash
   curl -N -w "\n首字节: %{time_starttransfer}s  总耗时: %{time_total}s\n" \
     -X POST http://<IP>:8080/api/chat/stream \
     -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
     -d "{\"question\":\"如何切分 markdown?\",\"thread_id\":\"$(uuidgen)\"}"
   ```

---

## §4 P1：决定「高级」还是「普通」（合计约 12–18h）

### Task P1-1：修掉 `generate` 兜底分支不产生 `token` 帧（约 1h，TDD）

**现状**：未命中知识库时前端答案气泡**完全空白** —— 因为 `graph.py` 的兜底分支直接 `return {"generation": ...}`
而不调用 model，`stream_mode="messages"` 一个 token 都不推。
实测事件序列：`step × 8 → sources → done`，`token` 帧数 = 0。

**Files:**
- Test: `tests/test_graph.py`（新增用例）
- Modify: `backend/app/agent/graph.py`（`generate` 节点兜底分支）

**Step 1：写失败测试**（`FakeRAGModel` 需能被断言「兜底时也被调用过」）

```python
def test_fallback_branch_emits_tokens(fake_model, fake_vectorstore):
    """未命中知识库时，兜底文案也必须走 token 流，否则前端答案气泡是空白的。"""
    graph = build_graph(model=fake_model, vectorstore=fake_vectorstore)  # 检索全不相关
    frames = list(collect_frames(graph, question="完全无关的问题"))
    assert [f for f in frames if f[0] == "token"], "兜底分支必须产生 token 帧"
    assert frames[-1][0] == "done" and frames[-1][1]["grounded"] is False
```

**Step 2**：`pytest tests/test_graph.py -k fallback -v` → 预期 FAIL（token 帧为空）

**Step 3**：改实现 —— 兜底分支改为「先 yield 兜底提示语，再用 model 流式生成正文」，
或最小改动版：在 `chat_service` 转发 `done` 之前补发一帧 `token`（内容是 `generation` 的兜底文案）。

> ⚠️ 选第二个方案时**必须**同步 `docs/api/README.md` 的 SSE 协议说明（`token` 现在有第二个来源）。
> 契约文档是唯一真相源，改了行为不改契约就是给自己埋雷。

**Step 4**：`pytest tests/test_graph.py -v` → 预期全绿（367 行的现有用例一个都不能红）

**Step 5**：手工验证 —— 起前后端，问一个知识库里没有的问题，确认气泡**显示**兜底提示而非空白

---

### Task P1-2：评测集补强 + 端到端指标（约 6–8h，本方案最高价值项）

**依据**：§2.3 —— 现有评测集有选择偏差，且只有文档级指标，测不出线上真问题。

**Files:**
- Modify: `data/eval_queries.json`（补困难样本 + 段级标注）
- Create: `eval_e2e.py`（端到端评测）
- Create: `tests/test_eval_e2e.py`

**Step 1：补困难样本到 `data/eval_queries.json`**

加入 `type: "hard_cross_doc"` 一类，特征：**答案分散在多个文档、且需要用术语的同义表达去问**。至少 8 条，且**必须包含已知失败的那条**：

```json
{ "id": "hard_01", "type": "hard_cross_doc",
  "query": "RecursiveCharacterTextSplitter 怎么切分 markdown",
  "expected_sources": ["..."],
  "expected_keywords": ["from_language", "Language.MARKDOWN"],
  "note": "README 已知问题 §2 的原案例，必须进评测集，否则永远测不出真问题" }
```

> ⚠️ 标注时**不要**只写 `expected_sources`。补一个 `expected_keywords` 字段（答案里必须出现的关键词/API 名），
> 这是从「文档级」升到「段级」的最小改动 —— 命中文档但段落里没有 `from_language`，就不算命中。

**Step 2：给 `eval_retrieval.py` 加段级判定**

在现有 `first_hit_rank` 之外加 `first_content_hit_rank`：只有 `page_content` 里同时出现 `expected_keywords`
才算命中，`expected_keywords` 缺失时退化为原口径（保持向后兼容，历史报表可比）。报表里**两栏并列**，
差值就是「文档级虚高」的量化值。

**Step 3：写 `eval_e2e.py`（端到端）**

跑**完整图**（不是只跑检索），逐条采集：

| 指标 | 采集方式 |
|---|---|
| `grounded` | 最终状态里 `documents` 是否非空 |
| `rewrites` | 最终状态 `rewrites` 计数 |
| `fell_back` | `grounded=False` 占比 —— **这叫「兜底率」，是本项目最该优化的指标** |
| `ttft_ms` | 首次收到 `token` 帧的时间（用 `time.perf_counter()` 打点） |
| `grade_keep_ratio` | `len(documents) / retrieved_count`（grade 幸存率） |

**Step 4：跑出基线并落盘** `data/eval_e2e_results.json`

**验收命令：**

```bash
cd advanced_tutorial/rag_qa_project
uv run python eval_retrieval.py --no-rerank --detail    # 看段级 vs 文档级的口径差异
uv run python eval_e2e.py --out data/eval_e2e_results.json
# 预期：打印兜底率 / 平均重写次数 / TTFT p50,p95，并落盘 JSON
```

**预期结论（可证伪的假设，跑完用真实数据修正）**：
- 困难样本上**文档级 recall 高但段级 recall 明显更低** → 坐实 §2.3 的归因
- 困难样本**兜底率显著高于简单样本** → 给出「兜底率」的优化基线

---

### Task P1-3：基于上一步的数据做检索/评分优化（约 4–8h，**不凭感觉调参**）

**只能从下面四项里选，且每项都要用 P1-2 的 e2e 指标回归验证：**

| 候选 | 依据 | 风险 |
|---|---|---|
| a. 放宽 `grades` 判定（`config.py` 的 `grade_strict`） | README FAQ Q2；`_GRADE_RULES` 已写「宁可多留」 | 留太多噪声段落进上下文，生成质量下降 → 看 `grounded` 上升但答案质量要人工抽检 |
| b. `top_k` 4 → 6/8（`RAGQA_TOP_K`） | §2.1 显示 dense 在 top_5 内 recall 已 100%，说明**排序没问题**，问题是 grade 误杀时没有幸存余量 | 上下文变长 → TTFT 上升、token 成本上升 |
| c. markdown 感知切分（`RecursiveCharacterTextSplitter.from_language(Language.MARKDOWN)`） | 现有 `chunk_size=1000/overlap=150` 是通用切分 | 需重建向量库（3319 段 ≈ 332 次嵌入请求，有 API 成本）——README 已验证「入库去重+滤超短片段」不值得为此重建，切分策略变更要重新论证 |
| d. 检索结果去重（滤掉重复 `page_content`） | README 已记录 RRF 对重复内容累加计分；`_big_sample.md` 就是重复内容样本 | README 已实测：垃圾仅占 2/96 席位，滤掉后指标不变 → **大概率无效，除非 P1-2 的新评测集推翻了它** |

**验收命令：**

```bash
uv run python eval_e2e.py --out data/eval_e2e_after.json
# 然后对比两份 JSON，产出下面这张表填进 README：
```

| 指标 | 优化前 | 优化后 | Δ |
|---|---|---|---|
| 兜底率（全部） | ? | ? | ? |
| 兜底率（hard_cross_doc） | ? | ? | ? |
| 段级 recall@5 | ? | ? | ? |
| TTFT p50 / p95 | ? | ? | ? |

> ⚠️ **如果测下来没有改善，就诚实地写「测了，无效」** —— README 里已经有过一次这样的先例（§「已知问题」的重复内容分析、
> P4 章节的「诚实声明：本期不带来性能收益」）。**这比伪造一个漂亮的数字安全得多**：
> 面试官一旦追问「这个 15% 是怎么测的」，假数字当场崩盘；而「我测了三种方案都没改善，
> 最后定位到瓶颈在 grade 的判定粒度」是加分回答。

---

## §5 P2：打磨（合计约 4–6h）

### Task P2-1：清理死代码与误入文件（30min）

| 文件 | 处置 | 理由 |
|---|---|---|
| `_big_sample.md` | 删 / 移到 `tests/fixtures/` | 1 行重复 1000+ 遍的占位文本，是「重复 chunk 分析」的样本，不是项目内容 |
| `data/knowledge.json` | 删 | README FAQ Q7 自己说「当前代码没有任何地方引用它」 |
| `backend/_sse_upload_demo.py` | 移到 `docs/examples/` | 教学 demo，有价值但不该出现在 `backend/` 顶层（`biz` 目录下划线前缀文件会被误认为生产代码） |
| `backend/_verify_prefix.py` | 删 | 24 行的一次性校验脚本 |
| `backend/app/services/mermaid1789283356857.mermaid` | 删 | 误入的依赖图文件，带随机时间戳后缀，明显是工具产物 |

**验收**：`git ls-files | grep -E "_big_sample|knowledge.json|mermaid1789|_verify_prefix"` 无输出。

### Task P2-2：依赖清单统一（1h）

**现状矛盾**：`Dockerfile:19` 用 `grep -vE '^(sentence-transformers|langchain-huggingface)'` 剔除两行，
说明这两个依赖**运行时不需要**；但 `pyproject.toml:39` 仍把它们列为正式依赖 → 本地 `uv sync` 会拖入 torch（~2GB）。

**处置**：把 `sentence-transformers` / `langchain-huggingface` 从 `pyproject.toml` 的 `dependencies` 移到
`dependency-groups.dev`，或直接删除（`config.py` 顶部已有 HF_ENDPOINT 处理，说明是历史遗留）。
同步更新 `requirements.txt` 与 Dockerfile（剔除逻辑可保留作防御，但注释要改）。

**验收**：`uv sync` 后 `python -c "import torch"` 报 `ModuleNotFoundError`；`pytest tests` 仍 260 例全绿。

### Task P2-3：lint + 类型检查（1–2h）

- 后端：`ruff`（lint + format）+ `mypy`（先只对 `backend/app/` 开，历史代码逐步收）
- 前端：`eslint` + `prettier`
- `pre-commit` 钩子串起来，CI 里加一个 `lint` job

**验收**：`ruff check .` / `mypy backend/app` / `npx eslint src` 全部零错误；CI 新增 job 绿。

### Task P2-4：可观测性（2h，可选但出彩）

**依据**：现在只有 LangSmith（第三方 SaaS）。自己产一份指标更显工程能力，且和 SSE 场景天然契合。

- `GET /metrics`（Prometheus 文本格式）：`ragqa_chat_requests_total` / `ragqa_ttft_seconds`（直方图）/
  `ragqa_rewrites_total` / `ragqa_fallback_total` / `ragqa_kb_chunks`
- **`ragqa_fallback_total` 直接复用 P1-2 的兜底率概念** —— 线上监控和离线评测用同一个口径，这是很好的设计闭环
- 结构化日志（`structlog`）：`request_id` / `user_id` / `thread_id` / `node` / `duration_ms`

**验收**：`curl /metrics | grep ragqa_ttft` 有输出；压测一轮后直方图分位数合理。

### Task P2-5：LICENSE + 截图（30min）

- `LICENSE`：MIT（没有 LICENSE 的 repo 会被认为不规范）
- 4 张截图进 README（见 Task P0-5）

---

## §6 简历文案（按 §3–§5 完成后可直接用）

> **LangChain / LangGraph 文档智能问答系统** ｜ 独立开发 ｜ [线上地址] ｜ [GitHub]
>
> 基于 LangGraph 实现 CRAG 纠错式 RAG 工作流（检索 → 相关性评分 → 查询重写 → 生成），
> FastAPI 以 SSE 流式推送推理步骤与答案 token，React 前端渲染 Markdown 与来源引用；
> 知识库为 88 篇官方文档（3755 段向量），支持用户自助上传并按租户隔离。
>
> - **多租户隔离**：JWT 鉴权 + MySQL 归属表，文档删除越权返回 404 而非 403 以避免泄露资源存在性；
>   检索侧用 `{"$or":[{"kb":"..."},{"user_id":uid}]}` 实现「预置文档全局共享、上传件仅本人可见」
> - **可观测与降级**：Redis cache-aside 读缓存 + Streams 事件流（消费组 / `event_id` 幂等 / 死信队列），
>   提供配置开关一键回滚到无缓存行为；缓存故障一律降级回源，不升级为业务故障
> - **检索评估驱动决策**：搭建 23 条标注 query 的离线对照评估（recall / MRR / hit@1 / p50-p95），
>   证明 hybrid（RRF）检索 recall 零增益且排序质量显著劣化（hit@1 95.7%→60.9%），
>   据此决策不引入 BM25，规避索引常驻内存与上传后重建成本
> - **发现问题并归因**：定位到「离线文档级指标 100% 但线上频繁走兜底」的口径不一致问题，
>   补充段级判定与端到端指标（兜底率 / 重写次数 / TTFT），将困难样本兜底率从 X% 降至 Y%
> - **工程质量**：408 个自动化测试（后端 260 / 前端 148）全离线运行，GitHub Actions 全绿；
>   Docker Compose 一键部署（MySQL + Redis + nginx 反代，SSE 已关缓冲）

**写作纪律（很重要）**：
- 每一个数字都必须能**当场用一条命令跑出来**（面试官真的会让你跑）
- 没做完的条目**不要写进简历**。写了「兜底率从 X% 降至 Y%」但答不出 X 怎么测的，比不写差十倍
- 「408 个测试」这种数字最好配 CI 徽章，一眼可验证

---

## §7 面试追问准备（Java 倾向岗位必看）

### Q：你这个项目是 Python 的，我们这边是 Java，你怎么看？

**答**：「这个项目我刻意用 Python 做，因为 LangGraph 的 checkpointer、结构化输出、双 stream_mode 这些能力在 Python 生态里最成熟，我想先把 RAG 链路的坑踩透。但项目里的工程部分——多租户隔离、缓存降级、MQ 幂等与死信、测试注入——这些是语言无关的，我在 Java 里也知道对应的做法（`@Cacheable` + 自定义 `CacheErrorHandler`、Spring 的事务边界、JUnit 的依赖注入），只是在这个项目里我用 Python 手写了一遍，所以每个降级分支的失败点我都清楚。」

### 必答的追问（这个项目最容易被问穿的地方）

| 追问 | 你要能答出的点 |
|---|---|
| 「为什么删除越权返回 404，重命名会话却返回 403？」 | 404 是为了不泄露 **doc_id 的存在性**（doc_id 来自服务端列表，泄露存在性有信息价值）；`thread_id` 由前端 `crypto.randomUUID()` 生成、就写在 URL 里，用户本来就知道它存在，403 不构成额外泄露 |
| 「`InMemorySaver` 和 `SqliteSaver` 你最后用的哪个？为什么？」 | 必须答对 —— README 现在写的和代码不一致，先把 P0-1 改完再面试 |
| 「重写循环会不会死循环？」 | `max_rewrites=2` 是安全阀，耗尽后走 generate 兜底分支，不会无限循环 |
| 「离线评测 recall 100%，是不是说明你的检索很好？」 | **这是陷阱题**。正确答案见 §2.3：文档级口径掩盖了段级问题，要主动说出这个局限 |
| 「为什么不做 rerank？」 | 拿出 §2.1 的表：hit@1 已经 95.7%，rerank 是 cross-encoder 只能重排给定候选、无法召回上游漏掉的文档，recall 天花板不变，却要付出云端 RTT 加在首 token 之前 |
| 「缓存这套带来多少性能提升？」 | 如实说 ≈0（README 已声明）。价值在把 cache-aside + 降级 + 派生数据的模式走通，以及为将来真正的热点（检索侧 dense p50 338ms）备好可复用工具 |
| 「怎么保证上传了一半中断不污染检索？」 | `asyncio.CancelledError`（**不是 `GeneratorExit`**，实测过）+ 按 `doc_id` 连向量带落盘原件一起回滚 |

### 建议补的 Java 侧准备

本项目不重写，但简历上**必须有 Java 的东西**。最低成本方案：另做一个 Spring Boot 小项目作为 Java 能力证明，
或者把已有 Java 项目按同样的标准（测试 + CI + README）包装。**不要让面试官问出「你的 Java 项目在哪」而答不上来。**

---

## §8 执行顺序与优先级总表

| 优先级 | Task | 耗时 | 简历回报 |
|---|---|---|---|
| **P0** | P0-2 前端测试锁 NODE_ENV | 15min | 🔴 高（当前一跑就红） |
| **P0** | P0-1 README 对齐 | 2–3h | 🔴 极高（面试官第一眼看的就是它） |
| **P0** | P0-5 部署上线（D3） | 2–4h | 🔴 极高（唯一的「可验证产出」） |
| **P0** | P0-3 CI | 1–2h | 🟠 高（把「你说了」变成「可验证」） |
| **P0** | P0-4 独立仓库（D2） | 1h | 🟠 高（决定面试官能不能找到项目） |
| **P1** | P1-1 修兜底 token 帧 | 1h | 🟠 高（用户可见的功能 bug，README 自曝） |
| **P1** | P1-2 评测集 + e2e 指标 | 6–8h | 🔴 极高（本项目最值钱的 10 分钟） |
| **P1** | P1-3 检索优化 + 回归验证 | 4–8h | 🟠 高（有数字才有说服力） |
| **P2** | P2-1 清理死代码 | 30min | 🟡 中（体现素养） |
| **P2** | P2-2 依赖统一 | 1h | 🟡 中 |
| **P2** | P2-3 lint / 类型检查 | 1–2h | 🟡 中 |
| **P2** | P2-4 可观测性 | 2h | 🟢 加分 |
| **P2** | P2-5 LICENSE + 截图 | 30min | 🟡 中 |

**合计约 22–34h** —— 按实习准备期 2–3 周的业余时间（每周 10h）刚好。

**如果只有 8 小时**（最小可行集）：P0-2 → P0-1 → P1-1 → P0-5。
这四件事做完，这个项目就已经是一个**及格线以上的实习简历项目**了。

---

## §9 已知风险

| 风险 | 说明 | 应对 |
|---|---|---|
| 部署成本 | 云服务器 ¥10/月 | 用学生机或新用户免费试用；实在不行用 Cloudflare Tunnel 把本机暴露到公网（免费，但需要本机常开） |
| API 额度 | P1-2/P1-3 的 e2e 评测要跑真实 LLM（23 条 × 多次重写） | `eval_retrieval.py` 已有 `--quick` 冒烟；e2e 脚本应支持 `--limit N` |
| 抽独立 repo 后路径断裂 | README / Dockerfile / CI 里的 `advanced_tutorial/rag_qa_project` 前缀 | P0-4 后用 `grep -rn "advanced_tutorial" .` 全量扫一遍 |
| P1-3 优化无效 | 很可能实测下来指标不动（README 已有两次这样的先例） | 如实记录，把「归因过程」本身当作成果 —— 见 Task P1-3 的 ⚠️ |
| `grade_strict` 放宽引入噪声 | 留太多无关段落会降低答案质量，而 e2e 指标只能测「是否兜底」，测不了「答得好不好」 | P1-3 改动后**必须人工抽检 10 条答案**，不能只看数字 |
