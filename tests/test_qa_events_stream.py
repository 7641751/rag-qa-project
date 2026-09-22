# rag_qa_project/tests/test_qa_events_stream.py
# -*- coding: utf-8 -*-
"""问答事件的产生：字段完整、失败不影响 SSE 流。全离线（复用 test_chat_stream 的假图）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_chat_stream import _run_stream_with_vs      # 同层 import，与 test_graph 的用法一致


class FakeRedis:
    """只实现生产端用到的 xadd；记录 (流名, 字段, maxlen)。"""

    def __init__(self, xadd_exc=None):
        self.calls = []
        self._exc = xadd_exc

    async def xadd(self, name, fields, maxlen=None, approximate=True):
        self.calls.append((name, dict(fields), maxlen))
        if self._exc:
            raise self._exc
        return "1-0"


def test_qa_event_fields_are_complete(monkeypatch):
    redis = FakeRedis()

    frames, _vs = _run_stream_with_vs(monkeypatch, relevance_mode="all",
                                      user_id=7, redis=redis, started_at=0.0)

    assert [e for e, _ in frames][-1] == "done", "事件在 done 之后才发，不能打乱帧序"

    assert len(redis.calls) == 1, "一轮问答恰好一条事件"
    stream_key, fields, maxlen = redis.calls[0]
    assert stream_key == "ragqa:stream:qa_stats"
    assert fields["user_id"] == "7"
    assert fields["thread_id"] == "t-stream"        # 由 _run_stream_with_vs 固定
    assert fields["grounded"] == "1"                # relevance_mode="all" ⇒ 有资料
    assert int(fields["latency_ms"]) > 0            # started_at=0.0 ⇒ 耗时必为正
    assert len(fields["event_id"]) == 36            # uuid4 字符串长度
    assert maxlen == 10000, "必须带 MAXLEN，否则流会无限增长"


def test_xadd_failure_does_not_break_stream(monkeypatch):
    redis = FakeRedis(xadd_exc=RuntimeError("stream down"))

    frames, _vs = _run_stream_with_vs(monkeypatch, relevance_mode="all",
                                      user_id=7, redis=redis, started_at=0.0)

    assert [e for e, _ in frames][-1] == "done", "XADD 失败绝不能影响 SSE 流（统计是可丢的）"
    assert not [d for e, d in frames if e == "error"]


def test_no_redis_means_no_event_and_no_error(monkeypatch):
    """redis=None（未配置 / 开关关闭）：不产生事件，也不报错。"""
    frames, _vs = _run_stream_with_vs(monkeypatch, relevance_mode="all", user_id=7)

    assert [e for e, _ in frames][-1] == "done"
