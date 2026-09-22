# rag_qa_project/tests/test_ingest_env.py
# -*- coding: utf-8 -*-
"""ingest.py 的「项目根 .env」定位：本地布局与容器布局都不能崩。

背景（2026-09-22 云部署实测）：容器里代码在 /app/ 下，`Path(__file__).resolve()
.parents[2]` 越界抛 IndexError —— 而 get_vectorstore() 会 `from ingest import
build_embeddings`，于是「一提问就炸」；健康检查的 kb_count 也会被静默吞掉
（main.py 的 try/except 只 pass）。本地因为上两层恰好是仓库的再上一层（放 .env
的地方）所以测不出来 —— 这类「只在容器里死」的问题必须有回归线钉住。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ingest  # noqa: E402


def test_container_layout_returns_none_instead_of_index_error():
    """/app/ingest.py（只有两层父目录）→ 返回 None，不抛 IndexError。

    容器里没有 .env（.dockerignore 排除），密钥全部由 compose 环境变量注入，
    因此这里的正确行为是「静默跳过」而不是「崩」。
    """
    assert ingest._repo_env_file(Path("/app/ingest.py")) is None


def test_normal_layout_points_two_levels_up(tmp_path):
    """rag_qa_project/ingest.py（本地开发）→ 仍指向上两层的 .env（行为不变）。"""
    deep = tmp_path / "root" / "proj" / "ingest.py"
    deep.parent.mkdir(parents=True)
    deep.write_text("")
    assert ingest._repo_env_file(deep) == tmp_path / ".env"
