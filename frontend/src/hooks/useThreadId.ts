import { useCallback, useState } from 'react';

const KEY = 'ragqa_thread_id';

/** 生成 uuid；无 crypto.randomUUID 的极端环境走手写兜底。useKnowledgeBase 复用此函数。 */
export function genId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  // 兜底（极端环境无 crypto.randomUUID 时）
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

export function useThreadId(): {
  threadId: string;
  /** 切到指定会话（侧栏点击用）。与 resetThreadId 的区别：换到的 id 由调用方给，
   *  用于「回到某个已存在的会话」，而 resetThreadId 是开一段全新的。 */
  setThreadId: (id: string) => void;
  resetThreadId: () => string;
} {
  const [threadId, setThreadIdState] = useState<string>(() => {
    const saved = localStorage.getItem(KEY);
    if (saved) return saved;
    const id = genId();
    localStorage.setItem(KEY, id);
    return id;
  });

  // 与 resetThreadId 一致：**写回 localStorage**。否则刷新后会回到切走前那个会话，
  // 而用户在侧栏选的那个反而丢了。
  const setThreadId = useCallback((id: string) => {
    localStorage.setItem(KEY, id);
    setThreadIdState(id);
  }, []);

  const resetThreadId = useCallback(() => {
    const id = genId();
    localStorage.setItem(KEY, id);
    setThreadIdState(id);
    return id;
  }, []);

  return { threadId, setThreadId, resetThreadId };
}
