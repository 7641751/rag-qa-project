import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

const { streamChatMock, fetchHistoryMock } = vi.hoisted(() => ({
  streamChatMock: vi.fn(), fetchHistoryMock: vi.fn(),
}));
vi.mock('../api/client', () => ({
  streamChat: streamChatMock, fetchHistory: fetchHistoryMock,
}));

import { useChat } from '../hooks/useChat';

beforeEach(() => {
  localStorage.clear();
  streamChatMock.mockReset();
  fetchHistoryMock.mockReset();
  fetchHistoryMock.mockResolvedValue({ thread_id: 't', messages: [] });
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
});
