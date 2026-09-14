import { useCallback, useEffect, useReducer, useRef } from 'react';
import type { Step, Source, ErrorEvent } from '../api/types';
import { streamChat, fetchHistory, deleteThread } from '../api/client';
import { useThreadId } from './useThreadId';

export interface UserMsg { role: 'user'; content: string; }
export interface AssistantMsg {
  role: 'assistant'; steps: Step[]; answer: string; sources: Source[];
  status: 'streaming' | 'done' | 'error'; error?: string;
  /** false = 本轮未命中知识库（答案来自模型通用知识）；streaming 期间为 undefined */
  grounded?: boolean;
}
export type Msg = UserMsg | AssistantMsg;

type State = {
  messages: Msg[];
  streaming: boolean;
  /** 删除请求在飞：弹窗据此禁用按钮、忽略 Esc 与遮罩 */
  deleting: boolean;
  /** 删除失败的中文提示；成功或重新打开弹窗时清空 */
  deleteError: string | null;
};

type Action =
  | { type: 'PUSH_USER'; content: string }
  | { type: 'START_ASSISTANT' }
  | { type: 'ADD_STEP'; step: Step }
  | { type: 'APPEND_TOKEN'; text: string }
  | { type: 'SET_SOURCES'; sources: Source[] }
  | { type: 'FINISH'; grounded?: boolean }
  | { type: 'FAIL'; error: string }
  | { type: 'LOAD_HISTORY'; messages: Msg[] }
  | { type: 'CLEAR' }
  | { type: 'DELETE_START' }
  | { type: 'DELETE_OK' }
  | { type: 'DELETE_FAIL'; error: string }
  | { type: 'CLEAR_DELETE_ERROR' };

function updateLastAssistant(msgs: Msg[], fn: (a: AssistantMsg) => AssistantMsg): Msg[] {
  const out = msgs.slice();
  for (let i = out.length - 1; i >= 0; i--) {
    const m = out[i];
    if (m.role === 'assistant') { out[i] = fn(m); break; }
  }
  return out;
}

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case 'PUSH_USER':
      return { ...state, messages: [...state.messages, { role: 'user', content: action.content }] };
    case 'START_ASSISTANT':
      return { ...state, streaming: true, messages: [...state.messages, { role: 'assistant', steps: [], answer: '', sources: [], status: 'streaming' }] };
    case 'ADD_STEP':
      return { ...state, messages: updateLastAssistant(state.messages, a => ({ ...a, steps: [...a.steps, action.step] })) };
    case 'APPEND_TOKEN':
      return { ...state, messages: updateLastAssistant(state.messages, a => ({ ...a, answer: a.answer + action.text })) };
    case 'SET_SOURCES':
      return { ...state, messages: updateLastAssistant(state.messages, a => ({ ...a, sources: action.sources })) };
    case 'FINISH':
      // grounded 只在 done 事件里给；abort 路径不传，用 ?? 保住已有值不被 undefined 覆盖
      return { ...state, streaming: false, messages: updateLastAssistant(state.messages, a => ({ ...a, status: 'done', grounded: action.grounded ?? a.grounded })) };
    case 'FAIL':
      return { ...state, streaming: false, messages: updateLastAssistant(state.messages, a => ({ ...a, status: 'error', error: action.error })) };
    case 'LOAD_HISTORY':
      // 仅当当前无消息时回填：避免异步历史冲掉用户刚发起的对话，也优雅处理 StrictMode 双调用
      return state.messages.length === 0 ? { ...state, messages: action.messages, streaming: false } : state;
    case 'CLEAR':
      // ⚠ 必须 ...state：State 新增了 deleting/deleteError，漏掉会 TS 报缺字段
      return { ...state, messages: [], streaming: false };
    case 'DELETE_START':
      return { ...state, deleting: true, deleteError: null };
    case 'DELETE_OK':
      // 悲观时序（spec 决策 9）：只有后端确认成功才清空消息
      return { ...state, messages: [], streaming: false, deleting: false, deleteError: null };
    case 'DELETE_FAIL':
      // 失败保留 messages：删除不可逆，宁可让用户看到原样并重试
      return { ...state, deleting: false, deleteError: action.error };
    case 'CLEAR_DELETE_ERROR':
      return { ...state, deleteError: null };
  }
}

export function useChat() {
  const [state, dispatch] = useReducer(reducer, {
    messages: [], streaming: false, deleting: false, deleteError: null,
  });
  const { threadId, resetThreadId } = useThreadId();
  const abortRef = useRef<AbortController | null>(null);
  /** 在飞的 SSE promise。deleteChat 必须先 await 它再发 DELETE：否则被取消的 astream
   *  可能在 DELETE 之后才写入 checkpoint，留下删不掉的残行（spec §4）。 */
  const streamRef = useRef<Promise<void> | null>(null);

  useEffect(() => {
    let alive = true;
    fetchHistory(threadId)
      .then(h => {
        if (!alive) return;
        const msgs: Msg[] = h.messages.map(m =>
          m.role === 'user'
            ? { role: 'user', content: m.content }
            : { role: 'assistant', steps: [], answer: m.content, sources: m.sources ?? [], status: 'done',
                // 刷新后靠它恢复警示标识；后端未持久化时为 null → 转成 undefined 不渲染
                grounded: m.grounded ?? undefined },
        );
        dispatch({ type: 'LOAD_HISTORY', messages: msgs });
      })
      .catch(() => { /* 新会话或后端未就绪：静默 */ });
    return () => { alive = false; };
  }, [threadId]);

  const send = useCallback((question: string) => {
    const q = question.trim();
    if (!q || state.streaming) return;
    dispatch({ type: 'PUSH_USER', content: q });
    dispatch({ type: 'START_ASSISTANT' });
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    // catch 已消化所有异常（AbortError → FINISH，其余 → FAIL），故 pending 永不 reject，
    // deleteChat 里 await 它不会抛
    const pending: Promise<void> = streamChat({ question: q, thread_id: threadId }, {
      signal: ctrl.signal,
      onStep: e => dispatch({ type: 'ADD_STEP', step: e }),
      onToken: e => dispatch({ type: 'APPEND_TOKEN', text: e.text }),
      onSources: e => dispatch({ type: 'SET_SOURCES', sources: e.sources }),
      onDone: e => dispatch({ type: 'FINISH', grounded: e.grounded }),
      onError: (e: ErrorEvent) => dispatch({ type: 'FAIL', error: e.message }),
    }).catch((err: unknown) => {
      if ((err as Error)?.name === 'AbortError') dispatch({ type: 'FINISH' });
      else dispatch({ type: 'FAIL', error: '无法连接后端' });
    });
    streamRef.current = pending;
    void pending.finally(() => {
      // 只在还是自己时清空：避免旧流晚 settle 把新流的 ref 抹掉
      if (streamRef.current === pending) streamRef.current = null;
    });
  }, [state.streaming, threadId]);

  const abort = useCallback(() => { abortRef.current?.abort(); }, []);

  const newChat = useCallback(() => {
    abortRef.current?.abort();
    dispatch({ type: 'CLEAR' });
    resetThreadId();
  }, [resetThreadId]);

  /** 删除当前对话。返回 true = 后端已确认删除（App 据此决定关不关弹窗）。
   *  与 newChat 语义正交：newChat 换 thread_id 且不动服务端；
   *  deleteChat 清服务端且**保留** thread_id（spec 决策 8）。 */
  const deleteChat = useCallback(async (): Promise<boolean> => {
    dispatch({ type: 'DELETE_START' });
    abortRef.current?.abort();
    // 硬约束：先等在飞的流真正结束，再发 DELETE（spec 决策 7）
    const pending = streamRef.current;
    if (pending) { try { await pending; } catch { /* send() 内已消化 */ } }
    try {
      await deleteThread(threadId);      // deleted:false 同样算成功（幂等）
      dispatch({ type: 'DELETE_OK' });
      return true;
    } catch (err) {
      // fetch 网络层失败抛 TypeError；HTTP 非 2xx 由 deleteThread 抛 Error
      dispatch({ type: 'DELETE_FAIL',
                 error: err instanceof TypeError ? '无法连接后端' : '删除失败，请重试' });
      return false;
    }
  }, [threadId]);

  const clearDeleteError = useCallback(() => dispatch({ type: 'CLEAR_DELETE_ERROR' }), []);

  return {
    messages: state.messages, streaming: state.streaming,
    deleting: state.deleting, deleteError: state.deleteError,
    send, abort, newChat, deleteChat, clearDeleteError, threadId,
  };
}
