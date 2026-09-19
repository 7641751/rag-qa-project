import type {
  ChatRequest, StreamHandlers, HistoryResponse, Health, ChatDeleteResponse,
  StepEvent, TokenEvent, SourcesEvent, DoneEvent, ErrorEvent, ErrorCode,
  KbStreamHandlers, KbProgressEvent, KbDoneEvent, KbListResponse, KbDeleteResponse,
  AuthUser, LoginRequest, LoginResponse, RegisterRequest,
  ThreadListResponse, RenameRequest, RenameResponse,
} from './types';
import { clearToken, getToken } from './tokenStore';

const API_BASE = '/api';

// ---------------------------------------------------------------- 鉴权
let onUnauthorized: (() => void) | null = null;

/** 注册「会话失效」回调（useAuth 挂载时注册 → token 过期自动回登录页，用户无需手动刷新）。
 *  传 `null` 注销（组件卸载时用）。 */
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  onUnauthorized = fn;
}

/** 当前应带的鉴权头。无 token 时返回**空对象**，而不是 `{ Authorization: 'Bearer null' }`。 */
export function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** 所有 fetch 的唯一出口：注入 Authorization + 集中识别 401。
 *
 *  **为什么必须集中**：改造前 6 处 fetch 各自拼 URL 与请求头，漏一处就是一个「带着过期
 *  token 静默失败」的 bug。收敛到这里后，401 处理只有一份实现（spec §8.2）。
 *
 *  **无 token 时连 `headers` 键都不写**（而不是写 `headers: {}`）：既不给公开端点塞无用头，
 *  也让 fetch 收到的 init 形状与改造前完全一致 —— 既有断言
 *  `expect(init).toEqual({ method: 'DELETE' })` 依赖这一点（`toEqual` 会忽略值为 undefined
 *  的属性，但 `{}` 会被算成差异）。
 *
 *  **401 的两义性**：登录接口用 401 表示「用户名或密码错」，那是业务失败、不是会话失效；
 *  只有**带着 token 仍返 401** 才算会话失效。所以下面用 token 是否非空来分流 —— 否则用户
 *  输错一次密码就会被当成「会话过期」，还会多清一次 token。
 */
async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const token = getToken();
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    ...(token ? { headers: { ...(init.headers ?? {}), Authorization: `Bearer ${token}` } } : {}),
  });
  if (res.status === 401 && token) {
    clearToken();
    onUnauthorized?.();
  }
  return res;
}

/** 非 2xx → 取后端的 `message` 直接抛出。登录页要把原文显示在错误行（spec §9 矩阵），
 *  所以这里不能吞成「登录失败」四个字。 */
async function errorText(res: Response, fallback: string): Promise<string> {
  const text = await res.text().catch(() => '');
  try {
    const j = JSON.parse(text);
    if (j.message) return j.message as string;
    if (j.code) return j.code as string;
  } catch { /* 非 JSON（如网关 502 的 HTML），落到下面的兜底文案 */ }
  return `${fallback}（HTTP ${res.status}）`;
}

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
  const res = await request('/chat/stream', {
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
  const res = await request('/kb/documents', {
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
  const res = await request('/kb/documents');
  if (!res.ok) throw new Error(`kb list HTTP ${res.status}`);
  return (await res.json()) as KbListResponse;
}

/** DELETE /api/kb/documents/{doc_id} → 删除结果 */
export async function deleteDocument(docId: string): Promise<KbDeleteResponse> {
  const res = await request(`/kb/documents/${encodeURIComponent(docId)}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`kb delete HTTP ${res.status}`);
  return (await res.json()) as KbDeleteResponse;
}

/** GET /api/chat/history?thread_id= → 历史消息 */
export async function fetchHistory(threadId: string): Promise<HistoryResponse> {
  const res = await request(`/chat/history?thread_id=${encodeURIComponent(threadId)}`);
  if (!res.ok) throw new Error(`history HTTP ${res.status}`);
  return (await res.json()) as HistoryResponse;
}

/** DELETE /api/chat/threads/{thread_id} → 删除该会话在服务端的全部 checkpoint。
 *  契约规定未知 id 也返 200（deleted:false 表示本来就没有），所以非 2xx 一律是真失败，
 *  直接 throw 交给调用方（useChat.deleteChat 会转成中文提示）。 */
export async function deleteThread(threadId: string): Promise<ChatDeleteResponse> {
  const res = await request(`/chat/threads/${encodeURIComponent(threadId)}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`delete thread HTTP ${res.status}`);
  return (await res.json()) as ChatDeleteResponse;
}

/** GET /api/health → 健康状态（公开端点，但也走 request()：接线只有一处） */
export async function fetchHealth(): Promise<Health> {
  const res = await request('/health');
  if (!res.ok) throw new Error(`health HTTP ${res.status}`);
  return (await res.json()) as Health;
}

// ---------------------------------------------------------------- 鉴权端点

/** POST /api/auth/register → 新用户（201）。重名时抛的 message 来自后端（409 USERNAME_TAKEN）。 */
export async function register(body: RegisterRequest): Promise<AuthUser> {
  const res = await request('/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await errorText(res, '注册失败'));
  return (await res.json()) as AuthUser;
}

/** POST /api/auth/login → token + user。
 *  失败时抛后端的 message（401，后端故意不区分「用户不存在」与「密码错」）。 */
export async function login(body: LoginRequest): Promise<LoginResponse> {
  const res = await request('/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await errorText(res, '登录失败'));
  return (await res.json()) as LoginResponse;
}

/** GET /api/auth/me → 校验本地 token 是否仍然有效。
 *
 *  **启动时必须调它**，而不是直接信任 localStorage：token 可能已过期、被改过、或后端换了
 *  secret。不验证就会出现「界面显示已登录、但每个请求都 401」的错位状态（spec §8.3）。 */
export async function fetchMe(): Promise<AuthUser> {
  const res = await request('/auth/me');
  if (!res.ok) throw new Error(await errorText(res, '登录状态校验失败'));
  return (await res.json()) as AuthUser;
}

// ---------------------------------------------------------------- 会话列表（P3）

/** GET /api/chat/threads → 当前用户的会话，按 updated_at 倒序、最多 50 条。
 *  空列表返回 `200 {threads:[], total:0}`（不是 404），所以这里不把空当异常。 */
export async function fetchThreads(): Promise<ThreadListResponse> {
  const res = await request('/chat/threads');
  if (!res.ok) throw new Error(`threads HTTP ${res.status}`);
  return (await res.json()) as ThreadListResponse;
}

/** PATCH /api/chat/threads/{thread_id} → 重命名。
 *  非本人 → 403、不存在 → 404、标题空或超 60 字 → 422；三者都抛后端给的 message，
 *  调用方据此决定「回滚 + 行内提示」还是「从列表移除」。
 *  用 PATCH 而非 PUT：只改 title 一个字段，语义上是部分更新。 */
export async function renameThread(threadId: string, title: string): Promise<RenameResponse> {
  const res = await request(`/chat/threads/${encodeURIComponent(threadId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title } satisfies RenameRequest),
  });
  if (!res.ok) throw new Error(await errorText(res, '重命名失败'));
  return (await res.json()) as RenameResponse;
}
