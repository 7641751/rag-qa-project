import { useCallback, useEffect, useReducer, useRef } from 'react';
import type { KbBuiltinSummary, KbDocument, KbStage } from '../api/types';
import { deleteDocument, fetchDocuments, uploadDocument } from '../api/client';
import { genId } from './useThreadId';

export type UploadStatus = 'pending' | 'uploading' | 'done' | 'error';

export interface UploadItem {
  id: string;                 // 前端本地 id（uuid），≠ 后端 doc_id
  file: File;
  status: UploadStatus;
  stage?: KbStage;
  current?: number;
  total?: number;
  message?: string;
  error?: string;
  cancelled?: boolean;        // 用户主动取消：UI 用灰色区别于红色失败
  docId?: string;             // done 后填入
  replaced?: boolean;
}

interface State {
  open: boolean;
  documents: KbDocument[];
  builtin?: KbBuiltinSummary;
  uploads: UploadItem[];
  loadingList: boolean;
  listError?: string;
  docError?: string;
}

type Action =
  | { type: 'OPEN' }
  | { type: 'CLOSE' }
  | { type: 'LIST_START' }
  | { type: 'LIST_OK'; documents: KbDocument[]; builtin?: KbBuiltinSummary }
  | { type: 'LIST_FAIL'; error: string }
  | { type: 'ENQUEUE'; items: UploadItem[] }
  | { type: 'UPLOAD_START'; id: string }
  | { type: 'PROGRESS'; id: string; stage: KbStage; current?: number | null; total?: number | null; message?: string }
  | { type: 'UPLOAD_DONE'; id: string; docId: string; replaced: boolean }
  | { type: 'UPLOAD_FAIL'; id: string; error: string; cancelled?: boolean }
  | { type: 'DISMISS'; id: string }
  | { type: 'REMOVE_DOC'; docId: string }
  | { type: 'RESTORE_DOC'; doc: KbDocument; index: number }
  | { type: 'DOC_FAIL'; error: string };

export const ALLOWED_EXT = ['.md', '.txt', '.pdf'];
export const MAX_BYTES = 20 * 1024 * 1024;      // 20 MB，对齐契约 413
export const MAX_FILES_PER_PICK = 5;            // 一次最多选 5 个，串行上传

function validate(file: File): string | null {
  const lower = file.name.toLowerCase();
  if (!ALLOWED_EXT.some(ext => lower.endsWith(ext))) return `不支持的格式，仅 ${ALLOWED_EXT.join(' / ')}`;
  if (file.size > MAX_BYTES) return `超过 ${MAX_BYTES / 1024 / 1024} MB`;
  return null;
}

/** stage → 百分比；null 表示不定进度（渲染条纹动画），避免假百分比 */
export function toPercent(u: UploadItem): number | null {
  if (u.status === 'done') return 100;
  if (u.status === 'error') return null;
  if (u.stage === 'embedding' && u.current != null && u.total) {
    return Math.round(20 + 80 * (u.current / u.total));
  }
  if (u.stage === 'split') return 20;
  if (u.stage === 'parsed') return 12;
  if (u.stage === 'saved') return 6;
  return null;
}

const initial: State = { open: false, documents: [], uploads: [], loadingList: false };

function reducer(state: State, action: Action): State {
  const patch = (id: string, p: Partial<UploadItem>) =>
    state.uploads.map(u => (u.id === id ? { ...u, ...p } : u));
  switch (action.type) {
    case 'OPEN': return { ...state, open: true };
    case 'CLOSE': return { ...state, open: false };
    case 'LIST_START': return { ...state, loadingList: true, listError: undefined };
    case 'LIST_OK': return {
      ...state, loadingList: false, listError: undefined,
      documents: action.documents, builtin: action.builtin,
    };
    case 'LIST_FAIL': return { ...state, loadingList: false, listError: action.error };
    case 'ENQUEUE': return { ...state, uploads: [...state.uploads, ...action.items] };
    case 'UPLOAD_START': return { ...state, uploads: patch(action.id, { status: 'uploading' }) };
    case 'PROGRESS': return {
      ...state,
      uploads: patch(action.id, {
        stage: action.stage,
        current: action.current ?? undefined,
        total: action.total ?? undefined,
        message: action.message,
      }),
    };
    case 'UPLOAD_DONE': return {
      ...state,
      uploads: patch(action.id, { status: 'done', docId: action.docId, replaced: action.replaced, message: undefined }),
    };
    case 'UPLOAD_FAIL': return {
      ...state,
      uploads: patch(action.id, { status: 'error', error: action.error, cancelled: action.cancelled }),
    };
    case 'DISMISS': return { ...state, uploads: state.uploads.filter(u => u.id !== action.id) };
    case 'REMOVE_DOC': return {
      ...state, docError: undefined,
      documents: state.documents.filter(d => d.doc_id !== action.docId),
    };
    case 'RESTORE_DOC': {
      const next = [...state.documents];
      next.splice(action.index, 0, action.doc);
      return { ...state, documents: next };
    }
    case 'DOC_FAIL': return { ...state, docError: action.error };
  }
}

export function useKnowledgeBase() {
  const [state, dispatch] = useReducer(reducer, initial);

  const queue = useRef<string[]>([]);                      // 待传 item id
  const files = useRef<Map<string, File>>(new Map());      // id → File（pump 用，避开闭包读旧 state）
  const controllers = useRef<Map<string, AbortController>>(new Map());
  const pumping = useRef(false);

  const refresh = useCallback(async () => {
    dispatch({ type: 'LIST_START' });
    try {
      const r = await fetchDocuments();
      dispatch({ type: 'LIST_OK', documents: r.documents, builtin: r.builtin });
    } catch (e) {
      dispatch({ type: 'LIST_FAIL', error: e instanceof Error ? e.message : '列表加载失败' });
    }
  }, []);

  /** 串行泵：同一时刻只有一个上传在跑；单个失败不阻断后续 */
  const pump = useCallback(async () => {
    if (pumping.current) return;
    pumping.current = true;
    try {
      for (;;) {
        const id = queue.current.shift();
        if (!id) break;
        const file = files.current.get(id);
        if (!file) continue;                  // 已被 cancelUpload 摘除
        dispatch({ type: 'UPLOAD_START', id });
        const ctrl = new AbortController();
        controllers.current.set(id, ctrl);
        let settled = false;                  // onDone / onError 是否已回调
        try {
          await uploadDocument(file, {
            signal: ctrl.signal,
            onProgress: e => dispatch({
              type: 'PROGRESS', id, stage: e.stage,
              current: e.current, total: e.total, message: e.message,
            }),
            onDone: e => {
              settled = true;
              dispatch({ type: 'UPLOAD_DONE', id, docId: e.doc_id, replaced: e.replaced });
              void refresh();                 // 列表以后端为准，重拉一次
            },
            onError: e => { settled = true; dispatch({ type: 'UPLOAD_FAIL', id, error: e.message }); },
          });
          // 契约 §4.3：流在无 done 也无 error 的情况下关闭（异常断连）→ 按中断处理。
          // 不补这一手，条目会永远卡在 uploading、Header 角标也下不去。
          if (!settled && !ctrl.signal.aborted) {
            dispatch({ type: 'UPLOAD_FAIL', id, error: '连接中断，请重试' });
          }
        } catch (err) {
          // abort 时 fetch 会 reject（AbortError）而不走 onError，靠 signal.aborted 区分
          const cancelled = ctrl.signal.aborted;
          dispatch({
            type: 'UPLOAD_FAIL', id, cancelled,
            error: cancelled ? '已取消' : (err instanceof Error ? err.message : '网络中断，请重试'),
          });
        } finally {
          controllers.current.delete(id);
          files.current.delete(id);
        }
      }
    } finally {
      pumping.current = false;
    }
  }, [refresh]);

  const addFiles = useCallback((list: File[]) => {
    const accepted = list.slice(0, MAX_FILES_PER_PICK);
    const overflow = list.slice(MAX_FILES_PER_PICK);
    const items: UploadItem[] = [];
    for (const f of accepted) {
      const id = genId();
      const bad = validate(f);
      if (bad) {
        items.push({ id, file: f, status: 'error', error: bad });   // 不发请求，但让用户看见原因
      } else {
        files.current.set(id, f);
        queue.current.push(id);
        items.push({ id, file: f, status: 'pending' });
      }
    }
    for (const f of overflow) {
      items.push({ id: genId(), file: f, status: 'error', error: `一次最多上传 ${MAX_FILES_PER_PICK} 个文件` });
    }
    dispatch({ type: 'ENQUEUE', items });
    void pump();
  }, [pump]);

  const cancelUpload = useCallback((id: string) => {
    const running = controllers.current.get(id);
    if (running) { running.abort(); return; }        // 进行中的交给 catch 分支标记
    const qi = queue.current.indexOf(id);            // 尚未开始：直接从队列摘除
    if (qi >= 0) {
      queue.current.splice(qi, 1);
      files.current.delete(id);
      dispatch({ type: 'UPLOAD_FAIL', id, error: '已取消', cancelled: true });
    }
  }, []);

  const removeDoc = useCallback(async (docId: string) => {
    const index = state.documents.findIndex(d => d.doc_id === docId);
    const doc = index >= 0 ? state.documents[index] : undefined;
    dispatch({ type: 'REMOVE_DOC', docId });         // 乐观更新
    try {
      await deleteDocument(docId);
    } catch (e) {
      if (doc) dispatch({ type: 'RESTORE_DOC', doc, index });      // 失败回滚
      dispatch({ type: 'DOC_FAIL', error: e instanceof Error ? e.message : '删除失败' });
    }
  }, [state.documents]);

  const dismiss = useCallback((id: string) => dispatch({ type: 'DISMISS', id }), []);
  const openKb = useCallback(() => { dispatch({ type: 'OPEN' }); void refresh(); }, [refresh]);
  const closeKb = useCallback(() => dispatch({ type: 'CLOSE' }), []);

  useEffect(() => { void refresh(); }, [refresh]);

  const activeCount = state.uploads.filter(u => u.status === 'pending' || u.status === 'uploading').length;

  return {
    open: state.open,
    documents: state.documents,
    builtin: state.builtin,
    uploads: state.uploads,
    loadingList: state.loadingList,
    listError: state.listError,
    docError: state.docError,
    activeCount,
    openKb, closeKb, refresh, addFiles, cancelUpload, removeDoc, dismiss,
  };
}
