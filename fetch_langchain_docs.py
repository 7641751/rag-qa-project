#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_langchain_docs.py

抓取 LangChain / LangGraph (Python) 官方文档并清洗为纯净 Markdown，
落盘到 data/langchain_docs/ 供本项目 RAG 知识库使用。

数据来源：官方 docs.langchain.com 的分区索引 (llms.txt) 与每页的 .md 版本。
- 分区索引: https://docs.langchain.com/oss/python/<section>/llms.txt
- 单页正文: https://docs.langchain.com/<path>.md

特性：
- 自动发现全部页面（无需硬编码清单），日后可重跑以更新知识库
- 轻度清洗：去除 MDX/JSX 组件、HTML 标签、<a id> 锚点、图片、页脚 source-links 等噪声
- 保护代码块：清洗只作用于正文，代码围栏内容原样保留
- 并发下载 + 失败重试，生成 INDEX.md 索引（标题 / 源链接 / 抓取日期）

用法：
    python fetch_langchain_docs.py
"""

from __future__ import annotations

import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
BASE = "https://docs.langchain.com"

# 需要抓取的分区（Python 核心）
SECTIONS = [
    "/oss/python/langchain",
    "/oss/python/langgraph",
    "/oss/python/concepts",
]

# 输出目录：脚本所在目录下的 data/langchain_docs
OUT_DIR = Path(__file__).resolve().parent / "data" / "langchain_docs"

# 路径包含以下任一子串的页面将被跳过（对 Python RAG 后端价值低 / 噪声高）
DENY = [
    "/frontend/",            # 生成式 UI / 前端集成
    "changelog",             # 变更日志
    "/academy",              # 课程导流页
    "/get-help",             # 求助页
    "/studio",               # Studio 图形工具
    "/ui.md",                # 生成式 UI
    "/case-studies",         # 案例研究
    "/backward-compatibility",  # 兼容/迁移说明
]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; rag-kb-builder/1.0)"}
TIMEOUT = 40
MAX_WORKERS = 6
RETRIES = 3
DELAY = 0.1  # 每个请求前的礼貌性延时（秒）


# --------------------------------------------------------------------------- #
# 网络
# --------------------------------------------------------------------------- #
def http_get(url: str) -> str:
    """带重试的 GET，返回解码后的文本。"""
    last_err: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            time.sleep(DELAY)
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            last_err = exc
            wait = attempt * 1.5
            print(f"  [retry {attempt}/{RETRIES}] {url} -> {exc}; 等待 {wait:.1f}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"下载失败: {url} ({last_err})")


def discover_pages() -> list[tuple[str, str, str]]:
    """从各分区 llms.txt 解析 (title, url, section) 列表，去重并应用 DENY 过滤。"""
    entry_re = re.compile(r"-\s*\[([^\]]+)\]\((https://docs\.langchain\.com/[^\s)]+\.md)\)")
    seen: set[str] = set()
    pages: list[tuple[str, str, str]] = []

    for section in SECTIONS:
        index_url = f"{BASE}{section}/llms.txt"
        print(f"[discover] {index_url}")
        text = http_get(index_url)
        for title, url in entry_re.findall(text):
            if url in seen:
                continue
            path = url[len(BASE):]  # 例如 /oss/python/langgraph/persistence.md
            if any(d in path for d in DENY):
                continue
            seen.add(url)
            pages.append((title.strip(), url, section))

    return pages


# --------------------------------------------------------------------------- #
# 清洗
# --------------------------------------------------------------------------- #
# 需要剥离的 HTML / MDX 组件标签名
_TAGS = [
    "div", "span", "img", "a", "br", "hr", "frame", "icon", "video", "iframe",
    "embed", "details", "summary", "sup", "sub", "kbd", "figure", "figcaption",
    "picture", "source", "Note", "Warning", "Info", "Tip", "Check", "X",
    "Callout", "Tabs", "Tab", "Accordion", "AccordionGroup", "Steps", "Step",
    "Card", "CardGroup", "Prompt", "Params", "Param", "ResponseField",
    "CodeGroup", "Expandable", "Properties", "Property", "RequestExample",
    "ResponseExample", "Dropdown", "Button", "Badge", "Tooltip", "Mermaid",
    "Heading", "Columns", "Column",
]
_TAGNAME = "|".join(_TAGS)
_OPEN_TAG_RE = re.compile(rf"<(?:{_TAGNAME})\b[^>]*/?>", re.IGNORECASE)
_CLOSE_TAG_RE = re.compile(rf"</(?:{_TAGNAME})\s*>", re.IGNORECASE)
_TITLE_TAG_RE = re.compile(r'<(?:Tab|Step|Accordion)\s+title="([^"]*)"[^>]*>', re.IGNORECASE)
_CARD_RE = re.compile(r"<Card\s+([^>]*?)/?>", re.IGNORECASE)
_HTML_COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MDX_IMPORT_RE = re.compile(r"(?m)^\s*import\s+[\s\S]*?from\s*['\"][^'\"]+['\"]\s*$\n?")
_MDX_EXPORT_RE = re.compile(r"(?m)^\s*export\s+const\s+.*$\n?")
_LEADING_NOTE_RE = re.compile(r"^(?:\s*>[^\n]*\n)+")
_CODE_FENCE_RE = re.compile(r"(```.*?```)", re.DOTALL)
# 仅保留围栏首行的 ``` + 语言标识，剥离 theme={...}（含嵌套花括号）等属性
_FENCE_OPEN_RE = re.compile(r"^(```[^\s]*)\s+.*$")

_DOC_INDEX_HINTS = ("Documentation Index", "llms.txt")


def _card_repl(match: re.Match) -> str:
    attrs = match.group(1)
    title = re.search(r'title="([^"]*)"', attrs)
    href = re.search(r'href="([^"]*)"', attrs)
    t = title.group(1) if title else ""
    h = href.group(1) if href else ""
    if t and h:
        return f"- [{t}]({h})"
    if t:
        return f"- {t}"
    return ""


def _clean_non_code(part: str) -> str:
    """清洗正文（非代码块）部分。"""
    part = _HTML_COMMENT_RE.sub("", part)
    part = _MD_IMAGE_RE.sub("", part)
    part = _MDX_IMPORT_RE.sub("", part)
    part = _MDX_EXPORT_RE.sub("", part)
    part = _TITLE_TAG_RE.sub(r"\n**\1**\n", part)
    part = _CARD_RE.sub(_card_repl, part)
    part = _OPEN_TAG_RE.sub("", part)
    part = _CLOSE_TAG_RE.sub("", part)
    return part


def clean_markdown(raw: str, url: str, section: str) -> str:
    """把官方 .md 原文清洗为适合 RAG 的纯净 Markdown。"""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")

    # 1) 去掉开头的 "Documentation Index" 提示块（位于首个标题之前的引用块）
    lead = _LEADING_NOTE_RE.match(text)
    if lead and any(h in lead.group(0) for h in _DOC_INDEX_HINTS):
        text = text[lead.end():]

    # 2) 截断页脚 source-links / "Connect these docs" / "Edit this page" 区块
    cut = text.find('<div className="source-links"')
    if cut == -1:
        marker = text.find("Connect these docs")
        if marker != -1:
            sep = text.rfind("***", 0, marker)
            cut = sep if (sep != -1 and marker - sep < 400) else marker
    if cut != -1:
        text = text[:cut]
    text = re.sub(r"\n\*{3,}\s*$", "", text.rstrip())

    # 3) 保护代码块，仅清洗正文
    pieces = _CODE_FENCE_RE.split(text)
    for i, piece in enumerate(pieces):
        if piece.startswith("```"):
            nl = piece.find("\n")
            if nl != -1:
                pieces[i] = _FENCE_OPEN_RE.sub(r"\1", piece[:nl]) + piece[nl:]
        else:
            pieces[i] = _clean_non_code(piece)
    text = "".join(pieces)

    # 4) 规整空白
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip() + "\n"

    # 5) 顶部加入来源信息（供 RAG 引用溯源）
    header = f"<!-- source: {url} | section: {section} | fetched: {date.today().isoformat()} -->\n\n"
    return header + text


# --------------------------------------------------------------------------- #
# 落盘
# --------------------------------------------------------------------------- #
def local_path_for(url: str) -> Path:
    """把 URL 映射到本地相对路径：/oss/python/<section>/... -> <section>/..."""
    path = url[len(BASE):].lstrip("/")          # oss/python/langgraph/persistence.md
    prefix = "oss/python/"
    if path.startswith(prefix):
        path = path[len(prefix):]               # langgraph/persistence.md
    return OUT_DIR / path


def process_page(title: str, url: str, section: str) -> tuple[str, Path, int]:
    raw = http_get(url)
    cleaned = clean_markdown(raw, url, section)
    dest = local_path_for(url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(cleaned, encoding="utf-8", newline="\n")
    return title, dest, len(cleaned.encode("utf-8"))


def write_index(results: list[tuple[str, Path, int, str]]) -> None:
    """生成 INDEX.md 索引。"""
    lines = [
        "# LangChain / LangGraph (Python) 文档知识库索引",
        "",
        f"> 来源: {BASE}  |  抓取日期: {date.today().isoformat()}  |  页面数: {len(results)}",
        "> 由 fetch_langchain_docs.py 自动抓取并清洗，可重跑该脚本以更新。",
        "",
    ]
    # 按相对路径分组排序
    for _title, dest, size, url in sorted(results, key=lambda r: r[1].as_posix()):
        rel = dest.relative_to(OUT_DIR).as_posix()
        lines.append(f"- [{_title}]({rel}) — {size / 1024:.1f} KB · [原文]({url})")
    lines.append("")
    (OUT_DIR / "INDEX.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")


def main() -> int:
    print(f"输出目录: {OUT_DIR}")
    pages = discover_pages()
    print(f"发现 {len(pages)} 个待抓取页面（已过滤 {len(DENY)} 类噪声路径）\n")
    if not pages:
        print("未发现任何页面，请检查网络或分区索引。", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    results: list[tuple[str, Path, int, str]] = []
    failures: list[tuple[str, str]] = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        future_map = {
            pool.submit(process_page, title, url, section): (title, url)
            for title, url, section in pages
        }
        for fut in as_completed(future_map):
            title, url = future_map[fut]
            try:
                t, dest, size = fut.result()
                results.append((t, dest, size, url))
                print(f"  [ok] {dest.relative_to(OUT_DIR).as_posix()}  ({size / 1024:.1f} KB)")
            except Exception as exc:  # noqa: BLE001
                failures.append((url, str(exc)))
                print(f"  [FAIL] {url} -> {exc}", file=sys.stderr)

    write_index(results)

    total_kb = sum(r[2] for r in results) / 1024
    print("\n" + "=" * 60)
    print(f"完成：成功 {len(results)} 篇，失败 {len(failures)} 篇，总计 {total_kb:.1f} KB")
    print(f"索引：{OUT_DIR / 'INDEX.md'}")
    if failures:
        print("\n失败清单：")
        for url, err in failures:
            print(f"  - {url}: {err}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
