# 会话列表 Redis 读缓存 + MQ 派生数据落库（P4）设计文档

- 日期：2026-09-21
- 前置：P2（账号鉴权，`conversations` 表与 `assert_owner`）、P3（会话列表端点与 KB 隔离）
- 相关既有实现：`backend/app/services/conversation_service.py`、`backend/app/api/deps.py`（`get_redis_pool`）、`config.py`（`redis_url`）

---

## 1. 背景与问题

原始需求是「用 Redis 缓存 conversation 表一段时间的会话，并通过 MQ 队列写入 MySQL」。
落笔前先做了只读实测（2026-09-21，30 次采样）：

| 测量项 | 实测值 |
|---|---|
| `conversations` 全表行数 | **3 行**（5 个用户中 2 个各有 1 条） |
| `list_conversations` | **p50 0.99ms** / p95 1.68ms |
| `assert_owner`（每请求） | **p50 0.01ms** / p95 0.02ms |
| checkpointer 规模（消息历史所在） | 48 checkpoints / 117 writes / 3 个线程，载荷 358.5 KB |
| `graph.aget_state`（读消息历史） | **p50 0.3–0.4ms** / p95 0.5–1.9ms |
| 写入频率（读代码得出） | 每轮问答 1 次 `upsert_conversation`；rename/delete 极少 |

**结论必须写在最前面：这两条缓存路线的性能收益都≈0（亚毫秒~2ms）。**

因此本期的定位是**模式演练**，不是性能优化 —— 文档不假装它能带来加速，也不允许为了「看起来更快」引入正确性风险。真正值得优化的是检索侧（dense p50 197ms），那是另一期的事。

### 原始方案的两个致命问题

1. **把真相源延后落库**：`conversations` 是真相源，且是**归属校验的唯一依据**（P2 spec §3 决策 5）。
   走 MQ write-behind 意味着「刚问过的会话可能还不在 MySQL」——列表缺行、删除被延迟执行后会「复活」。
2. **拿缓存做授权判断**：`assert_owner` 若读缓存，一次缓存不一致就直接变成**越权或误拒**。
   安全边界不能建立在可丢失的副本上。

## 2. 目标与非目标

### 目标

1. 在**会话列表读路径**上引入 Redis 读缓存（cache-aside），验证「缓存命中/失效/降级」三件事在真实代码里怎么落地。
2. 引入一个 MQ（**Redis Streams**），把**派生数据**（问答统计）异步写入 MySQL —— 即原始需求里「通过 MQ 队列写入 MySQL」的那部分，但载在**可丢可重算**的数据上。
3. **契约零变化**：所有端点的请求/响应结构与今天逐字相同。
4. **降级是硬要求**：Redis 全挂时，系统行为与今天完全一致（不报 5xx、不改语义）。

### 非目标（YAGNI）

| 不做 | 理由 |
|---|---|
| `conversations` 的 write-behind | 真相源不延后落库（见 §1） |
| 用缓存做 `assert_owner` | 安全边界不可用可丢副本 |
| 缓存消息历史（checkpointer） | 收益 p50 0.3ms；且等于与 LangGraph 争真相源，失效点要侵入图执行流 |
| 引入 RabbitMQ / Kafka | 新基础设施 + 运维面，而 Redis Streams 的消费语义已够用 |
| 多级缓存（本地 + Redis） | 单实例项目，收益为负 |
| 缓存预热/自动续期 | 冷启动回源仅 1ms，不值 |

## 3. 决策记录

| # | 决策 | 被否决的替代方案 | 理由 |
|---|---|---|---|
| 1 | 缓存目标 = 会话列表元数据 | 缓存消息历史 | 实测 0.3ms；且与框架内部状态冲突 |
| 2 | MySQL 保持**同步**写入 | MQ write-behind | `conversations` 是真相源 |
| 3 | 授权（`assert_owner`）**永走 MySQL** | 缓存授权结果 | 缓存不一致 → 越权/误拒 |
| 4 | 写路径只**删键**，不更新缓存 | 写时更新缓存值 | 并发写会互相覆盖；且 `rename` 的「不刷新 `updated_at`」语义（P3 踩过）要在缓存层再实现一遍 |
| 5 | TTL 作为**兜底**，不是主策略 | 只靠 TTL 过期 | 只靠 TTL 会让改名最长滞后一整个 TTL |
| 6 | MQ 只承载派生数据（问答统计） | MQ 承载 `conversations` 写入 | 派生数据可丢可重算，真相源不可 |
| 7 | MQ 用 **Redis Streams** | RabbitMQ / Kafka | 复用已有 Redis，零新基础设施；支持消费组、ACK、重投 |
| 8 | 新增 `qa_events` 表存统计 | 只写文件/日志 | 原始需求明确要求「经 MQ 写入 MySQL」 |
| 9 | 开关默认开，可一键关 | 无开关 | 出问题时关闭缓存即回到今天的行为（回滚手段） |

## 4. 架构与数据流

```
                 ┌──────────── 读：GET /api/chat/threads ────────────┐
   前端 ─────────▶│ 1) Redis GET ragqa:conv:list:v1:{user_id}         │
                 │ 2) 命中 → 直接返回（不碰 MySQL）                  │
                 │ 3) miss/异常 → MySQL（今天的 list_conversations）  │
                 │              → SETEX 写回缓存 → 返回              │
                 └───────────────────────────────────────────────────┘

                 ┌──────────── 写：提问 / 改名 / 删会话 ──────────────┐
                 │ 1) MySQL 提交（真相源，顺序不可颠倒）             │
                 │ 2) DEL ragqa:conv:list:v1:{user_id}               │
                 └───────────────────────────────────────────────────┘

   每轮问答结束（done）：
   生产者 ──XADD──▶ ragqa:stream:qa_stats ──XREADGROUP──▶ 消费者
                                                        └▶ 写 MySQL qa_events 表
```

**三个不可颠倒的顺序**：

1. **先写 MySQL，再删缓存**。反过来会留下「库还是旧值、缓存已空」的窗口，被并发读回填旧值。
2. **先校验归属，再读缓存**。授权判断在 MySQL（今天的 `assert_owner` 一行不动）。
3. **MQ 事件的产生在业务成功之后**。写不进去统计不该影响问答本身。

## 5. Redis 键设计

| 键 | 类型 | 内容 | TTL |
|---|---|---|---|
| `ragqa:conv:list:v1:{user_id}` | String(JSON) | `{"threads":[{thread_id,title,created_at,updated_at}...],"total":N}` | `RAGQA_CONV_CACHE_TTL`（默认 60s） |
| `ragqa:stream:qa_stats` | Stream | 问答事件 | 无（用 `MAXLEN ~ 10000` 截断） |
| `ragqa:stream:qa_stats:dead` | Stream | 重试仍失败的死信 | 无 |

**为什么键名带 `:v1:`**：将来若要改缓存里的 JSON 结构（比如列表字段变化），不必写迁移脚本 —— 直接把版本号改成 `v2`，旧键自然不可达、随 TTL 消失。这比「先清了 Redis 再发版」可靠（清缓存这一步总有人忘）。

**`total` 必须进缓存**：P3 已明确它是前端显示「仅显示最近 50 条」的唯一依据（`LIST_LIMIT=50`）。缓存里只存 `threads` 会让截断提示失效。

**TTL 为什么是 60s 而不是更长**：TTL 只是兜底（主策略是写时删键）。但**多实例部署下**，A 实例的写不会删 B 实例的键 —— 那时 TTL 就是唯一的收敛上限。60s 是「最坏情况下用户看到的最长陈旧时间」，比 5 分钟更保守，且回源只要 1ms 所以过期代价极低。

## 6. 实现设计

### 6.1 配置（`config.py`）

```python
# ---- Redis 缓存（P4）----
redis_cache_enabled: bool = True    # 一键回退到「无缓存」行为（RAGQA_REDIS_CACHE_ENABLED）
conv_cache_ttl: int = 60            # 列表缓存 TTL 秒（RAGQA_CONV_CACHE_TTL）
qa_stream_key: str = "ragqa:stream:qa_stats"
```

沿用 P2 决策 4 的口径：给默认值而不是占位符；缺 `redis_url` 时**缓存自动降级**（见 §7），不快速失败 —— 与 `mysql_database_url` 不同，Redis 是可选依赖。

### 6.2 读路径（`conversation_service.list_conversations` 之上加一层）

新增 `backend/tools/redis_cache_tools.py`（纯工具，不碰请求上下文）：

```python
async def cached_json(redis, key: str, ttl: int, loader) -> tuple[Any, bool]
    """cache-aside 读：命中返回 (值, True)；miss/损坏/异常则调 loader 回源并写回。
    任何 Redis 异常都在这里被吞掉并记 warning —— 调用方永远拿到可用数据。"""
```

`list_conversations` 的调用点改成先走它。**关键实现约束**：

- `loader` 是**原地不动**的今天那段 MySQL 查询（不重写、不优化），这样回源路径与今天逐字一致。
- 异常区分两类：**连接类**（`RedisError`）→ 降级；**序列化类**（`json.JSONDecodeError`、键结构不符）→ 当 miss 并 `DEL` 掉坏值（否则每次请求都白读一次坏键）。

### 6.3 写路径（三处，只加一行删键）

`upsert_conversation` / `rename_conversation` / `delete_conversation_row` 的调用方（`chat_service`）在 **MySQL commit 之后**调 `await invalidate_conv_list(redis, user_id)`。

⚠ **不要**改这三个函数内部去删缓存 —— 它们有的不 commit（由调用方统一 commit，见 `delete_chat` 的顺序注释）。删键必须发生在 commit 成功之后，否则「删了键但事务回滚」就留下空缓存 + 旧库。

### 6.4 MQ 生产端与消费端

- 生产端：`chat_service.stream_chat` 的 `done` 分支里 `XADD`（`MAXLEN ~ 10000`）。字段：
  `user_id` / `thread_id` / `rewrites` / `grounded` / `latency_ms` / `event_id`（= 生成的 uuid4）。
  **失败只记 warning，绝不影响 SSE 流**。
  ⚠ `latency_ms` 需要**新增计时**：核对过代码，`stream_chat` 目前**没有任何耗时测量**
  （`done` 事件只有 `thread_id/rewrites/grounded`）。要在 generator 入口取一次
  `perf_counter()`、到 `done` 时相减。这是本期唯一的新增埋点 —— 顺带补上「一轮问答到底多慢」
  这个今天完全看不到的数字（而它恰是判断「该不该做检索侧缓存」的唯一依据）。
- `qa_events` 表（新）：`id`(自增) / `event_id`(String(36), **UNIQUE**) / `user_id` / `thread_id` / `rewrites` / `grounded` / `latency_ms` / `created_at`。
- 消费端：`lifespan` 启动时起一个 asyncio 后台任务，`XREADGROUP`（消费组 `qa_consumers`）+ `XACK`：
  - 消费成功 → `XACK`
  - 抛异常 → **不 ACK**（留在 PEL 里待重投），同一事件重试超过 3 次 → `XADD` 到 `:dead` 流 + `XACK` 原事件 + 记 error 日志
  - 关闭时取消任务并等待退出（与 `aclose_db` 同一处收尾）
- **幂等靠 `event_id` 唯一键**：Redis Streams 至少一次投递，重复消费会撞唯一键 → 捕获 `IntegrityError` 当作成功（派生数据不重复计数）。

## 7. 错误处理矩阵

| 场景 | 行为 | 用户可见影响 |
|---|---|---|
| Redis 未配置（`redis_url` 为空） | 缓存层整体旁路 | **无**（行为同今天） |
| Redis 连不上 / 命令报错 | 记 warning，回源 MySQL | **无** |
| 缓存命中但 JSON 损坏 | 当 miss，`DEL` 坏键，回源 | 无 |
| 缓存内容与当前用户不符（键串了） | 视为 bug：记 error + 当 miss（**不返回别人的数据**） | 无 |
| Redis 恢复 | 后续请求自动重新填充 | 无 |
| `XADD` 失败 | warning | 无（少一条统计） |
| 消费者持续失败 | 进死信流 + error 日志 | 无（统计缺失） |
| MySQL 挂了 | 与今天一致（500） | 同今天 |

**贯穿原则：缓存层的任何故障都不得升级为业务故障。**

## 8. 接口契约

**无变化** —— 这是本期的硬约束，也是验收项：

- `GET /api/chat/threads` → `{"threads":[...],"total":N}` 结构、字段名、时间格式（ISO 8601 UTC）、`LIST_LIMIT=50` 截断行为全部不变。
- `PATCH /api/chat/threads/{id}`、`DELETE /api/chat/threads/{id}`、`GET /api/chat/history` 的错误码矩阵（403/404/422）不变。
- 不新增对外端点（`qa_events` 不对外暴露）。

## 9. 测试策略

沿用 `pytest.ini` 的分层：离线用例是基线，需要真实 Redis 的用例打 `@pytest.mark.redis`（默认不跑）。

### 9.1 离线（默认套件，零外部依赖）

用**协议级假客户端**（记录 `get/set/delete/xadd` 调用，可注入异常）：

| # | 用例 | 钉住的契约 |
|---|---|---|
| 1 | 缓存命中时不查 MySQL | 用计数替身断言 MySQL loader **0 次**调用 |
| 2 | miss 时回源并写回 | loader 1 次 + `set` 带 `ex=ttl` |
| 3 | 命中时 `total` 与 `threads` 同时返回 | 防止只缓存列表导致「截断提示」失效 |
| 4 | 写路径**先 MySQL 后 DEL** | 用调用序列断言顺序（不是集合） |
| 5 | Redis 抛连接异常 → 仍返回正确列表 + warning | 降级 |
| 6 | 缓存 JSON 损坏 → 当 miss 且 `DEL` 坏键 | 防「每次请求都白读坏键」 |
| 7 | `redis_cache_enabled=False` → 完全不碰 Redis | 回滚开关有效 |
| 8 | 键名带用户 id，两个用户不共用键 | 隔离 |
| 9 | 消费端：正常事件写入 `qa_events` | 字段映射 |
| 10 | 消费端：`IntegrityError`（重复 `event_id`）不报错、不重复插 | 幂等 |
| 11 | 消费端：消费异常不 ACK | 至少一次语义 |
| 12 | 超 3 次失败进死信流 | 不无限重试 |

### 9.2 在线（`-m redis`，需真实 Redis）

| # | 用例 |
|---|---|
| 13 | `SETEX` 后 TTL 落在 (0, ttl] 区间（真 Redis 才验得了） |
| 14 | 写路径删键后，读路径确实回源（命中率从 1 变 0） |
| 15 | Streams 生产→消费→MySQL 全链路一次（含 `XACK`） |

### 9.3 不测什么

不测 Redis 自身行为（连接池、TTL 精度）；不为缓存单独写压测（收益本就是 0）。

## 10. 数据模型改动

新增一张表（**只增不改**，不动 `conversations` / `users`）：

```sql
CREATE TABLE qa_events (
  id          BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  event_id    VARCHAR(36)  NOT NULL UNIQUE,   -- 幂等键（Redis Streams 是至少一次投递）
  user_id     BIGINT UNSIGNED NOT NULL,
  thread_id   VARCHAR(36)  NOT NULL,
  rewrites    INT          NOT NULL,
  grounded    TINYINT(1)   NOT NULL,
  latency_ms  INT          NOT NULL,
  created_at  DATETIME(6)  NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
```

`init_db()`（`create_all`）会自动建出它，无需迁移脚本 —— 与 `users`/`conversations` 同一机制。**不建外键**：派生数据不该因为用户被删而级联消失（统计要能独立留存），且避免 CASCADE 误删事件。

## 11. 上线顺序（顺序错了会出问题）

1. **先修 Redis 认证**：`.env` 第 30 行的 `redis://root:<密码>@…` 中 `root` 用户不存在（实测 `AuthenticationError`），删掉用户名或改 `default`。**否则整条链路起不来**。
2. 加配置项 + `qa_events` 模型（`init_db` 建表）。
3. 接**读缓存 + 三处失效**（含降级）→ 跑 9.1 的表 1–8。
4. 接 **MQ 生产端 + 消费端**（含幂等与死信）→ 跑表 9–12。
5. 跑在线用例（`-m redis`）。
6. 端到端：开缓存/关缓存各跑一遍前端侧栏的「列表 / 改名 / 删除 / 切换」。

## 12. 验收标准

| # | 标准 | 判据 |
|---|---|---|
| 1 | **契约零变化** | 所有端点的响应 JSON 与改动前逐字相同（对比录制） |
| 2 | **降级成立** | 停掉 Redis：所有端点无 5xx，列表/改名/删除行为与今天一致 |
| 3 | **命中确实生效** | 命中时 MySQL 查询计数为 0（用例 #1 断言） |
| 4 | **失效即时** | 改名/删除后 <1s 内列表反映新值 |
| 5 | **顺序正确** | 写路径的调用序列是 `MySQL commit → DEL`（用例 #4） |
| 6 | **授权未被缓存** | `assert_owner` 的调用路径上不出现 Redis 调用（用例可读性检查 + 代码搜索） |
| 7 | **MQ 闭环** | 一轮问答后 `qa_events` 多一行；重复投递不重复插入 |
| 8 | **开关可回滚** | `RAGQA_REDIS_CACHE_ENABLED=false` 后行为回到今天 |
| 9 | 不劣化 | 不设延迟改善指标（收益本为 0），只要求「不劣化」 |

## 13. 风险与对策

| 风险 | 对策 |
|---|---|
| 缓存不一致导致显示错误 | 写时删键 + 短 TTL + 降级；**授权永不走缓存**（最重要的那条） |
| 消费者停了导致统计缺失 | 派生数据可容忍；死信流 + error 日志；`MAXLEN` 防止无限增长 |
| Redis 变成隐性依赖 | 全部 Redis 调用都包在降级逻辑内；验收 #2 专门守它 |
| 有人把缓存当成真相源继续加功能 | 本文档 §1/§2 已写明定位；`redis_cache_tools` 的 docstring 里再写一次 |

## 14. 明确写下的诚实声明

**本期不带来性能收益**（实测收益≈0），它的价值是：

1. 把「cache-aside + 失效 + 降级 + MQ 派生数据」这套模式在真实代码里走通一遍；
2. 为将来真正的热点（检索侧，dense p50 197ms）准备好可复用的工具与降级习惯；
3. 把「哪些数据可以延后写、哪些绝不能」这条判断线，用一份带实测数字的文档钉在仓库里。

如果后续要评估「缓存到底有没有用」，判据应该是**命中率与 MySQL 查询计数**（本期都测得到），而不是延迟。
