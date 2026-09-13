import { useCallback, useEffect, useReducer, useRef } from 'react';
import type { Step, Source, ErrorEvent } from '../api/types';
import { streamChat, fetchHistory } from '../api/client';
import { useThreadId } from './useThreadId';

export interface UserMsg { role: 'user'; content: string; }
export interface AssistantMsg {
  role: 'assistant'; steps: Step[]; answer: string; sources: Source[];
  status: 'streaming' | 'done' | 'error'; error?: string;
  /** false = 本轮未命中知识库（答案来自模型通用知识）；streaming 期间为 undefined */
  grounded?: boolean;
}
export type Msg = UserMsg | AssistantMsg;

type State = { messages: Msg[]; streaming: boolean };
type Action =
  | { type: 'PUSH_USER'; content: string }
  | { type: 'START_ASSISTANT' }
  | { type: 'ADD_STEP'; step: Step }
  | { type: 'APPEND_TOKEN'; text: string }
  | { type: 'SET_SOURCES'; sources: Source[] }
  | { type: 'FINISH'; grounded?: boolean }
  | { type: 'FAIL'; error: string }
  | { type: 'LOAD_HISTORY'; messages: Msg[] }
  | { type: 'CLEAR' };

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
      return state.messages.length === 0 ? { messages: action.messages, streaming: false } : state;
    case 'CLEAR':
      return { messages: [], streaming: false };
  }
}

export function useChat() {
  const [state, dispatch] = useReducer(reducer, { messages: [], streaming: false });
  const { threadId, resetThreadId } = useThreadId();
  const abortRef = useRef<AbortController | null>(null);

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
    void streamChat({ question: q, thread_id: threadId }, {
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
  }, [state.streaming, threadId]);

  const abort = useCallback(() => { abortRef.current?.abort(); }, []);
  const newChat = useCallback(() => {
    abortRef.current?.abort();
    dispatch({ type: 'CLEAR' });
    resetThreadId();
  }, [resetThreadId]);

  return { messages: state.messages, streaming: state.streaming, send, abort, newChat, threadId };
}
