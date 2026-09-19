import { useCallback, useEffect, useState } from 'react';
import * as api from '../api/client';
import { clearToken, getToken, setToken } from '../api/tokenStore';
import type { AuthUser } from '../api/types';

/**
 * 鉴权状态机（spec §8.3）。
 *
 * | status    | 含义                    | 触发                                        |
 * |-----------|-------------------------|---------------------------------------------|
 * | `loading` | 有 token，正在 fetchMe  | 初始挂载且 getToken() 非空                   |
 * | `anon`    | 无 token 或校验失败      | 无 token / fetchMe 401 / logout / 401 回调   |
 * | `authed`  | 校验通过，user 有值      | fetchMe 200 或 login/register 成功           |
 *
 * **为什么初始要区分 loading 与 anon**：有 token 时不能立刻当作已登录 —— 得先问一次
 * `/auth/me`。否则 token 过期/被改/后端换 secret 时，界面会显示已登录而每个请求都 401。
 */
export type AuthStatus = 'loading' | 'anon' | 'authed';

export interface UseAuth {
  status: AuthStatus;
  user: AuthUser | null;
  /** 最近一次 login/register 的失败原因（直接来自后端 message），成功则清空 */
  error: string | null;
  login: (username: string, password: string) => Promise<boolean>;
  register: (username: string, password: string) => Promise<boolean>;
  logout: () => void;
}

export function useAuth(): UseAuth {
  const [status, setStatus] = useState<AuthStatus>(() => (getToken() ? 'loading' : 'anon'));
  const [user, setUser] = useState<AuthUser | null>(null);
  const [error, setError] = useState<string | null>(null);

  // 把 client 层的「会话失效」翻译成「回登录页」。
  // 有了它，token 在任意请求上过期都会自动登出，用户不必手动刷新页面。
  useEffect(() => {
    api.setUnauthorizedHandler(() => { setUser(null); setStatus('anon'); });
    return () => api.setUnauthorizedHandler(null);
  }, []);

  // 启动校验：只在 loading 态跑一次。成功→authed，失败→清 token 回 anon。
  useEffect(() => {
    if (status !== 'loading') return;
    let alive = true;                       // 卸载后别再 setState（避免 React 警告与竞态）
    api.fetchMe()
      .then(u => { if (alive) { setUser(u); setStatus('authed'); } })
      .catch(() => { if (alive) { clearToken(); setUser(null); setStatus('anon'); } });
    return () => { alive = false; };
  }, [status]);

  const login = useCallback(async (username: string, password: string) => {
    setError(null);
    try {
      const res = await api.login({ username, password });
      setToken(res.access_token);
      setUser(res.user);
      setStatus('authed');
      return true;
    } catch (e) {
      // 后端对「用户不存在」与「密码错」返回完全相同的 message，这里直接透传即可，
      // 前端不做任何区分（做了就等于把枚举用户名的旁路搬到前端）。
      setError(e instanceof Error ? e.message : '登录失败');
      return false;
    }
  }, []);

  const register = useCallback(async (username: string, password: string) => {
    setError(null);
    try {
      await api.register({ username, password });
      // 契约里 register 只回 {id, username}、**不含 token**，所以注册成功后要再登录一次换 token。
      // 用同一个密码立刻登录是安全的：这一步失败会走下面的 catch，不会出现「注册成功但状态不明」。
      const res = await api.login({ username, password });
      setToken(res.access_token);
      setUser(res.user);
      setStatus('authed');
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : '注册失败');
      return false;
    }
  }, []);

  const logout = useCallback(() => {
    // 刻意不调后端：JWT 无状态，契约里也没有 logout 端点（spec §2 非目标），
    // 清掉本地 token 就是登出。副作用是 token 在 7 天有效期内仍然有效（已记为已知风险）。
    clearToken();
    setUser(null);
    setError(null);
    setStatus('anon');
  }, []);

  return { status, user, error, login, register, logout };
}
