# rag_qa_project/tests/test_deps_manifest.py
# -*- coding: utf-8 -*-
"""依赖清单一致性：pyproject.toml 与 requirements.txt 不许漂移。

为什么需要这条
--------------
本项目有**两份**依赖清单，服务不同入口：
  · `pyproject.toml`      —— 本地开发走 `uv sync`；也是「代码到底需要什么」的声明
  · `requirements.txt`    —— Docker 镜像构建与 CI 的安装来源（Dockerfile 会再 grep 掉
                             两行仅本地嵌入才用的重依赖）

两份各自手工维护，**漂移不报错**，只会在某个环节突然炸。实测（2026-09-23）：
CI 第一次跑就红在

    eval_retrieval.py:56: import jieba
    E   ModuleNotFoundError: No module named 'jieba'

`jieba` 在 pyproject 里明明有，requirements 里却没有 —— 一共漏了 7 项，
其中 `jieba` / `rank-bm25` / `grandalf` 是**真被 import** 的
（rank-bm25 由 `langchain_community.BM25Retriever` 在运行时要求，虽无直接 import 语句）。

这条测试把「两份清单必须一致」变成 CI 上的硬约束 —— 以后漏一项就红，
而不是等到某台机器上跑不起来才发现。
"""
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 允许 requirements.txt 比 pyproject **多**东西（反向不成立）：
#   · sentence-transformers / langchain-huggingface —— 仅在本地实验用，Dockerfile 会 grep 掉
#   · pytest —— 测试依赖，pyproject 放在 dependency-groups.dev 里
# 所以本测试只断言**单向包含**：pyproject 的运行时依赖必须全部出现在 requirements 里。
_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")


def _canonical(spec: str) -> str | None:
    """把一条依赖规格归一成可比较的包名（PEP 503 风格：小写、下划线转连字符）。

    `redis[hiredis]>=8.1.0` → `redis`；`Python_DotEnv>=1.2` → `python-dotenv`。
    取不到名字（空行、注释、`-r other.txt` 之类的指令）返回 None。
    """
    m = _NAME.match(spec.strip())
    return m.group(1).lower().replace("_", "-") if m else None


def _pyproject_runtime_deps() -> dict[str, str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for spec in data["project"]["dependencies"]:
        name = _canonical(spec)
        if name:
            out[name] = spec
    return out


def _requirements_deps() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()          # 去行尾注释
        if not line or line.startswith("-"):
            continue
        name = _canonical(line)
        if name:
            out[name] = line
    return out


def test_pyproject_runtime_deps_are_all_in_requirements():
    """pyproject 声明的每个运行时依赖，都必须能在 requirements.txt 里找到。

    漏了会怎样：CI / Docker 从 requirements.txt 装依赖 → 少装的包在 import 期炸，
    而且**本地永远复现不了**（本地走 uv sync，装的是 pyproject）。这正是 2026-09-23
    CI 首跑失败的原因。
    """
    py, req = _pyproject_runtime_deps(), _requirements_deps()
    missing = sorted(set(py) - set(req))
    assert not missing, (
        "以下依赖在 pyproject.toml 里声明了，但 requirements.txt 里没有 —— "
        "CI 与 Docker 都从 requirements.txt 安装，会缺包：\n  "
        + "\n  ".join(f"{n}  （pyproject: {py[n]}）" for n in missing)
    )


def test_both_manifests_are_parseable_and_non_empty():
    """防止解析逻辑本身失效：两份清单都要能读出合理数量。

    没有这条的话，正则写错导致两边都解析成空集时，上面的包含断言会**静默通过**
    —— 一条永远绿的测试比没有测试更糟。
    """
    py, req = _pyproject_runtime_deps(), _requirements_deps()
    assert len(py) >= 20, f"pyproject 只解析出 {len(py)} 个依赖，解析逻辑可能坏了"
    assert len(req) >= 20, f"requirements 只解析出 {len(req)} 个依赖，解析逻辑可能坏了"
