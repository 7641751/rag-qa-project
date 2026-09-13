import type {
  ChatRequest, StreamHandlers, HistoryResponse, Health,
  StepEvent, TokenEvent, SourcesEvent, DoneEvent, ErrorEvent, ErrorCode,
  KbStreamHandlers, KbProgressEvent, KbDoneEvent, KbListResponse, KbDeleteResponse,
} from './types';

const API_BASE = '/api';

/** 解析一帧 SSE 文本(可能含多行 event:/data:) → { event, data }；无 data 返回 null */
export function parseSSEFrame(frame: string): { event: string; data: string } | null {
  let event = 'message';
  const dataLines: string[] = [];
  for (const raw of frame.split('\n')) {
    const line = raw.replace(/\r$/, '');
    if (!line || line.startsWith(':')) continue;
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''));
  }
  if (dataLines.length === 0) return null;
  return { event, data: dataLines.join('\n') };
}

/** 把 buffer 按 \n\n 切成完整帧，返回 [frames, rest]（rest 为残帧，留到下次） */
export function splitFrames(buffer: string): { frames: string[]; rest: string } {
  const parts = buffer.split('\n\n');
  const rest = parts.pop() ?? '';
  return { frames: parts.filter(f => f.trim().length > 0), rest };
}

/** 读 SSE 流：按 \n\n 切帧回调 dispatch，处理粘包/半包，收尾 flush 残帧。
 *  streamChat 与 uploadDocument 共用（原逻辑已被测试覆盖，提炼不改行为）。 */
async function readSSE(res: Response, dispatch: (frame: string) => void): Promise<void> {
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const { frames, rest } = splitFrames(buffer);
    buffer = rest;
    for (const f of frames) dispatch(f);
  }
  buffer += decoder.decode();
  if (buffer.trim()) for (const f of splitFrames(buffer + '\n\n').frames) dispatch(f);
}

/** 非 2xx → 解析 {code,message} → onError；解析失败回退 INTERNAL_ERROR + `HTTP <status>` */
async function emitHttpError(res: Response, onError?: (e: ErrorEvent) => void): Promise<void> {
  const text = await res.text().catch(() => '');
  let code: ErrorCode = 'INTERNAL_ERROR';
  let message = `HTTP ${res.status}`;
  try {
    const j = JSON.parse(text);
    if (j.code) code = j.code as ErrorCode;
    if (j.message) message = j.message as string;
  } catch { /* 非 JSON 错误体，沿用默认 */ }
  onError?.({ code, message });
}

/** POST /api/chat/stream 并逐事件回调（signal 用于「停止生成」） */
export async function streamChat(
  body: ChatRequest,
  handlers: StreamHandlers & { signal?: AbortSignal },
): Promise<void> {
  const res = await fetch(`${API_BASE}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(body),
    signal: handlers.signal,
  });
  if (!res.ok) { await emitHttpError(res, handlers.onError); return; }
  if (!res.body) { handlers.onError?.({ code: 'INTERNAL_ERROR', message: '响应无 body' }); return; }

  const dispatch = (frame: string) => {
    const parsed = parseSSEFrame(frame);
    if (!parsed) return;
    let data: unknown;
    try { data = JSON.parse(parsed.data); } catch { return; }
    switch (parsed.event) {
      case 'step': handlers.onStep?.(data as StepEvent); break;
      case 'token': handlers.onToken?.(data as TokenEvent); break;
      case 'sources': handlers.onSources?.(data as SourcesEvent); break;
      case 'done': handlers.onDone?.(data as DoneEvent); break;
      case 'error': handlers.onError?.(data as ErrorEvent); break;
      default: break;
    }
  };
  await readSSE(res, dispatch);
}

/** POST /api/kb/documents（multipart）并逐事件回调。
 *  ⚠ 不要手动设 Content-Type，否则丢失 multipart boundary。
 *  ⚠ abort 时 fetch 会 **reject**（AbortError）而非返回非 2xx，
 *    调用方必须 try/catch 并用 signal.aborted 区分「用户取消」与「真失败」。 */
export async function uploadDocument(
  file: File,
  handlers: KbStreamHandlers & { signal?: AbortSignal },
): Promise<void> {
  const form = new FormData();
  form.append('file', file);
  const res = await fetch(`${API_BASE}/kb/documents`, {
    method: 'POST',
    headers: { Accept: 'text/event-stream' },
    body: form,
    signal: handlers.signal,
  });
  if (!res.ok) { await emitHttpError(res, handlers.onError); return; }
  if (!res.body) { handlers.onError?.({ code: 'INTERNAL_ERROR', message: '响应无 body' }); return; }

  const dispatch = (frame: string) => {
    const parsed = parseSSEFrame(frame);
    if (!parsed) return;
    let data: unknown;
    try { data = JSON.parse(parsed.data); } catch { return; }
    switch (parsed.event) {
      case 'progress': handlers.onProgress?.(data as KbProgressEvent); break;
      case 'done': handlers.onDone?.(data as KbDoneEvent); break;
      case 'error': handlers.onError?.(data as ErrorEvent); break;
      default: break;
    }
  };
  await readSSE(res, dispatch);
}

/** GET /api/kb/documents → 上传文档列表 + 可选预置摘要 */
export async function fetchDocuments(): Promise<KbListResponse> {
  const res = await fetch(`${API_BASE}/kb/documents`);
  if (!res.ok) throw new Error(`kb list HTTP ${res.status}`);
  return (await res.json()) as KbListResponse;
}

/** DELETE /api/kb/documents/{doc_id} → 删除结果 */
export async function deleteDocument(docId: string): Promise<KbDeleteResponse> {
  const res = await fetch(`${API_BASE}/kb/documents/${encodeURIComponent(docId)}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`kb delete HTTP ${res.status}`);
  return (await res.json()) as KbDeleteResponse;
}

/** GET /api/chat/history?thread_id= → 历史消息 */
export async function fetchHistory(threadId: string): Promise<HistoryResponse> {
  const res = await fetch(`${API_BASE}/chat/history?thread_id=${encodeURIComponent(threadId)}`);
  if (!res.ok) throw new Error(`history HTTP ${res.status}`);
  return (await res.json()) as HistoryResponse;
}

/** GET /api/health → 健康状态 */
export async function fetchHealth(): Promise<Health> {
  const res = await fetch(`${API_BASE}/health`);
  if (!res.ok) throw new Error(`health HTTP ${res.status}`);
  return (await res.json()) as Health;
}
