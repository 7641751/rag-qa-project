import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  parseSSEFrame, splitFrames, streamChat, uploadDocument, fetchDocuments, deleteDocument,
  deleteThread, fetchHistory, authHeaders, setUnauthorizedHandler,
  fetchThreads, renameThread,
} from '../api/client';
import { getToken, setToken } from '../api/tokenStore';
import type { StepEvent, TokenEvent, DoneEvent, ErrorEvent, KbProgressEvent, KbDoneEvent } from '../api/types';

function mockStream(chunks: string[], init: { ok?: boolean; status?: number; body?: string } = {}) {
  const enc = new TextEncoder();
  let i = 0;
  const res = {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    text: async () => init.body ?? '',
    body: {
      getReader: () => ({
        read: async () =>
          i < chunks.length
            ? { value: enc.encode(chunks[i++]), done: false }
            : { value: undefined, done: true },
      }),
    },
  };
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(res as unknown as Response));
}

beforeEach(() => {
  vi.unstubAllGlobals();
  // 鉴权状态必须逐用例归零：token 存在会改变 request() 注入的 init 形状，
  // 泄漏到后面的用例会让「无 token」相关断言变成假通过。
  localStorage.clear();
  setUnauthorizedHandler(null);
});

describe('parseSSEFrame', () => {
  it('解析单帧 event+data', () => {
    expect(parseSSEFrame('event: token\ndata: {"text":"hi"}')).toEqual({ event: 'token', data: '{"text":"hi"}' });
  });
  it('多行 data 拼接', () => {
    expect(parseSSEFrame('data: a\ndata: b')!.data).toBe('a\nb');
  });
  it('无 data 返回 null', () => {
    expect(parseSSEFrame('event: ping')).toBeNull();
  });
});

describe('splitFrames', () => {
  it('粘包: 一个 buffer 两帧', () => {
    const { frames, rest } = splitFrames('event: a\ndata: 1\n\nevent: b\ndata: 2\n\n');
    expect(frames).toHaveLength(2);
    expect(rest).toBe('');
  });
  it('半包: 残帧留在 rest', () => {
    const { frames, rest } = splitFrames('event: a\ndata: 1\n\nevent: b\nda');
    expect(frames).toHaveLength(1);
    expect(rest).toBe('event: b\nda');
  });
});

describe('streamChat', () => {
  it('跨 chunk 粘包/半包仍能按序回调', async () => {
    mockStream([
      'event: step\ndata: {"node":"retrieve","la',
      'bel":"检索"}\n\nevent: token\ndata: {"text":"你"}\n\n',
      'event: done\ndata: {"thread_id":"t1","rewrites":0,"grounded":true}\n\n',
    ]);
    const steps: StepEvent[] = []; const tokens: TokenEvent[] = []; let done: DoneEvent | null = null;
    await streamChat({ question: 'q', thread_id: 't1' }, {
      onStep: e => steps.push(e), onToken: e => tokens.push(e), onDone: e => { done = e; },
    });
    expect(steps).toEqual([{ node: 'retrieve', label: '检索' }]);
    expect(tokens).toEqual([{ text: '你' }]);
    expect(done).toEqual({ thread_id: 't1', rewrites: 0, grounded: true });
  });
  it('error 事件回调 onError', async () => {
    mockStream(['event: error\ndata: {"code":"LLM_ERROR","message":"boom"}\n\n']);
    let err: ErrorEvent | null = null;
    await streamChat({ question: 'q', thread_id: 't' }, { onError: e => { err = e; } });
    expect(err).toEqual({ code: 'LLM_ERROR', message: 'boom' });
  });
  it('非 2xx 响应转 onError', async () => {
    mockStream([], { ok: false, status: 503 });
    const errs: ErrorEvent[] = [];
    await streamChat({ question: 'q', thread_id: 't' }, { onError: e => errs.push(e) });
    expect(errs[0]?.code).toBeTruthy();
    expect(errs[0]?.message).toContain('503');
  });
});

describe('uploadDocument', () => {
  const mdFile = () => new File(['# hi'], 'a.md', { type: 'text/markdown' });

  it('收 progress×N + done', async () => {
    mockStream([
      'event: progress\ndata: {"stage":"saved","message":"已接收 a.md"}\n\n',
      'event: progress\ndata: {"stage":"embedding","current":5,"total":10,"message":"嵌入 5/10"}\n\n',
      'event: done\ndata: {"doc_id":"d1","filename":"a.md","chunks":10,"replaced":false,"uploaded_at":"2026-09-11T10:00:00Z"}\n\n',
    ]);
    const prog: KbProgressEvent[] = []; const dones: KbDoneEvent[] = [];
    await uploadDocument(mdFile(), { onProgress: e => prog.push(e), onDone: e => dones.push(e) });
    expect(prog).toHaveLength(2);
    expect(prog[0]?.stage).toBe('saved');
    expect(prog[1]?.current).toBe(5);
    expect(prog[1]?.total).toBe(10);
    expect(dones[0]).toEqual({ doc_id: 'd1', filename: 'a.md', chunks: 10, replaced: false, uploaded_at: '2026-09-11T10:00:00Z' });
  });

  it('跨 chunk 半包仍能解析（证明复用了 readSSE）', async () => {
    mockStream([
      'event: progress\ndata: {"sta',
      'ge":"split","current":7,"total":7}\n\nevent: done\ndata: {"doc_id":"d2","filename":"b.txt","chunks":7,"replaced":true,"uploaded_at":"2026-09-11T10:00:01Z"}\n\n',
    ]);
    const prog: KbProgressEvent[] = []; const dones: KbDoneEvent[] = [];
    await uploadDocument(mdFile(), { onProgress: e => prog.push(e), onDone: e => dones.push(e) });
    expect(prog[0]?.stage).toBe('split');
    expect(dones[0]?.replaced).toBe(true);
  });

  it('415 转 onError(UNSUPPORTED_FILE_TYPE)', async () => {
    mockStream([], { ok: false, status: 415, body: '{"code":"UNSUPPORTED_FILE_TYPE","message":"仅支持 md/txt/pdf"}' });
    const errs: ErrorEvent[] = [];
    await uploadDocument(new File(['x'], 'a.exe'), { onError: e => errs.push(e) });
    expect(errs[0]?.code).toBe('UNSUPPORTED_FILE_TYPE');
    expect(errs[0]?.message).toContain('md/txt/pdf');
  });

  it('流中 error 事件转 onError', async () => {
    mockStream(['event: error\ndata: {"code":"EMPTY_DOCUMENT","message":"解析后无有效文本"}\n\n']);
    const errs: ErrorEvent[] = [];
    await uploadDocument(mdFile(), { onError: e => errs.push(e) });
    expect(errs[0]?.code).toBe('EMPTY_DOCUMENT');
  });
});

describe('fetchDocuments / deleteDocument', () => {
  it('fetchDocuments 返回列表与可选 builtin', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        documents: [{ doc_id: 'd1', filename: 'a.md', chunks: 3, uploaded_at: '2026-09-11T10:00:00Z' }],
        builtin: { docs: 88, chunks: 2885 },
      }),
    } as unknown as Response));
    const r = await fetchDocuments();
    expect(r.documents).toHaveLength(1);
    expect(r.documents[0]?.filename).toBe('a.md');
    expect(r.builtin?.docs).toBe(88);
  });

  it('deleteDocument 非 2xx 抛错', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 404 } as unknown as Response));
    await expect(deleteDocument('nope')).rejects.toThrow(/404/);
  });
});

// ============================ 鉴权：Authorization 注入与 401 集中处理 ============================
describe('authHeaders / 401', () => {
  it('有 token 返回 Bearer 头，无 token 返回空对象（不是 Bearer null）', () => {
    expect(authHeaders()).toEqual({});

    setToken('tk-1');

    expect(authHeaders()).toEqual({ Authorization: 'Bearer tk-1' });
  });

  it('鉴权头只在实际有 token 时注入（无 token 时连 headers 键都不写）', async () => {
    // 前半段就是既有 deleteThread 用例的基础：无 token 时 init 形状与改造前完全一致。
    let fetchMock = vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ thread_id: 't1', messages: [] }),
    } as unknown as Response);
    vi.stubGlobal('fetch', fetchMock);

    await fetchHistory('t1');
    expect(fetchMock.mock.calls[0]![1]).toEqual({});

    setToken('tk-2');
    fetchMock = vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ thread_id: 't1', messages: [] }),
    } as unknown as Response);
    vi.stubGlobal('fetch', fetchMock);

    await fetchHistory('t1');
    expect((fetchMock.mock.calls[0]![1] as RequestInit).headers)
      .toEqual({ Authorization: 'Bearer tk-2' });
  });

  it('带 token 却收到 401 → 清 token 并触发 onUnauthorized（会话失效）', async () => {
    setToken('stale');
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401 } as unknown as Response));

    await expect(fetchHistory('t1')).rejects.toThrow(/401/);

    expect(getToken()).toBeNull();
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it('未带 token 的 401（登录密码错）→ 不清 token、不触发回调', async () => {
    // 401 在本项目有**两种含义**：会话失效（带着 token 仍被拒）与凭证错（登录接口）。
    // 不加这条分流，用户输错一次密码就会被当成「会话过期」，并多清一次 token。
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401 } as unknown as Response));

    await expect(fetchHistory('t1')).rejects.toThrow(/401/);

    expect(onUnauthorized).not.toHaveBeenCalled();
  });
});

// ============================ 会话列表（P3） ============================
describe('fetchThreads / renameThread', () => {
  it('fetchThreads 走 GET /api/chat/threads 并原样带出 total', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ threads: [{ thread_id: 't1', title: 'a', created_at: 'x', updated_at: 'y' }], total: 55 }),
    } as unknown as Response);
    vi.stubGlobal('fetch', fetchMock);

    const r = await fetchThreads();

    expect(fetchMock.mock.calls[0]![0]).toBe('/api/chat/threads');
    expect(r.total).toBe(55);        // total 是「被 50 条截断」的唯一信号，不能丢
  });

  it('renameThread 用 PATCH、把新标题放进 body、并对 id 做 URL 编码', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ thread_id: 't1', title: '新标题' }),
    } as unknown as Response);
    vi.stubGlobal('fetch', fetchMock);

    const r = await renameThread('t1', '新标题');

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe('/api/chat/threads/t1');
    expect(init.method).toBe('PATCH');
    expect(init.body).toBe(JSON.stringify({ title: '新标题' }));
    expect(r).toEqual({ thread_id: 't1', title: '新标题' });
  });
});

describe('deleteThread', () => {
  it('用 DELETE 方法并把 thread_id 编进路径', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ thread_id: 't1', deleted: true }),
    } as unknown as Response);
    vi.stubGlobal('fetch', fetchMock);

    const r = await deleteThread('t1');

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe('/api/chat/threads/t1');
    expect(init).toEqual({ method: 'DELETE' });
    expect(r).toEqual({ thread_id: 't1', deleted: true });
  });

  it('对含特殊字符的 id 做 URL 编码', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ thread_id: 'a/b', deleted: false }),
    } as unknown as Response);
    vi.stubGlobal('fetch', fetchMock);

    await deleteThread('a/b');

    expect(fetchMock.mock.calls[0]![0]).toBe('/api/chat/threads/a%2Fb');
  });

  it('deleted:false 也是成功(幂等)，非 2xx 才抛错', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true, json: async () => ({ thread_id: 't1', deleted: false }),
    } as unknown as Response));
    await expect(deleteThread('t1')).resolves.toEqual({ thread_id: 't1', deleted: false });

    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 500 } as unknown as Response));
    await expect(deleteThread('t1')).rejects.toThrow(/500/);
  });
});
