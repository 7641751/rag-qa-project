import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, renderHook, act, within } from '@testing-library/react';
import { Sidebar } from '../components/Sidebar';
import { useSidebar } from '../hooks/useSidebar';
import type { ConversationSummary } from '../api/types';

const row = (id: string, title: string, updatedAt = '2026-09-13T06:00:00Z'): ConversationSummary =>
  ({ thread_id: id, title, created_at: '2026-09-13T05:00:00Z', updated_at: updatedAt });

const TWO = [row('t1', '第一个'), row('t2', '第二个')];

function setup(over: Partial<Parameters<typeof Sidebar>[0]> = {}) {
  const props = {
    collapsed: false, onToggle: vi.fn(),
    threads: TWO, total: 2, loading: false,
    error: null, itemError: null,
    activeId: 't1',
    onSelect: vi.fn(), onRename: vi.fn(), onDelete: vi.fn(), onRetry: vi.fn(),
    ...over,
  };
  render(<Sidebar {...props} />);
  return props;
}

beforeEach(() => localStorage.clear());

describe('Sidebar', () => {
  it('展开态渲染列表，并把 activeId 高亮成当前项', () => {
    setup();

    expect(screen.getByTestId('sidebar')).toHaveAttribute('data-collapsed', 'false');
    expect(screen.getAllByTestId('conv-title').map(b => b.textContent)).toEqual(['第一个', '第二个']);
    // 当前项高亮：只有 t1 那行是 active
    expect(screen.getAllByTestId('conv-item')[0]).toHaveAttribute('data-active', 'true');
    expect(screen.getAllByTestId('conv-item')[1]).toHaveAttribute('data-active', 'false');
  });

  it('点折叠把手调 onToggle', () => {
    const { onToggle } = setup();

    fireEvent.click(screen.getByTestId('sidebar-toggle'));

    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('折叠态 data-collapsed 为 true，且把手仍在（否则折叠后就无从展开了）', () => {
    setup({ collapsed: true });

    expect(screen.getByTestId('sidebar')).toHaveAttribute('data-collapsed', 'true');
    expect(screen.getByTestId('sidebar-toggle')).toBeInTheDocument();
  });

  it('空列表显示引导文案', () => {
    setup({ threads: [], total: 0 });

    expect(screen.getByTestId('sidebar-empty')).toHaveTextContent('还没有会话，问一句就开始');
  });

  it('首次加载中显示 loading，而不是「还没有会话」', () => {
    setup({ threads: [], total: 0, loading: true });

    expect(screen.getByTestId('sidebar-loading')).toBeInTheDocument();
    expect(screen.queryByTestId('sidebar-empty')).not.toBeInTheDocument();
  });

  it('列表加载失败显示错误行与重试按钮', () => {
    const { onRetry } = setup({ threads: [], total: 0, error: '会话列表加载失败' });

    expect(screen.getByTestId('sidebar-error')).toHaveTextContent('会话列表加载失败');
    fireEvent.click(screen.getByTestId('sidebar-retry'));

    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('有错误时不再显示空态文案（避免两句话同时出现）', () => {
    setup({ threads: [], total: 0, error: 'boom' });

    expect(screen.queryByTestId('sidebar-empty')).not.toBeInTheDocument();
  });

  it('total 大于列表长度时提示被截断', () => {
    setup({ threads: TWO, total: 55 });

    expect(screen.getByTestId('list-truncated')).toHaveTextContent('仅显示最近 50 条');
  });

  it('行级错误只出现在对应的那一行', () => {
    setup({ itemError: { threadId: 't2', message: '该会话不属于当前用户' } });

    // 只有一条错误节点 —— 若 itemError 不带 threadId，这里会渲染出两条
    const errs = screen.getAllByTestId('conv-error');
    expect(errs).toHaveLength(1);
    expect(errs[0]).toHaveTextContent('该会话不属于当前用户');
  });

  it('选中/删除回调带上正确的 threadId', () => {
    const props = setup();                          // activeId 默认 t1
    // ⚠ 必须用 within() 定位到「行」再取按钮：按钮只在 active 行常显，
    // 直接 getAllByTestId('delete-btn')[0] 拿到的是当行，与 conv-title 的下标并不对齐
    const t1 = screen.getAllByTestId('conv-item')[0]!;

    fireEvent.click(within(t1).getByTestId('conv-title'));
    fireEvent.click(within(t1).getByTestId('delete-btn'));

    expect(props.onSelect).toHaveBeenCalledWith('t1');
    expect(props.onDelete).toHaveBeenCalledWith('t1');
  });
});

describe('useSidebar（折叠持久化）', () => {
  it('初始从 localStorage 读取：存过 "1" 就是折叠态', () => {
    localStorage.setItem('ragqa_sidebar_collapsed', '1');
    const { result } = renderHook(() => useSidebar());
    expect(result.current.collapsed).toBe(true);
  });

  it('toggle 写回 localStorage，且 "0" 必须被读成**未**折叠', () => {
    const { result } = renderHook(() => useSidebar());
    expect(result.current.collapsed).toBe(false);

    act(() => { result.current.toggle(); });
    expect(result.current.collapsed).toBe(true);
    expect(localStorage.getItem('ragqa_sidebar_collapsed')).toBe('1');

    act(() => { result.current.toggle(); });
    expect(localStorage.getItem('ragqa_sidebar_collapsed')).toBe('0');
    // '0' 是非空字符串（真值）—— 判断必须用 === '1'，用真值判断会把它当成折叠
    const { result: again } = renderHook(() => useSidebar());
    expect(again.current.collapsed).toBe(false);
  });
});
