# -*- coding: utf-8 -*-
"""把现有上传文档的向量补上 user_id（一次性脚本，P3 上线时跑）。

为什么必须跑
------------
P3 上线后检索过滤变成 `{"$or":[{"kb":"langchain_docs"},{"user_id":uid}]}`。
P3 之前上传的向量**没有 user_id**，两个子条件都不匹配 → 对所有人都不可见
（连原上传者也看不见），KB 列表同样查不到。不迁移等于那 20 份资料凭空消失。

用法
----
    python scripts/migrate_kb_user_id.py --dry-run          # 先看数量，不写入
    python scripts/migrate_kb_user_id.py --yes              # 实际写入（归给第一个注册用户）
    python scripts/migrate_kb_user_id.py --yes --user hao    # 显式指定归属
    python scripts/migrate_kb_user_id.py --dry-run          # 再跑应为 0 段（验证幂等）

⚠ 上线顺序：**先迁移、后部署新代码**。反过来的话，从部署到迁移完成这段时间里，
所有上传件对所有人不可见；先迁移则无副作用（多出来的 user_id 在旧代码下被忽略）。

⚠ 本脚本**不改动重算向量**（不调嵌入）：走 raw collection 的 `update`，只传
ids + metadatas，Chroma 会跳过嵌入（CollectionCommon.update 里 update_embeddings
为 None）。2885 段全量重嵌是真实的 DashScope 额度成本，别用 add_documents 的写法。
"""
import argparse
import asyncio
import sys
from pathlib import Path

# 让脚本能直接 `python scripts/xxx.py` 运行（把项目根加进 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from backend.app.models import User
from backend.tools.mysql_db_tools import aclose_db, get_db_session_maker

# 单批 500 段：一次 get 可能返回上万段（预置 2885 + 上传若干），
# update 按批处理避免单次请求过大。
BATCH = 500


def collect_targets(store, batch_size: int = BATCH) -> list[tuple[str, dict]]:
    """取出「origin=upload 且还没有 user_id」的向量 → [(chroma_id, metadata)]。

    幂等的基础：已经迁过的段（有 user_id）直接跳过，所以重复跑不会重复写。
    """
    got = store._collection.get(where={"origin": "upload"}, include=["metadatas"])
    ids = got["ids"] or []
    metadatas = got["metadatas"] or []
    return [(cid, md) for cid, md in zip(ids, metadatas) if md.get("user_id") is None]


def migrate(store, user_id: int, *, batch_size: int = BATCH, dry_run: bool = False) -> dict:
    """把待迁移向量补上 user_id，返回 {chunks, docs, user_id, written}。

    ⚠ `update` 是**整体替换** metadata，不是合并：必须先读出现有 metadata 再
    `{**old, "user_id": uid}` 传回去。只传 {"user_id": uid} 会把
    doc_id / filename / origin / uploaded_at 全部抹掉 —— 文档立刻变成既查不到、
    也删不掉的孤儿（连 list_upload_docs 都列不出来）。

    ⚠ 只传 ids + metadatas、**不传 documents**：Chroma 据此跳过嵌入，零额度消耗。
    """
    targets = collect_targets(store, batch_size)
    doc_ids = {md.get("doc_id") for _, md in targets}

    if dry_run:
        return {"chunks": len(targets), "docs": len(doc_ids),
                "user_id": user_id, "written": 0}

    written = 0
    for i in range(0, len(targets), batch_size):
        batch = targets[i:i + batch_size]
        store._collection.update(
            ids=[cid for cid, _ in batch],
            metadatas=[{**md, "user_id": user_id} for _, md in batch])
        written += len(batch)

    return {"chunks": len(targets), "docs": len(doc_ids),
            "user_id": user_id, "written": written}


async def resolve_user(username: str | None) -> tuple[int, str]:
    """确定归属用户：显式指定的用户名，或**第一个注册的用户**（spec §9.2 第 5 条）。"""
    maker = get_db_session_maker()
    try:
        async with maker() as db:
            if username:
                row = (await db.execute(
                    select(User.id, User.username).where(User.username == username))).first()
                if row is None:
                    raise SystemExit(f"[ERROR] 用户不存在：{username}")
                return row.id, row.username
            row = (await db.execute(
                select(User.id, User.username).order_by(User.id).limit(1))).first()
            if row is None:
                raise SystemExit(
                    "[ERROR] users 表里一个用户都没有。请先在应用里注册一个账号，再跑迁移。")
            return row.id, row.username
    finally:
        # ⚠ 必须显式释放连接池。aiomysql 的连接**绑定在创建它的那个事件循环**上，
        # 而 asyncio.run() 一返回循环就关了；连接随后在 __del__ 里尝试 close 时会抛
        # `RuntimeError: Event loop is closed` —— 脚本会在一堆 traceback 中结束，
        # 看起来像迁移失败，实际数据已经写好了。这种「成功却报错」最容易被误判。
        await aclose_db()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="给 P3 之前上传的向量补 user_id（一次性）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印将影响多少段向量/文档、归属给谁，不写入")
    ap.add_argument("--yes", action="store_true",
                    help="确认写入。不加这个开关时脚本会拒绝执行")
    ap.add_argument("--user", default=None,
                    help="归属用户名；默认取第一个注册的用户")
    args = ap.parse_args(argv)

    if not args.dry_run and not args.yes:
        # ⚠ 输出一律用 ASCII：中文 Windows 控制台默认 GBK，打印 ✔/✘ 会直接抛
        # UnicodeEncodeError —— 连「拒绝执行」这句提示都打不出来，反而像脚本坏了。
        print("[ABORT] 本脚本会改写现有向量，必须显式确认。")
        print("        先跑 `--dry-run` 核对数量，确认无误后再加 `--yes`。")
        return 2

    user_id, username = asyncio.run(resolve_user(args.user))

    # 延迟导入：get_vectorstore 在 import 期会加载 Chroma + 嵌入模型，
    # 放在参数校验之后，参数写错时不必等它加载完。
    from backend.app.agent.graph import get_vectorstore

    store = get_vectorstore()

    if args.dry_run:
        r = migrate(store, user_id, dry_run=True)
        print(f"[dry-run] 将影响 {r['chunks']} 段向量 / {r['docs']} 个文档 "
              f"→ 归属给 {username}(id={user_id})")
        print("          未写入任何数据。确认无误后加 --yes 执行。")
        return 0

    r = migrate(store, user_id, dry_run=False)
    print(f"[DONE] 已迁移 {r['written']} 段向量 / {r['docs']} 个文档 "
          f"→ {username}(id={user_id})")
    print("       建议再跑一次 --dry-run，应显示 0 段待迁移（验证幂等）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
