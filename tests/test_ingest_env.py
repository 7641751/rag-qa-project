# rag_qa_project/tests/test_ingest_env.py
# -*- coding: utf-8 -*-
""".env 定位：单一真相源在 `config._find_env_file()`，各入口不得各写一份。

本文件的历史
------------
最初它测的是 `ingest._repo_env_file(src)` —— 一个写死「上两层」的定位器。
那是 2026-09-22 云部署事故的产物：容器里代码在 /app 下（只有两层父目录），
`src.parents[2]` 越界 IndexError，而 `get_vectorstore()` 会 `from ingest import
build_embeddings`，于是「一提问就炸」；健康检查的 kb_count 又被 main.py 的
try/except 静默吞掉，探活仍返回 ok，反而掩盖问题。

但那个修法**只解决了容器**：项目抽成独立仓库后 `PROJECT_DIR` 就是仓库根，
`parents[2]` 会指到仓库外面去（可能读到无关的 .env）。于是同一件事出现了两份实现：
`config._find_env_file()`（四级回退 + RAGQA_ENV_FILE 逃生舱）与
`ingest._repo_env_file()`（写死两层）—— 两份迟早漂移。

现在统一到 config 一处，`ingest` 直接 `from config import ENV_FILE`。
本文件因此改为测 **config 的定位器**，并额外钉住「ingest 不得再有自己的实现」。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
import ingest  # noqa: E402


# ============================ 1. 容器布局（原事故的回归线） ============================
def test_shallow_path_does_not_crash():
    """容器布局（/app/config.py，父目录层级不足）→ 返回 None，**不得抛 IndexError**。

    `/app/` 只有两层父目录，而候选里最外层要 `parent.parent`。
    pathlib 在根目录上 `.parent` 会返回自身（不像 `parents[2]` 那样越界），
    所以这里既不会崩、也找不到 .env → 返回 None，交由进程环境变量接管
    （容器里密钥全部由 compose 注入，.dockerignore 也排除了 .env）。
    """
    assert config._find_env_file(Path("/app/config.py")) is None


# ============================ 2. 三种真实布局 ============================
def test_monorepo_layout_prefers_outer(tmp_path):
    """monorepo 布局：外层那份（开发用）优先，即使项目根也有一份。

    ⚠ 这条钉的是**踩过的坑**：曾经想改成「就近优先」，那会让本地开发读到
    `rag_qa_project/.env`（docker compose 用的那份，只有 MYSQL_PASSWORD 之类的容器变量），
    少了 `RAGQA_MYSQL_DATABASE_URL` → 构造引擎时快速失败。
    """
    proj = tmp_path / "repo" / "advanced_tutorial" / "rag_qa_project"
    proj.mkdir(parents=True)
    src = proj / "config.py"
    src.write_text("")
    (tmp_path / "repo" / ".env").write_text("OUTER=1")     # 外层开发配置
    (proj / ".env").write_text("INNER=1")                  # docker compose 那份

    assert config._find_env_file(src) == tmp_path / "repo" / ".env"


def test_standalone_layout_falls_back_to_project_root(tmp_path):
    """独立仓库布局：上两级都没有 .env 时，回退到项目根那份。

    这是「抽成独立仓库」后的正常形态 —— 也是写死 parents[2] 会失效的场景。
    """
    proj = tmp_path / "repos" / "rag_qa_project"
    proj.mkdir(parents=True)
    src = proj / "config.py"
    src.write_text("")
    (proj / ".env").write_text("ROOT=1")

    assert config._find_env_file(src) == proj / ".env"


def test_middle_layer_is_used_when_outer_missing(tmp_path):
    """中间层（项目根的上一级）也能命中 —— 四级回退里的第 ③ 档。"""
    mid = tmp_path / "workspace"
    proj = mid / "rag_qa_project"
    proj.mkdir(parents=True)
    src = proj / "config.py"
    src.write_text("")
    (mid / ".env").write_text("MID=1")

    assert config._find_env_file(src) == mid / ".env"


# ============================ 3. 逃生舱与空结果 ============================
def test_explicit_override_wins(tmp_path, monkeypatch):
    """RAGQA_ENV_FILE 优先级最高，且**不校验存在性**（由调用方自负）。"""
    proj = tmp_path / "p"
    proj.mkdir()
    src = proj / "config.py"
    src.write_text("")
    (proj / ".env").write_text("ROOT=1")
    explicit = tmp_path / "custom.env"

    monkeypatch.setenv("RAGQA_ENV_FILE", str(explicit))
    assert config._find_env_file(src) == explicit


def test_returns_none_when_nothing_found(tmp_path):
    """四处都找不到 → None（此时全靠进程环境变量，不报错）。"""
    proj = tmp_path / "isolated" / "rag_qa_project"
    proj.mkdir(parents=True)
    src = proj / "config.py"
    src.write_text("")

    assert config._find_env_file(src) is None


# ============================ 4. 统一性（本文件存在的另一半理由） ============================
def test_ingest_has_no_duplicate_locator():
    """ingest 不得再自带一套 .env 定位实现 —— 两份实现迟早漂移。

    统一后 ingest 只是 `from config import ENV_FILE`，由 config 在 import 期
    load_dotenv 一次，所有入口共享同一份。
    """
    assert not hasattr(ingest, "_repo_env_file"), \
        "ingest 不应再有自己的 .env 定位器（已统一到 config._find_env_file）"
    assert not hasattr(ingest, "_repo_env"), "ingest 不应再持有自己的 .env 路径"


def test_ingest_and_config_agree_on_env_file():
    """ingest 与 config 必须指向**同一个** .env 对象。"""
    assert ingest.ENV_FILE is config.ENV_FILE


@pytest.mark.parametrize("module_name", ["ingest", "run"])
def test_entrypoints_reuse_config_locator(module_name):
    """入口模块要么复用 config 的定位器，要么干脆不自己定位 —— 不许各写一份。"""
    mod = __import__(module_name)
    assert not hasattr(mod, "_repo_env_file")
