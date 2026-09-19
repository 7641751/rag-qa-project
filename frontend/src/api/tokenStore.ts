/**
 * JWT 的本地存取。单独成模块（而不是散在 client.ts 里）有两个理由：
 *
 * 1. **可替换**：测试要点是「401 会清 token」「启动时拿 token 去 fetchMe」，这些都依赖
 *    能独立地读写与清空 token；单独成模块后测试可以直接 import 它，不必去猜 localStorage 的键名。
 * 2. **单一键名**：键名只在这里出现一次。若将来升级到 httpOnly cookie（见 spec §11 的升级路径），
 *    改动面就是这一个文件 + client.ts 的 request()。
 *
 * ⚠ 安全性：localStorage 里的 token 可被任意注入脚本读取（XSS）。本期是 spec §3 决策 1 明确
 * 选定的权衡（对比 httpOnly cookie 需要引入 CSRF 防护），不要在此基础上存放更敏感的东西。
 */
const KEY = 'ragqa_token';

export function getToken(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    // Safari 隐私模式等场景下 localStorage 可能直接抛异常 —— 降级成「无 token」，
    // 让用户看到登录页，而不是整个应用白屏。
    return null;
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem(KEY, token);
  } catch { /* 存不进去就只能本次会话内存态，下次刷新要重新登录；不阻断当前流程 */ }
}

export function clearToken(): void {
  try {
    localStorage.removeItem(KEY);
  } catch { /* 同上，忽略 */ }
}
