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

export function useThreadId(): { threadId: string; resetThreadId: () => string } {
  const [threadId, setThreadId] = useState<string>(() => {
    const saved = localStorage.getItem(KEY);
    if (saved) return saved;
    const id = genId();
    localStorage.setItem(KEY, id);
    return id;
  });
  const resetThreadId = useCallback(() => {
    const id = genId();
    localStorage.setItem(KEY, id);
    setThreadId(id);
    return id;
  }, []);
  return { threadId, resetThreadId };
}
