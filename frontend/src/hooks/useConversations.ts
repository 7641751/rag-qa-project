import { useCallback, useEffect, useReducer } from 'react';
import type { ConversationSummary } from '../api/types';
import { deleteThread, fetchThreads, renameThread } from '../api/client';

/** 后端对 title 的硬上限（`conversations.title` 是 varchar(60)）。前端据此先拦一道，
 *  免得明知会被 422 还白发一次请求（§10.5）。 */
export const RENAME_MAX_CHARS = 60;

/** 后端列表的硬上限（§7.1）。`total` 大于它说明被截断了，侧栏要提示「仅显示最近 50 条」。 */
export const LIST_LIMIT = 50;

interface State {
  threads: ConversationSummary[];
  total: number;
  loading: boolean;
  /** 列表级错误：整块加载失败 → 侧栏显示错误行 + 重试按钮（不影响聊天区） */
  listError?: string;
  /** 行级错误：某一条的重命名/删除失败 → 显示在**那一行内**。
   *  ⚠ 必须带上 threadId：只要一个字符串的话，`itemError` 会被渲染到**每一行**上，
   *  用户看到的是一个「所有行都报同一个错」的诡异界面。 */
  itemError?: { threadId: string; message: string };
}

type Action =
  | { type: 'LIST_START' }
  | { type: 'LIST_OK'; threads: ConversationSummary[]; total: number }
  | { type: 'LIST_FAIL'; error: string }
  | { type: 'SET_TITLE'; threadId: string; title: string }
  | { type: 'REMOVE'; threadId: string }
  | { type: 'RESTORE'; row: ConversationSummary; index: number }
  | { type: 'ITEM_FAIL'; threadId: string; error: string }
  | { type: 'CLEAR_ITEM_ERROR' };

const initial: State = { threads: [], total: 0, loading: false };

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case 'LIST_START':
      return { ...state, loading: true, listError: undefined };
    case 'LIST_OK':
      // 整块替换而非合并：一次刷新就是「新的一屏」，任何残留的旧行都不该留下
      return {
        ...state, loading: false, listError: undefined,
        threads: action.threads, total: action.total,
      };
    case 'LIST_FAIL':
      return { ...state, loading: false, listError: action.error };
    case 'SET_TITLE':
      // 乐观改与回滚都走这一个 action（回滚 = 用旧标题再调一次），免得多一个 action 类型
      return {
        ...state, itemError: undefined,
        threads: state.threads.map(t =>
          t.thread_id === action.threadId ? { ...t, title: action.title } : t),
      };
    case 'REMOVE':
      return {
        ...state, itemError: undefined,
        threads: state.threads.filter(t => t.thread_id !== action.threadId),
      };
    case 'RESTORE': {
      const next = [...state.threads];
      // 按**原下标**插回而不是 push：否则删除失败回滚后，这一行会跳到列表末尾，
      // 看起来像「刚更新过」，与 updated_at 倒序的语义矛盾
      next.splice(action.index, 0, action.row);
      return { ...state, threads: next };
    }
    case 'ITEM_FAIL':
      return { ...state, itemError: { threadId: action.threadId, message: action.error } };
    case 'CLEAR_ITEM_ERROR':
      return { ...state, itemError: undefined };
  }
}

/**
 * 侧栏的会话列表（P3）。职责：拉列表、重命名、删除「非当前」会话，全部带乐观更新。
 *
 * **为什么「删当前会话」不在这里**：那条路径要走 `useChat.deleteChat` —— 它必须先
 * abort 在飞的 SSE、等流真正结束再发 DELETE，删成功后还要清空消息区。侧栏这一路没有
 * 消息区要清，硬塞进来会把「聊天状态」和「列表状态」搅在一起。两条路径由 App 分流，
 * 各自拿到 `boolean` 成功后统一 `refresh()` 校准 total。
 */
export function useConversations() {
  const [state, dispatch] = useReducer(reducer, initial);

  const refresh = useCallback(async () => {
    dispatch({ type: 'LIST_START' });
    try {
      const r = await fetchThreads();
      dispatch({ type: 'LIST_OK', threads: r.threads, total: r.total });
    } catch (e) {
      dispatch({ type: 'LIST_FAIL', error: e instanceof Error ? e.message : '会话列表加载失败' });
    }
  }, []);

  // 挂载即拉一次。用 useEffect 而非在 useState 初始化里发请求：后者在并发渲染下可能被执行两次。
  useEffect(() => { void refresh(); }, [refresh]);

  /** 重命名。乐观更新：先改本地，失败按**原标题**回滚并在该行提示后端 message。
   *  空标题 / 超 60 字由 ConversationItem 先拦，不走这里。 */
  const rename = useCallback(async (threadId: string, title: string): Promise<boolean> => {
    const old = state.threads.find(t => t.thread_id === threadId)?.title ?? '';
    dispatch({ type: 'SET_TITLE', threadId, title });
    try {
      await renameThread(threadId, title);
      return true;
    } catch (e) {
      dispatch({ type: 'SET_TITLE', threadId, title: old });
      dispatch({ type: 'ITEM_FAIL', threadId, error: e instanceof Error ? e.message : '重命名失败' });
      return false;
    }
  }, [state.threads]);

  /** 删除。乐观移除，失败按原下标插回。
   *  `deleteThread` 对未知 id 返回 `deleted:false` 也当成功（契约幂等），所以这里只 catch 真失败。 */
  const remove = useCallback(async (threadId: string): Promise<boolean> => {
    const index = state.threads.findIndex(t => t.thread_id === threadId);
    const row = index >= 0 ? state.threads[index] : undefined;
    dispatch({ type: 'REMOVE', threadId });
    try {
      await deleteThread(threadId);
      return true;
    } catch (e) {
      if (row) dispatch({ type: 'RESTORE', row, index });
      // 与 `useChat.deleteChat` 同一口径：删除失败**不把 `delete thread HTTP 500` 抛给用户看**。
      // 而 rename 走的是后端 message（403 的「该会话不属于当前用户」对用户是有意义的），
      // 两者刻意不同：删除的失败原因对用户没有可操作性，重命名的有。
      dispatch({ type: 'ITEM_FAIL', threadId,
                 error: e instanceof TypeError ? '无法连接后端' : '删除失败，请重试' });
      return false;
    }
  }, [state.threads]);

  const clearItemError = useCallback(() => dispatch({ type: 'CLEAR_ITEM_ERROR' }), []);

  return {
    threads: state.threads,
    total: state.total,
    loading: state.loading,
    /** 列表级错误（整块没加载出来） */
    error: state.listError,
    /** 行级错误（某条重命名/删除失败） */
    itemError: state.itemError,
    refresh, rename, remove, clearItemError,
  };
}
