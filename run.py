# -*- coding: utf-8 -*-
"""rag_qa_project 命令行入口。

用法（在 rag_qa_project 目录下）：

"""
import argparse
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# 加载项目根 .env（DEEPSEEK_API_KEY 等）
ROOT = Path(__file__).resolve().parents[2]
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")


def ask(question: str, mode: str = "normal"):
    from backend.app.agent.graph import build_graph

    app = build_graph()
    state = {"question": question, "documents": [], "generation": "",
             "rewrites": 0}

    if mode == "stream":
        # 流式：LLM token 实时输出（updates + messages 双模式）
        print("回答: ", end="", flush=True)
        for chunk_mode, chunk in app.stream(
                state, config={"configurable": {"thread_id": "cli"}, "recursion_limit": 20},
                stream_mode=["updates", "messages"]):
            if chunk_mode == "messages":
                msg, _meta = chunk
                content = getattr(msg, "content", None)
                if content and not getattr(msg, "tool_calls", None):
                    print(content, end="", flush=True)
        print()
        return None

    if mode == "debug":
        # 调试：每个节点执行完打印状态增量
        for update in app.stream(state, config={"configurable": {"thread_id": "cli"}, "recursion_limit": 20},
                                 stream_mode="updates"):
            for node, delta in update.items():
                if node == "__end__":
                    continue
                print(f"\n== [{node}] ==")
                for k, v in delta.items():
                    summary = (f"{len(v)} 段文档" if k == "documents"
                               else str(v)[:80])
                    print(f"   {k}: {summary}")
        return None

    result = app.invoke(state, config={"configurable": {"thread_id": "cli"}, "recursion_limit": 20})
    return result["generation"]


def main():
    parser = argparse.ArgumentParser(description="RAG 智能问答")
    parser.add_argument("question", nargs="?", help="要提问的问题")
    parser.add_argument("--stream", action="store_true", help="流式输出")
    parser.add_argument("--debug", action="store_true", help="打印每步状态更新")
    parser.add_argument("--graph", action="store_true", help="打印工作流结构图")
    args = parser.parse_args()

    if args.graph:
        from backend.app.agent.graph import print_ascii_graph
        print_ascii_graph()
        return

    if not args.question:
        parser.print_help()
        return

    mode = "stream" if args.stream else ("debug" if args.debug else "normal")
    answer = ask(args.question, mode)
    if answer is not None:
        print("\n回答:")
        print(answer)


if __name__ == "__main__":
    main()
