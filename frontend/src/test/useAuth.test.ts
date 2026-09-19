import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';

const { fetchMeMock, loginMock, registerMock, setUnauthorizedHandlerMock } = vi.hoisted(() => ({
  fetchMeMock: vi.fn(),
  loginMock: vi.fn(),
  registerMock: vi.fn(),
  setUnauthorizedHandlerMock: vi.fn(),
}));

vi.mock('../api/client', () => ({
  fetchMe: fetchMeMock,
  login: loginMock,
  register: registerMock,
  setUnauthorizedHandler: setUnauthorizedHandlerMock,
}));

import { useAuth } from '../hooks/useAuth';
import { getToken, setToken } from '../api/tokenStore';

const HAO = { id: 7, username: 'hao' };

beforeEach(() => {
  localStorage.clear();
  fetchMeMock.mockReset();
  loginMock.mockReset();
  registerMock.mockReset();
  setUnauthorizedHandlerMock.mockReset();
});

describe('useAuth', () => {
  it('无 token → 直接 anon，且不去调 fetchMe', () => {
    const { result } = renderHook(() => useAuth());

    expect(result.current.status).toBe('anon');
    expect(fetchMeMock).not.toHaveBeenCalled();
  });

  it('有 token → 先 loading，fetchMe 成功后 authed 并带 user', async () => {
    setToken('tk');
    fetchMeMock.mockResolvedValue(HAO);

    const { result } = renderHook(() => useAuth());
    expect(result.current.status).toBe('loading');            // 不直接信任本地 token

    await waitFor(() => expect(result.current.status).toBe('authed'));
    expect(result.current.user).toEqual(HAO);
  });

  it('有 token 但校验失败 → 清 token 并回 anon', async () => {
    setToken('stale');
    fetchMeMock.mockRejectedValue(new Error('登录状态校验失败'));

    const { result } = renderHook(() => useAuth());

    await waitFor(() => expect(result.current.status).toBe('anon'));
    expect(getToken()).toBeNull();
    expect(result.current.user).toBeNull();
  });

  it('login 成功 → 存 token、authed、返回 true', async () => {
    loginMock.mockResolvedValue({ access_token: 'new-tk', token_type: 'bearer', user: HAO });
    const { result } = renderHook(() => useAuth());

    let ok: boolean | undefined;
    await act(async () => { ok = await result.current.login('hao', 'abcd1234'); });

    expect(ok).toBe(true);
    expect(getToken()).toBe('new-tk');
    expect(result.current.status).toBe('authed');
    expect(result.current.user).toEqual(HAO);
  });

  it('login 失败 → error 用后端 message，status 仍 anon，返回 false', async () => {
    loginMock.mockRejectedValue(new Error('用户名或密码错误'));
    const { result } = renderHook(() => useAuth());

    let ok: boolean | undefined;
    await act(async () => { ok = await result.current.login('hao', 'bad-password'); });

    expect(ok).toBe(false);
    expect(result.current.error).toBe('用户名或密码错误');     // 登录页直接显示这句
    expect(result.current.status).toBe('anon');
  });

  it('register 成功后自动登录（契约里 register 不回 token）', async () => {
    registerMock.mockResolvedValue({ id: 2, username: 'hao2' });
    loginMock.mockResolvedValue({ access_token: 'tk2', token_type: 'bearer', user: { id: 2, username: 'hao2' } });
    const { result } = renderHook(() => useAuth());

    let ok: boolean | undefined;
    await act(async () => { ok = await result.current.register('hao2', 'abcd1234'); });

    expect(ok).toBe(true);
    expect(registerMock).toHaveBeenCalledWith({ username: 'hao2', password: 'abcd1234' });
    expect(loginMock).toHaveBeenCalledWith({ username: 'hao2', password: 'abcd1234' });
    expect(getToken()).toBe('tk2');
    expect(result.current.status).toBe('authed');
  });

  it('logout → 清 token 并回 anon', async () => {
    setToken('tk');
    fetchMeMock.mockResolvedValue(HAO);
    const { result } = renderHook(() => useAuth());
    await waitFor(() => expect(result.current.status).toBe('authed'));

    act(() => { result.current.logout(); });

    expect(getToken()).toBeNull();
    expect(result.current.status).toBe('anon');
    expect(result.current.user).toBeNull();
  });

  it('注册了「会话失效」回调：被调用时自动回登录页', async () => {
    setToken('tk');
    fetchMeMock.mockResolvedValue(HAO);
    const { result } = renderHook(() => useAuth());
    await waitFor(() => expect(result.current.status).toBe('authed'));

    // client.request() 在「带 token 却 401」时会调这个回调（见 client.test.ts）
    const handler = setUnauthorizedHandlerMock.mock.calls[0]![0] as () => void;
    act(() => { handler(); });

    expect(result.current.status).toBe('anon');
    expect(result.current.user).toBeNull();
  });
});
