import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

const { fetchThreadsMock, renameThreadMock, deleteThreadMock } = vi.hoisted(() => ({
  fetchThreadsMock: vi.fn(), renameThreadMock: vi.fn(), deleteThreadMock: vi.fn(),
}));
vi.mock('../api/client', () => ({
  fetchThreads: fetchThreadsMock,
  renameThread: renameThreadMock,
  deleteThread: deleteThreadMock,
}));

import { useConversations } from '../hooks/useConversations';

const row = (id: string, title: string, updatedAt: string) => ({
  thread_id: id, title, created_at: '2026-09-13T06:00:00Z', updated_at: updatedAt,
});
const TWO = [row('t1', '第一个会话', '2026-09-13T07:00:00Z'), row('t2', '第二个会话', '2026-09-13T06:00:00Z')];

const flush = () => act(async () => { await new Promise(r => setTimeout(r, 0)); });

beforeEach(() => {
  fetchThreadsMock.mockReset();
  renameThreadMock.mockReset();
  deleteThreadMock.mockReset();
  fetchThreadsMock.mockResolvedValue({ threads: TWO, total: 2 });
  renameThreadMock.mockResolvedValue({ thread_id: 't1', title: '改过的标题' });
  deleteThreadMock.mockResolvedValue({ thread_id: 't1', deleted: true });
});

describe('useConversations', () => {
  it('挂载时加载列表，total 取自后端（而非数组长度）', async () => {
    // total > threads.length 是「被 50 条上限截断」的唯一信号，必须原样带出来
    fetchThreadsMock.mockResolvedValue({ threads: TWO, total: 55 });
    const { result } = renderHook(() => useConversations());
    await flush();

    expect(result.current.threads.map(t => t.thread_id)).toEqual(['t1', 't2']);
    expect(result.current.total).toBe(55);
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeUndefined();
  });

  it('列表加载失败 → error 有值且不影响其它状态', async () => {
    fetchThreadsMock.mockRejectedValue(new Error('threads HTTP 500'));
    const { result } = renderHook(() => useConversations());
    await flush();

    expect(result.current.error).toBe('threads HTTP 500');
    expect(result.current.loading).toBe(false);
    expect(result.current.threads).toEqual([]);
  });

  it('refresh 重新拉取（失败后重试的路径）', async () => {
    fetchThreadsMock.mockRejectedValueOnce(new Error('boom'));
    const { result } = renderHook(() => useConversations());
    await flush();
    expect(result.current.error).toBe('boom');

    fetchThreadsMock.mockResolvedValue({ threads: TWO, total: 2 });
    await act(async () => { await result.current.refresh(); });

    expect(result.current.error).toBeUndefined();
    expect(result.current.threads).toHaveLength(2);
  });

  it('rename 乐观更新：请求还没回来标题就已经改了', async () => {
    let release!: (v: unknown) => void;
    renameThreadMock.mockImplementation(() => new Promise(r => { release = r; }));
    const { result } = renderHook(() => useConversations());
    await flush();

    act(() => { void result.current.rename('t1', '新标题'); });

    expect(result.current.threads[0]!.title).toBe('新标题');    // 未 await 就已生效
    expect(renameThreadMock).toHaveBeenCalledWith('t1', '新标题');
    release({ thread_id: 't1', title: '新标题' });
    await flush();
  });

  it('rename 失败 → 按原标题回滚并给出 itemError', async () => {
    renameThreadMock.mockRejectedValue(new Error('该会话不属于当前用户'));
    const { result } = renderHook(() => useConversations());
    await flush();

    let ok = true;
    await act(async () => { ok = await result.current.rename('t1', '新标题'); });

    expect(ok).toBe(false);
    expect(result.current.threads[0]!.title).toBe('第一个会话');   // 回滚到原值
    // 必须带 threadId，否则这一行错误会被渲染到每一行上
    expect(result.current.itemError).toEqual({ threadId: 't1', message: '该会话不属于当前用户' });
  });

  it('remove 乐观移除并调 DELETE', async () => {
    const { result } = renderHook(() => useConversations());
    await flush();

    let ok = false;
    await act(async () => { ok = await result.current.remove('t1'); });

    expect(ok).toBe(true);
    expect(deleteThreadMock).toHaveBeenCalledWith('t1');
    expect(result.current.threads.map(t => t.thread_id)).toEqual(['t2']);
  });

  it('remove 失败 → 按**原下标**插回（不是追加到末尾）', async () => {
    deleteThreadMock.mockRejectedValue(new Error('delete thread HTTP 500'));
    const { result } = renderHook(() => useConversations());
    await flush();

    let ok = true;
    await act(async () => { ok = await result.current.remove('t1'); });

    expect(ok).toBe(false);
    // 插回原位，否则这一行会跳到列表末尾、看起来像「刚更新过」，与 updated_at 倒序矛盾
    expect(result.current.threads.map(t => t.thread_id)).toEqual(['t1', 't2']);
    expect(result.current.itemError).toEqual({ threadId: 't1', message: '删除失败，请重试' });
  });

  it('clearItemError 可清掉行级错误', async () => {
    renameThreadMock.mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() => useConversations());
    await flush();
    await act(async () => { await result.current.rename('t1', 'x'); });
    expect(result.current.itemError?.message).toBe('boom');

    act(() => { result.current.clearItemError(); });

    expect(result.current.itemError).toBeUndefined();
  });
});
