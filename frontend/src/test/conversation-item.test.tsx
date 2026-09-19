import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ConversationItem, relTime } from '../components/ConversationItem';
import type { ConversationSummary } from '../api/types';

const conv: ConversationSummary = {
  thread_id: 't1', title: 'checkpointer 怎么删会话',
  created_at: '2026-09-13T06:00:00Z', updated_at: new Date(Date.now() - 120_000).toISOString(),
};

function setup(over: Partial<Parameters<typeof ConversationItem>[0]> = {}) {
  const props = {
    conv, active: false,
    onSelect: vi.fn(), onRename: vi.fn(), onDelete: vi.fn(),
    ...over,
  };
  render(<ConversationItem {...props} />);
  return props;
}

describe('ConversationItem', () => {
  it('hover 前不显示 ✎/🗑，hover 后才出现', () => {
    setup();

    expect(screen.queryByTestId('rename-btn')).not.toBeInTheDocument();
    expect(screen.queryByTestId('delete-btn')).not.toBeInTheDocument();

    fireEvent.mouseEnter(screen.getByTestId('conv-item'));

    expect(screen.getByTestId('rename-btn')).toBeInTheDocument();
    expect(screen.getByTestId('delete-btn')).toBeInTheDocument();
  });

  it('当前会话（active）无需 hover 就常显按钮', () => {
    setup({ active: true });

    expect(screen.getByTestId('conv-item')).toHaveAttribute('data-active', 'true');
    expect(screen.getByTestId('rename-btn')).toBeInTheDocument();
  });

  it('点 ✎ 进入编辑：input 预填当前标题且全选', () => {
    setup({ active: true });

    fireEvent.click(screen.getByTestId('rename-btn'));

    const input = screen.getByTestId('rename-input') as HTMLInputElement;
    expect(input.value).toBe('checkpointer 怎么删会话');
    expect(document.activeElement).toBe(input);       // 已聚焦（配合 select() 便于直接覆写）
  });

  it('Enter 提交新标题', () => {
    const { onRename } = setup({ active: true });
    fireEvent.click(screen.getByTestId('rename-btn'));
    fireEvent.change(screen.getByTestId('rename-input'), { target: { value: '  持久化原理  ' } });

    fireEvent.keyDown(screen.getByTestId('rename-input'), { key: 'Enter' });

    // 前后空白被 trim 掉（varchar(60) 存空白没有意义）
    expect(onRename).toHaveBeenCalledWith('t1', '持久化原理');
    expect(screen.queryByTestId('rename-input')).not.toBeInTheDocument();
  });

  it('Esc 取消：不提交、退出编辑态', () => {
    const { onRename } = setup({ active: true });
    fireEvent.click(screen.getByTestId('rename-btn'));
    fireEvent.change(screen.getByTestId('rename-input'), { target: { value: '改了一半' } });

    fireEvent.keyDown(screen.getByTestId('rename-input'), { key: 'Escape' });

    expect(onRename).not.toHaveBeenCalled();
    expect(screen.queryByTestId('rename-input')).not.toBeInTheDocument();
  });

  it('空标题被前端拦下：不发请求、留在编辑态并提示', () => {
    const { onRename } = setup({ active: true });
    fireEvent.click(screen.getByTestId('rename-btn'));
    fireEvent.change(screen.getByTestId('rename-input'), { target: { value: '   ' } });

    fireEvent.keyDown(screen.getByTestId('rename-input'), { key: 'Enter' });

    expect(onRename).not.toHaveBeenCalled();                              // 明知会被 422
    expect(screen.getByTestId('rename-error')).toHaveTextContent('标题不能为空');
    expect(screen.getByTestId('rename-input')).toBeInTheDocument();       // 仍在编辑，可改完再交
  });

  it('超 60 字被前端拦下', () => {
    const { onRename } = setup({ active: true });
    fireEvent.click(screen.getByTestId('rename-btn'));
    fireEvent.change(screen.getByTestId('rename-input'), { target: { value: '字'.repeat(61) } });

    fireEvent.keyDown(screen.getByTestId('rename-input'), { key: 'Enter' });

    expect(onRename).not.toHaveBeenCalled();
    expect(screen.getByTestId('rename-error')).toHaveTextContent('不能超过 60 字');
  });

  it('标题没改就提交：不发请求', () => {
    const { onRename } = setup({ active: true });
    fireEvent.click(screen.getByTestId('rename-btn'));

    fireEvent.keyDown(screen.getByTestId('rename-input'), { key: 'Enter' });

    expect(onRename).not.toHaveBeenCalled();
  });

  it('点标题选中会话、点 🗑 请求删除', () => {
    const { onSelect, onDelete } = setup({ active: true });

    fireEvent.click(screen.getByTestId('conv-title'));
    fireEvent.click(screen.getByTestId('delete-btn'));

    expect(onSelect).toHaveBeenCalledWith('t1');
    expect(onDelete).toHaveBeenCalledWith('t1');
  });

  it('显示传进来的行级错误（可见，不只是给屏幕阅读器）', () => {
    setup({ active: true, error: '该会话不属于当前用户' });

    const err = screen.getByTestId('conv-error');
    expect(err).toHaveTextContent('该会话不属于当前用户');
    expect(err.className).not.toContain('sr-only');
  });
});

describe('relTime', () => {
  const now = Date.parse('2026-09-13T12:00:00Z');
  const at = (ms: number) => new Date(now - ms).toISOString();

  it('各档位的中文表述', () => {
    expect(relTime(at(30_000), now)).toBe('刚刚');
    expect(relTime(at(5 * 60_000), now)).toBe('5 分钟前');
    expect(relTime(at(3 * 3600_000), now)).toBe('3 小时前');
    expect(relTime(at(2 * 86400_000), now)).toBe('2 天前');
    expect(relTime(at(40 * 86400_000), now)).toBe('2026-08-04');   // 超 30 天给日期
  });

  it('脏数据返回空串，而不是「NaN 天前」', () => {
    expect(relTime('not-a-date', now)).toBe('');
  });
});
