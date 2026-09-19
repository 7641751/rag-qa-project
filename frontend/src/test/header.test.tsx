import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Header } from '../components/Header';

const base = {
  onNewChat: () => {}, onOpenKb: () => {}, onDeleteChat: () => {},
  canDelete: true, kbBadge: 0,
};

describe('Header · 退出登录', () => {
  it('渲染退出按钮，点击时调用 onLogout', () => {
    const onLogout = vi.fn();
    render(<Header {...base} onLogout={onLogout} />);

    fireEvent.click(screen.getByTestId('btn-logout'));

    expect(onLogout).toHaveBeenCalledTimes(1);
  });

  it('显示当前用户名（多账号联调时能一眼看出登的是谁）', () => {
    render(<Header {...base} onLogout={vi.fn()} username="hao" />);

    expect(screen.getByTestId('current-user')).toHaveTextContent('hao');
  });

  it('未传 username 时不渲染用户名，但退出按钮仍在', () => {
    render(<Header {...base} onLogout={vi.fn()} />);

    expect(screen.queryByTestId('current-user')).not.toBeInTheDocument();
    expect(screen.getByTestId('btn-logout')).toBeInTheDocument();
  });

  it('退出不是危险操作：与「删除对话」的禁用态无关（空会话也能退出）', () => {
    render(<Header {...base} canDelete={false} onLogout={vi.fn()} />);

    expect(screen.getByTestId('btn-delete-chat')).toBeDisabled();
    expect(screen.getByTestId('btn-logout')).not.toBeDisabled();
  });
});
