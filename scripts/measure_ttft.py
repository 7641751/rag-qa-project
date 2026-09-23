# -*- coding: utf-8 -*-
"""首 token 延迟（TTFT）实测：把「体感快慢」变成一个可复现的数字。

为什么需要它
------------
README 的「检索评估」章节量的是**检索**的 p50/p95，但用户体感的其实是**首 token 延迟** ——
从点下发送到第一段文字出现，中间夹着：检索 → grade →（可能）重写 → 生成的首个 token。
换检索策略、放宽 grade 判定、加大 top_k 都会动这个数，所以**优化前后必须用它回归**。

跑法（项目根目录，服务已在跑）：
    python scripts/measure_ttft.py                          # 默认 127.0.0.1:8000，跑内置查询集
    python scripts/measure_ttft.py --n 5                    # 每条查询重复 5 次取分位
    python scripts/measure_ttft.py --base-url http://<公网IP>:8080   # 量真实公网延迟
    python scripts/measure_ttft.py --question "缓存雪崩怎么预防"

产出：stdout 报表 + `data/ttft_results.json`（机器可读，便于优化前后对比）。

口径说明（重要，否则数字没有可比性）
------------------------------------
· TTFT 从**连接建立后发出请求体**开始计时，到收到第一帧 `event: token` 为止。
  它**包含**检索与 grade 的全部耗时 —— 那正是用户等待的部分。
  别用「服务端内部计时」替代它，那样会把网络往返和 SSE 首帧缓冲漏掉。
· 首次请求会额外摊上冷启动（Chroma 客户端 + 嵌入实例 + BM25…），
  所以默认**先跑一次预热**再开始统计；`--no-warmup` 可关掉以观察冷启动代价。
· 每条查询用**独立的 thread_id**：复用会话会带上历史消息，prompt 变长、TTFT 不可比。
"""
import argparse
import json
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 内置查询集：覆盖两类 —— 预置英文官方文档（term_en）与中文语义提问（semantic_zh），
# 与 data/eval_queries.json 的口径一致但不依赖它（那个是**检索**评测集，这里量端到端）。
DEFAULT_QUESTIONS = [
    ("term_en", "How to configure check_same_thread when using SqliteSaver with FastAPI"),
    ("term_en", "stream_mode updates versus messages difference"),
    ("semantic_zh", "怎么让智能体记住用户的长期偏好"),
    ("semantic_zh", "程序崩溃后怎么从上次中断的地方继续跑"),
    ("semantic_zh", "怎么把逐字生成的内容实时推给前端"),
]

USERNAME = "ttft_probe"
PASSWORD = "ttft-probe-pw-2026"     # ≥8 位，满足契约

# ⚠ 必须显式禁用代理：urllib 默认读 HTTP_PROXY / http_proxy 环境变量，
# 本机装了系统代理时，连 127.0.0.1 的请求也会被送到代理 —— 实测表现为
# 「register 返回 502」（代理发的，不是后端发的），而 Invoke-WebRequest / curl
# 因为走了别的路径而正常，极易误判成后端故障。量公网服务器时同理。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _post_json(url: str, payload: dict, token: str | None = None,
               timeout: float = 30.0):
    """普通 JSON 请求。返回 (status, body_text)。"""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def ensure_token(base_url: str) -> str:
    """注册并登录探针账号，返回 access_token。

    重复跑时注册会 409 USERNAME_TAKEN —— 那是**预期**的，直接转登录即可，
    不要在这里中断（脚本要能反复跑）。
    """
    payload = {"username": USERNAME, "password": PASSWORD}
    status, body = _post_json(f"{base_url}/api/auth/register", payload)
    if status not in (201, 409):
        raise RuntimeError(f"注册失败 [{status}] {body[:300]}")
    status, body = _post_json(f"{base_url}/api/auth/login", payload)
    if status != 200:
        raise RuntimeError(f"登录失败 [{status}] {body[:300]}")
    return json.loads(body)["access_token"]


def measure_once(base_url: str, token: str, question: str, timeout: float = 90.0) -> dict:
    """发一次问答，返回各阶段时间戳与事件统计。

    ⚠ 手工按 `\\n\\n` 切帧：SSE 的帧可能被 TCP 粘包/半包，
    httpx/urllib 的 iter_lines 会按**行**给，所以自己按空行聚合。
    """
    thread_id = str(uuid.uuid4())
    payload = json.dumps({"question": question, "thread_id": thread_id}).encode("utf-8")
    req = urllib.request.Request(f"{base_url}/api/chat/stream", data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "text/event-stream")
    req.add_header("Authorization", f"Bearer {token}")

    t0 = time.perf_counter()
    ttft = None                  # 首个 token 帧
    t_first_event = None         # 首个任意事件（能看到「检索」步骤出现的时刻）
    events: list[str] = []
    tokens = 0
    text_len = 0
    grounded = None
    rewrites = None

    total_bytes = 0
    raw_head = ""
    with _OPENER.open(req, timeout=timeout) as resp:
        buf = ""
        for raw in resp:
            total_bytes += len(raw)
            if len(raw_head) < 400:
                raw_head += raw.decode("utf-8", errors="replace")
            buf += raw.decode("utf-8", errors="replace")
            while "\n\n" in buf:
                frame, buf = buf.split("\n\n", 1)
                event, data = None, ""
                for line in frame.splitlines():
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data += line[5:].strip()
                if not event:
                    continue
                if t_first_event is None:
                    t_first_event = time.perf_counter() - t0
                events.append(event)
                if event == "token":
                    if ttft is None:
                        ttft = time.perf_counter() - t0
                    tokens += 1
                    try:
                        text_len += len(json.loads(data).get("text", ""))
                    except json.JSONDecodeError:
                        pass
                elif event == "done":
                    try:
                        d = json.loads(data)
                        grounded, rewrites = d.get("grounded"), d.get("rewrites")
                    except json.JSONDecodeError:
                        pass
    total = time.perf_counter() - t0
    return {
        "thread_id": thread_id,
        "ttft_s": None if ttft is None else round(ttft, 3),
        "first_event_s": None if t_first_event is None else round(t_first_event, 3),
        "total_s": round(total, 3),
        "tokens": tokens,
        "answer_chars": text_len,
        "grounded": grounded,
        "rewrites": rewrites,
        "event_sequence": events,
        # 诊断用：HTTP 200 但零事件时，靠这两项判断「服务端没发」还是「解析没认出来」
        "bytes": total_bytes,
        "raw_head": "" if events else raw_head.strip()[:400],
    }


def spawn_server(py: str, port: int, timeout_s: float = 90.0,
                 log_path: Path | None = None) -> subprocess.Popen:
    """自己拉起 uvicorn 并等它就绪，返回 Popen（由调用方负责收尾）。

    为什么要这个开关：手工开一个终端起服务、再开另一个终端跑本脚本，
    在 CI / 自动化里很别扭，而且**服务没起来时脚本只会报「连接被拒」**（把
    「服务没起」误报成「服务挂了」）。自己拉起就能做到「一条命令 → 一个数字」。

    注意 cwd 必须是项目根：后端的 import 走 `backend.app.*` 绝对路径，
    sys.path[0] 由启动方式决定（见 README 开发约定 §1）。
    """
    log = open(log_path, "w", encoding="utf-8") if log_path else subprocess.DEVNULL
    proc = subprocess.Popen(
        [py, "-m", "uvicorn", "backend.app.api.main:app",
         "--port", str(port), "--host", "127.0.0.1"],
        cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT)
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"后端启动即退出（exit={proc.returncode}），日志见 {log_path}")
        try:
            with _OPENER.open(f"http://127.0.0.1:{port}/api/health", timeout=2):
                return proc                       # 探活通了才算就绪
        except Exception:                          # noqa: BLE001  未就绪就继续等
            time.sleep(0.5)
    proc.terminate()
    raise RuntimeError(f"后端 {timeout_s:.0f}s 内未就绪，日志见 {log_path}")


def pct(values: list[float], q: float) -> float:
    """分位数（与 eval_retrieval.py 同样的「向上取整下标」口径，便于两份报表交叉印证）。"""
    if not values:
        return 0.0
    s = sorted(values)
    return s[max(0, min(len(s) - 1, int(len(s) * q + 0.9999) - 1))]


def main() -> int:
    ap = argparse.ArgumentParser(description="首 token 延迟（TTFT）实测")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000",
                    help="服务地址；量公网就传 http://<IP>:8080")
    ap.add_argument("--n", type=int, default=1, help="每条查询重复次数（默认 1）")
    ap.add_argument("--question", default="", help="只测这一条（默认跑内置查询集）")
    ap.add_argument("--no-warmup", action="store_true", help="跳过预热，观察冷启动代价")
    ap.add_argument("--timeout", type=float, default=90.0, help="单次请求超时秒数")
    ap.add_argument("--out", default=str(ROOT / "data" / "ttft_results.json"))
    ap.add_argument("--spawn-server", action="store_true",
                    help="自己拉起后端再测（一条命令出一个数字；服务未起时用它）")
    ap.add_argument("--port", type=int, default=8000, help="--spawn-server 用的端口")
    args = ap.parse_args()

    server: subprocess.Popen | None = None
    if args.spawn_server:
        # 自己拉后端时把 base-url 一并对齐，避免 --spawn-server 配错端口却不生效
        base = f"http://127.0.0.1:{args.port}"
        print(f"正在拉起后端（端口 {args.port}）…")
        try:
            server = spawn_server(sys.executable, args.port,
                                  log_path=ROOT / "data" / "_ttft_server.log")
        except Exception as exc:                          # noqa: BLE001
            print(f"[致命] {exc}")
            return 2
        print("后端就绪。")
    else:
        base = args.base_url.rstrip("/")

    try:
        return _run(args, base)
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
            print("\n（已关闭本次拉起的后端）")


def _run(args, base: str) -> int:
    """测量主流程。单独成函数，好让 main() 用 try/finally 保证 --spawn-server 一定被关掉。"""
    print("=" * 78)
    print(f"首 token 延迟实测　目标：{base}")
    print("=" * 78)

    try:
        token = ensure_token(base)
    except Exception as exc:                              # noqa: BLE001
        print(f"[致命] 无法获取 token：{exc}")
        print("       ① 服务在跑吗？② --base-url 对吗？③ 数据库可用吗？")
        return 2
    print(f"探针账号：{USERNAME}（复用；首次运行会自动注册）")

    if not args.no_warmup:
        # 预热：把 Chroma 客户端 / 嵌入实例 / lru_cache 单例都建起来。
        # 不预热的话第一条必然包含冷启动，分位数会被单条样本带偏。
        t = time.perf_counter()
        try:
            measure_once(base, token, "warmup", args.timeout)
        except Exception as exc:                          # noqa: BLE001
            print(f"[致命] 预热失败：{exc}")
            return 2
        print(f"预热完成（{time.perf_counter() - t:.1f}s，不计入统计）")

    questions = ([(("single"), args.question)] if args.question else DEFAULT_QUESTIONS)
    rows: list[dict] = []
    print()
    print(f"{'类型':<12s}{'#':>3s}  {'TTFT':>8s}{'首事件':>9s}{'总耗时':>9s}"
          f"{'token':>7s}{'字符':>6s}{'重写':>5s}{'有依据':>7s}  问题")
    for qtype, q in questions:
        for i in range(1, args.n + 1):
            try:
                r = measure_once(base, token, q, args.timeout)
            except Exception as exc:                      # noqa: BLE001
                print(f"{qtype:<12s}{i:>3d}  ERROR {type(exc).__name__}: {exc}")
                continue
            r["type"], r["question"] = qtype, q
            rows.append(r)
            # ⚠ 两个时间都可能为 None：请求成功（HTTP 200）但一帧事件都没收到时
            # 就属于这种 —— 直接格式化会 TypeError 把整个报表打挂。
            # 那种情况本身就是要报出来的异常（帧数 0 说明事件流断了），不能静默。
            ttft = "—" if r["ttft_s"] is None else f"{r['ttft_s']:.2f}s"
            first = "—" if r["first_event_s"] is None else f"{r['first_event_s']:.2f}s"
            print(f"{qtype:<12s}{i:>3d}  {ttft:>8s}{first:>9s}"
                  f"{r['total_s']:>8.2f}s{r['tokens']:>7d}{r['answer_chars']:>6d}"
                  f"{str(r['rewrites']):>5s}{str(r['grounded']):>7s}  {q[:28]}")
            if not r["event_sequence"]:
                # 200 却零事件 = 事件流没建立起来，属于要报出来的异常，不能混进统计里当成功
                print(f"      ⚠ HTTP 200 但零事件帧（收到 {r['bytes']} 字节）"
                      f"　原始内容：{r['raw_head'][:200]!r}")

    if not rows:
        print("\n没有采集到任何成功样本。")
        return 3

    # 「成功」= 至少收到过一帧事件。零事件的行仍留在明细里（要看得见），但不进分位数，
    # 否则失败样本会以「很快」的姿态混进统计、把延迟报低。
    ok = [r for r in rows if r["event_sequence"]]
    failed = len(rows) - len(ok)
    ttfts = [r["ttft_s"] for r in ok if r["ttft_s"] is not None]
    firsts = [r["first_event_s"] for r in ok if r["first_event_s"] is not None]
    totals = [r["total_s"] for r in ok]

    print()
    print("=" * 78)
    print(f"汇总（成功 {len(ok)} 次"
          + (f"，零事件帧 {failed} 次 —— 已排除在分位数外，请单独排查" if failed else "")
          + "）")
    print("=" * 78)
    if not ok:
        print("  没有一次成功调用，无法统计。先看上面每行的「原始内容」诊断。")
        return 3
    if ttfts:
        print(f"  首 token 延迟 TTFT　p50 = {statistics.median(ttfts):.2f}s　"
              f"p95 = {pct(ttfts, 0.95):.2f}s　min = {min(ttfts):.2f}s　max = {max(ttfts):.2f}s")
    else:
        print("  ⚠ 一次都没收到 token 帧 —— 检查是否走了兜底分支（见 README 已知问题 ①）")
    print(f"  首事件延迟　　　　　p50 = {statistics.median(firsts):.2f}s　"
          f"（= 第一个 step 帧，即「检索」开始可见的时刻）")
    print(f"  端到端总耗时　　　　p50 = {statistics.median(totals):.2f}s　"
          f"p95 = {pct(totals, 0.95):.2f}s")
    fallback = sum(1 for r in rows if r["grounded"] is False)
    print(f"  兜底率（grounded=false）　{fallback}/{len(rows)}"
          f"　← 这个数才是「答不出来」的真实指标，比 recall 诚实")

    out = {
        "meta": {"base_url": base, "n": args.n, "samples": len(rows),
                 "warmup": not args.no_warmup, "at": time.strftime("%Y-%m-%dT%H:%M:%S")},
        "summary": {
            "ttft_p50_s": round(statistics.median(ttfts), 3) if ttfts else None,
            "ttft_p95_s": round(pct(ttfts, 0.95), 3) if ttfts else None,
            "total_p50_s": round(statistics.median(totals), 3),
            "fallback_rate": round(fallback / len(rows), 3),
        },
        "samples": rows,
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入 {args.out}")
    print("→ 优化检索/评分后重跑本脚本，对比 summary 即可给出「优化前后」的数字（README 用它）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
