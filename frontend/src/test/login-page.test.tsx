import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { LoginPage } from '../components/LoginPage';

function fill(username: string, password: string) {
  fireEvent.change(screen.getByTestId('auth-username'), { target: { value: username } });
  fireEvent.change(screen.getByTestId('auth-password'), { target: { value: password } });
}

const submit = () => fireEvent.click(screen.getByTestId('auth-submit'));

beforeEach(() => vi.clearAllMocks());

describe('LoginPage', () => {
  it('默认是登录态', () => {
    render(<LoginPage onLogin={vi.fn()} onRegister={vi.fn()} />);

    expect(screen.getByTestId('auth-submit')).toHaveTextContent('登录');
    expect(screen.getByTestId('auth-toggle-login')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('auth-username')).toHaveAttribute('autocomplete', 'username');
    expect(screen.queryByTestId('auth-error')).not.toBeInTheDocument();
  });

  it('切到注册态：按钮文案变「注册」，密码 autoComplete 变 new-password', () => {
    render(<LoginPage onLogin={vi.fn()} onRegister={vi.fn()} />);

    fireEvent.click(screen.getByTestId('auth-toggle-register'));

    expect(screen.getByTestId('auth-submit')).toHaveTextContent('注册');
    expect(screen.getByTestId('auth-password')).toHaveAttribute('autocomplete', 'new-password');
  });

  it('本地校验提示优先于后端旧错误（避免旧错误盖住当前操作的问题）', () => {
    render(<LoginPage onLogin={vi.fn()} onRegister={vi.fn()} error="用户名或密码错误" />);

    submit();     // 空字段

    expect(screen.getByTestId('auth-error')).toHaveTextContent('请填写用户名和密码');
    expect(screen.getByTestId('auth-error')).not.toHaveTextContent('用户名或密码错误');
  });

  it('切换模式会清掉本地校验提示', () => {
    render(<LoginPage onLogin={vi.fn()} onRegister={vi.fn()} />);

    submit();     // 空字段 → 本地提示
    expect(screen.getByTestId('auth-error')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('auth-toggle-register'));

    expect(screen.queryByTestId('auth-error')).not.toBeInTheDocument();
  });

  it('空字段提交 → 只做本地非空校验提示，不调后端', () => {
    const onLogin = vi.fn();
    render(<LoginPage onLogin={onLogin} onRegister={vi.fn()} />);

    submit();

    expect(screen.getByTestId('auth-error')).toHaveTextContent('请填写用户名和密码');
    expect(onLogin).not.toHaveBeenCalled();
  });

  it('提交时进入 busy：按钮禁用并显示「处理中…」，结束后复位', async () => {
    let release!: (ok: boolean) => void;
    const onLogin = vi.fn(() => new Promise<boolean>(r => { release = r; }));
    render(<LoginPage onLogin={onLogin} onRegister={vi.fn()} />);
    fill('hao', 'abcd1234');

    submit();

    await waitFor(() => expect(screen.getByTestId('auth-submit')).toBeDisabled());
    expect(screen.getByTestId('auth-submit')).toHaveTextContent('处理中…');
    expect(onLogin).toHaveBeenCalledWith('hao', 'abcd1234');

    release(false);
    await waitFor(() => expect(screen.getByTestId('auth-submit')).not.toBeDisabled());
  });

  it('后端失败原因原样显示在错误行（注册态调 onRegister）', async () => {
    const onRegister = vi.fn().mockResolvedValue(false);
    render(<LoginPage onLogin={vi.fn()} onRegister={onRegister} error="用户名已存在" />);

    fireEvent.click(screen.getByTestId('auth-toggle-register'));
    fill('hao', 'abcd1234');
    submit();

    await waitFor(() => expect(onRegister).toHaveBeenCalled());
    // 文案**必须是后端原文**，前端不加工（否则会掩盖「故意不区分原因」的契约）
    await waitFor(() => expect(screen.getByTestId('auth-error')).toHaveTextContent('用户名已存在'));
  });
});
