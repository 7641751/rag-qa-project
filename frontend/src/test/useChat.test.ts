import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

const { streamChatMock, fetchHistoryMock, deleteThreadMock } = vi.hoisted(() => ({
  streamChatMock: vi.fn(), fetchHistoryMock: vi.fn(), deleteThreadMock: vi.fn(),
}));
vi.mock('../api/client', () => ({
  streamChat: streamChatMock, fetchHistory: fetchHistoryMock, deleteThread: deleteThreadMock,
}));

import { useChat } from '../hooks/useChat';

beforeEach(() => {
  localStorage.clear();
  streamChatMock.mockReset();
  fetchHistoryMock.mockReset();
  deleteThreadMock.mockReset();
  fetchHistoryMock.mockResolvedValue({ thread_id: 't', messages: [] });
  deleteThreadMock.mockResolvedValue({ thread_id: 't', deleted: true });
});

describe('useChat', () => {
  it('send 压入 user+assistant 并按事件增量更新', async () => {
    streamChatMock.mockImplementation(async (_b: unknown, h: any) => {
      h.onStep({ node: 'retrieve', label: '检索' });
      h.onToken({ text: '你' }); h.onToken({ text: '好' });
      h.onSources({ sources: [{ title: 'a.md' }] });
      h.onDone({ thread_id: 't', rewrites: 0, grounded: true })
    });
    const { result } = renderHook(() => useChat());
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
    await act(async () => { result.current.send('问题'); });
    const m = result.current.messages;
    expect(m).toHaveLength(2);
    expect(m[0]).toEqual({ role: 'user', content: '问题' });
    const a = m[1];
    if (a.role === 'assistant') {
      expect(a.steps).toEqual([{ node: 'retrieve', label: '检索' }]);
      expect(a.answer).toBe('你好');
      expect(a.sources).toEqual([{ title: 'a.md' }]);
      expect(a.status).toBe('done');
      expect(a.grounded).toBe(true);
    }
    expect(result.current.streaming).toBe(false);
  });

  it('onError 标 error', async () => {
    streamChatMock.mockImplementation(async (_b: unknown, h: any) => h.onError({ code: 'LLM_ERROR', message: 'boom' }));
    const { result } = renderHook(() => useChat());
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
    await act(async () => { result.current.send('q'); });
    const a = result.current.messages[1];
    if (a.role === 'assistant') { expect(a.status).toBe('error'); expect(a.error).toBe('boom'); }
  });

  it('空问题不发送', async () => {
    const { result } = renderHook(() => useChat());
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
    await act(async () => { result.current.send('   '); });
    expect(streamChatMock).not.toHaveBeenCalled();
    expect(result.current.messages).toHaveLength(0);
  });

  it('挂载时拉历史回填', async () => {
    fetchHistoryMock.mockResolvedValue({ thread_id: 't', messages: [
      { role: 'user', content: '旧问题' },
      { role: 'assistant', content: '旧答案', sources: [{ title: 'x.md' }] },
    ]});
    const { result } = renderHook(() => useChat());
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
    expect(result.current.messages).toHaveLength(2);
    const a = result.current.messages[1];
    if (a.role === 'assistant') { expect(a.answer).toBe('旧答案'); expect(a.status).toBe('done'); }
  });

  it('newChat 清空并换 thread_id', async () => {
    streamChatMock.mockImplementation(async (_b: unknown, h: any) => h.onDone({ thread_id: 't', rewrites: 0, grounded: true }));
    const { result } = renderHook(() => useChat());
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
    await act(async () => { result.current.send('q'); });
    const before = result.current.threadId;
    await act(async () => { result.current.newChat(); await new Promise(r => setTimeout(r, 0)); });
    expect(result.current.messages).toHaveLength(0);
    expect(result.current.threadId).not.toBe(before);
  });

  it('done.grounded=false 传到消息上（前端据此渲染警示横幅）', async () => {
    streamChatMock.mockImplementation(async (_b: unknown, h: any) => {
      h.onToken({ text: '⚠ 本回答未命中知识库' });
      h.onSources({ sources: [] });
      h.onDone({ thread_id: 't', rewrites: 2, grounded: false });
    });
    const { result } = renderHook(() => useChat());
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
    await act(async () => { result.current.send('随便问'); });
    const a = result.current.messages[1];
    if (a.role === 'assistant') {
      expect(a.grounded).toBe(false);
      expect(a.sources).toEqual([]);
      expect(a.status).toBe('done');
    }
  });

  it('挂载回填历史时恢复 grounded（刷新后警示标识不丢）', async () => {
    fetchHistoryMock.mockResolvedValue({ thread_id: 't', messages: [
      { role: 'user', content: '旧问题' },
      { role: 'assistant', content: '旧答案', grounded: false },
    ]});
    const { result } = renderHook(() => useChat());
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
    const a = result.current.messages[1];
    if (a.role === 'assistant') { expect(a.grounded).toBe(false); }
  });

  it('后端未持久化 grounded（null）时不误报警示', async () => {
    fetchHistoryMock.mockResolvedValue({ thread_id: 't', messages: [
      { role: 'user', content: '旧问题' },
      { role: 'assistant', content: '旧答案', grounded: null },
    ]});
    const { result } = renderHook(() => useChat());
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
    const a = result.current.messages[1];
    if (a.role === 'assistant') { expect(a.grounded).toBeUndefined(); }
  });

  it('流式进行中删除：先 abort 并等流结束，才发 DELETE（顺序是硬约束）', async () => {
    // 用一个悬着的流：只有 abort 时才 reject（模拟真实 AbortError）
    const order: string[] = [];
    let signal: AbortSignal | undefined;
    streamChatMock.mockImplementation((_b: unknown, h: any) => new Promise<void>((_res, rej) => {
      signal = h.signal as AbortSignal;
      signal.addEventListener('abort', () => {
        order.push('aborted');
        const e = new Error('Aborted'); e.name = 'AbortError'; rej(e);
      });
    }));
    deleteThreadMock.mockImplementation(async () => {
      order.push('deleted');
      return { thread_id: 't', deleted: true };
    });

    const { result } = renderHook(() => useChat());
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
    act(() => { result.current.send('q'); });                 // 不 await：让流悬着
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });

    let ok = false;
    await act(async () => { ok = await result.current.deleteChat(); });

    expect(order).toEqual(['aborted', 'deleted']);            // DELETE 不得早于流结束
    expect(ok).toBe(true);
    expect(signal?.aborted).toBe(true);
  });

  it('删除成功：清空消息、返回 true、deleting 归位', async () => {
    streamChatMock.mockImplementation(async (_b: unknown, h: any) =>
      h.onDone({ thread_id: 't', rewrites: 0, grounded: true }));
    const { result } = renderHook(() => useChat());
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
    await act(async () => { result.current.send('q'); });
    expect(result.current.messages).toHaveLength(2);

    let ok = false;
    await act(async () => { ok = await result.current.deleteChat(); });

    expect(ok).toBe(true);
    expect(result.current.messages).toHaveLength(0);
    expect(result.current.deleting).toBe(false);
    expect(result.current.deleteError).toBeNull();
  });

  it('删除失败：保留消息 + 中文提示 + 返回 false；clearDeleteError 可清', async () => {
    streamChatMock.mockImplementation(async (_b: unknown, h: any) =>
      h.onDone({ thread_id: 't', rewrites: 0, grounded: true }));
    deleteThreadMock.mockRejectedValue(new Error('delete thread HTTP 500'));
    const { result } = renderHook(() => useChat());
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
    await act(async () => { result.current.send('q'); });

    let ok = true;
    await act(async () => { ok = await result.current.deleteChat(); });

    expect(ok).toBe(false);
    expect(result.current.messages).toHaveLength(2);          // 悲观：不清空
    expect(result.current.deleteError).toBe('删除失败，请重试');
    expect(result.current.deleting).toBe(false);

    act(() => { result.current.clearDeleteError(); });
    expect(result.current.deleteError).toBeNull();
  });

  it('网络层失败(TypeError)提示「无法连接后端」', async () => {
    deleteThreadMock.mockRejectedValue(new TypeError('Failed to fetch'));
    const { result } = renderHook(() => useChat());
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });

    let ok = true;
    await act(async () => { ok = await result.current.deleteChat(); });

    expect(ok).toBe(false);
    expect(result.current.deleteError).toBe('无法连接后端');
  });

  // ---------- P3：LOAD_HISTORY 守卫的两半 ----------
  it('切换会话时即使当前有消息也要替换（否则界面一直显示上一个会话）', async () => {
    fetchHistoryMock.mockResolvedValue({ thread_id: 'tA', messages: [{ role: 'user', content: 'A 的问题' }] });
    const { result } = renderHook(() => useChat());
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
    expect(result.current.messages).toHaveLength(1);

    // 切到 B。回填时 A 的消息还在 state 里 —— 旧守卫（无消息才回填）会把这次回填整条挡掉
    fetchHistoryMock.mockResolvedValue({ thread_id: 'tB', messages: [{ role: 'user', content: 'B 的问题' }] });
    await act(async () => { result.current.setThreadId('tB'); await new Promise(r => setTimeout(r, 0)); });

    expect(result.current.messages).toEqual([{ role: 'user', content: 'B 的问题' }]);
  });

  it('同一会话的迟到历史不冲掉用户刚发起的对话（守卫的另一半）', async () => {
    let release!: (v: unknown) => void;
    fetchHistoryMock.mockImplementation(() => new Promise(r => { release = r; }));   // 历史悬着
    streamChatMock.mockImplementation(async (_b: unknown, h: any) =>
      h.onDone({ thread_id: 't', rewrites: 0, grounded: true }));

    const { result } = renderHook(() => useChat());
    await act(async () => { result.current.send('刚发出的问题'); });                  // 用户先发了消息

    release({ thread_id: 't', messages: [{ role: 'user', content: '很久以前的问题' }] });
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });

    const contents = result.current.messages.map(m => (m.role === 'user' ? m.content : m.answer));
    expect(contents).toContain('刚发出的问题');
    expect(contents).not.toContain('很久以前的问题');      // 同一会话 → 守卫仍要拦住
  });

  it('deleted:false(幂等)同样清空，且 thread_id 保持不变', async () => {
    streamChatMock.mockImplementation(async (_b: unknown, h: any) =>
      h.onDone({ thread_id: 't', rewrites: 0, grounded: true }));
    deleteThreadMock.mockResolvedValue({ thread_id: 't', deleted: false });
    const { result } = renderHook(() => useChat());
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
    await act(async () => { result.current.send('q'); });
    const before = result.current.threadId;

    let ok = false;
    await act(async () => { ok = await result.current.deleteChat(); });

    expect(ok).toBe(true);
    expect(result.current.messages).toHaveLength(0);
    // 与 newChat 语义正交：删除不换 id（spec 决策 8）
    expect(result.current.threadId).toBe(before);
  });
});
