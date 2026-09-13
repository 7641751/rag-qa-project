import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useThreadId } from '../hooks/useThreadId';

beforeEach(() => localStorage.clear());

describe('useThreadId', () => {
  it('首访生成并持久化 uuid', () => {
    const { result } = renderHook(() => useThreadId());
    expect(result.current.threadId).toMatch(/^[0-9a-f-]{36}$/);
    expect(localStorage.getItem('ragqa_thread_id')).toBe(result.current.threadId);
  });
  it('复用已存 thread_id', () => {
    localStorage.setItem('ragqa_thread_id', 'fixed-id');
    const { result } = renderHook(() => useThreadId());
    expect(result.current.threadId).toBe('fixed-id');
  });
  it('resetThreadId 换新并持久化', () => {
    const { result } = renderHook(() => useThreadId());
    const first = result.current.threadId;
    let second = '';
    act(() => { second = result.current.resetThreadId(); });
    expect(second).not.toBe(first);
    expect(localStorage.getItem('ragqa_thread_id')).toBe(second);
  });
});
