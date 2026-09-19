import { useState } from 'react';

/**
 * 登录 / 注册页（spec §8.4）。
 *
 * 三条刻意的克制：
 * ① **不引 react-router**：只有「登录页 / 主应用」两个视图，用一个 segmented 切换就够。
 * ② **只做非空校验**：长度/字符集一律交给后端（`Auth` 模型），否则前后端两套规则必然漂移。
 * ③ **错误文案不加工**：直接把后端的 `message` 显示出来 —— 登录失败时后端故意不区分
 *    「用户不存在」与「密码错」，前端若自作聪明地分类就等于把枚举用户名的旁路搬到了前端。
 */
export type AuthMode = 'login' | 'register';

export interface LoginPageProps {
  onLogin: (username: string, password: string) => Promise<boolean>;
  onRegister: (username: string, password: string) => Promise<boolean>;
  /** 最近一次失败原因（来自 useAuth.error，原样是后端 message） */
  error?: string | null;
}

export function LoginPage({ onLogin, onRegister, error = null }: LoginPageProps) {
  const [mode, setMode] = useState<AuthMode>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const isLogin = mode === 'login';
  const shownError = localError ?? error;

  function switchMode(next: AuthMode) {
    setMode(next);
    setLocalError(null);       // 清掉上一模式的报错，否则「注册失败」会留在登录态里误导
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;          // 双击防护：按钮已禁用，这里再兜一层
    if (!username.trim() || !password) {
      setLocalError('请填写用户名和密码');
      return;
    }
    setLocalError(null);
    setBusy(true);
    try {
      await (isLogin ? onLogin : onRegister)(username.trim(), password);
    } finally {
      setBusy(false);          // 失败也要复位，否则按钮永久卡在「处理中…」
    }
  }

  const segCls = (active: boolean) =>
    `rounded px-3 py-1.5 text-xs font-medium transition ${
      active ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'
    }`;

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 p-4">
      <form
        onSubmit={submit}
        data-testid="login-page"
        className="w-full max-w-sm rounded-lg bg-white p-6 shadow-xl"
      >
        <h1 className="text-center text-base font-semibold text-gray-900">LangChain 智能问答</h1>

        <div
          role="tablist"
          data-testid="auth-toggle"
          className="mt-5 grid grid-cols-2 gap-1 rounded-md bg-gray-100 p-1"
        >
          <button
            type="button" role="tab" aria-selected={isLogin}
            data-testid="auth-toggle-login"
            onClick={() => switchMode('login')}
            className={segCls(isLogin)}
          >登录</button>
          <button
            type="button" role="tab" aria-selected={!isLogin}
            data-testid="auth-toggle-register"
            onClick={() => switchMode('register')}
            className={segCls(!isLogin)}
          >注册</button>
        </div>

        <label className="mt-5 block text-xs text-gray-600" htmlFor="auth-username-input">用户名</label>
        <input
          id="auth-username-input"
          data-testid="auth-username"
          autoComplete="username"
          value={username}
          onChange={e => setUsername(e.target.value)}
          className="mt-1 w-full rounded-md border px-3 py-2 text-sm outline-none focus:border-gray-400"
        />

        <label className="mt-3 block text-xs text-gray-600" htmlFor="auth-password-input">密码</label>
        <input
          id="auth-password-input"
          data-testid="auth-password"
          type="password"
          autoComplete={isLogin ? 'current-password' : 'new-password'}
          value={password}
          onChange={e => setPassword(e.target.value)}
          className="mt-1 w-full rounded-md border px-3 py-2 text-sm outline-none focus:border-gray-400"
        />

        {shownError && (
          <p
            data-testid="auth-error"
            className="mt-3 rounded-md border border-red-200 bg-red-50 px-2 py-1.5 text-xs leading-relaxed text-red-700"
          >{shownError}</p>
        )}

        <button
          type="submit"
          data-testid="auth-submit"
          disabled={busy}
          className="mt-5 w-full rounded-md bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-50"
        >{busy ? '处理中…' : (isLogin ? '登录' : '注册')}</button>
      </form>
    </div>
  );
}
