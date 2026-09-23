import { useCallback, useState } from 'react';

/** 折叠状态持久化的键。单独成常量便于测试与将来迁移。 */
const KEY = 'ragqa_sidebar_collapsed';

/** 窄屏（<768px，Tailwind 的 md 断点）默认折叠：250px 侧栏会把聊天区挤没。
 *  jsdom 没有可用的 matchMedia（实现是 stub，可能返回 undefined 或直接抛），
 *  所以**必须** try/catch —— 测试环境一律走「未记录偏好 → 展开」的默认行为。 */
function narrowByDefault(): boolean {
  try {
    return (
      typeof window !== 'undefined' &&
      typeof window.matchMedia === 'function' &&
      (window.matchMedia('(max-width: 767px)').matches ?? false)
    );
  } catch {
    return false;
  }
}

/** 侧栏折叠开关。
 *
 *  **为什么持久化**：侧栏是可选的视野偏好，不该每次刷新都回到默认值 —— 用户折叠它
 *  通常是因为屏幕窄，刷新后又弹开会很难受。
 *
 *  存 `'1'` / `'0'` 而不是 `'true'` / `'false'`：localStorage 里全是字符串，`'0'` 是
 *  **真值**（非空串），所以判断必须用 `=== '1'`，不能用真值判断 —— 否则「存过 0」
 *  会被读成折叠。
 */
export function useSidebar(): { collapsed: boolean; toggle: () => void } {
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      const stored = localStorage.getItem(KEY);
      // 用户已有明确偏好 → 尊重它；否则按屏幕宽度给默认值
      if (stored !== null) return stored === '1';
      return narrowByDefault();
    } catch {
      // Safari 隐私模式等场景 localStorage 可能直接抛异常 → 降级为展开，不影响使用
      return false;
    }
  });

  const toggle = useCallback(() => {
    setCollapsed(c => {
      const next = !c;
      try {
        localStorage.setItem(KEY, next ? '1' : '0');
      } catch { /* 存不进去只影响下次刷新，本次会话仍按 next 生效 */ }
      return next;
    });
  }, []);

  return { collapsed, toggle };
}
