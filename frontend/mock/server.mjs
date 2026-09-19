// 无真实后端时联调用。启动: npm run mock (监听 :8000，实现全部 7 个端点)
import http from 'node:http';

const PORT = 8000;

// ---------- 聊天（原有，保持不变） ----------
const steps = [
  ['step', { node: 'retrieve', label: '检索', detail: '召回 8 段' }],
  ['step', { node: 'grade_documents', label: '评分', detail: '相关 6/8' }],
  ['step', { node: 'generate', label: '生成', detail: '开始作答' }],
];
const tokens = ['要切分 markdown，', '用 `RecursiveCharacterTextSplitter.from_language', '(Language.MARKDOWN, ...)`。', '它按标题/代码围栏优先切分。'];
const sources = [{ title: '01_文档加载与文本分割.md', snippet: 'RecursiveCharacterTextSplitter.from_language(...)', score: 0.82 }];
// thread_id → { title, created_at, updated_at, messages }。
// 带标题与时间戳是为了让侧栏（列表 / 相对时间 / 重命名）有**真实语义**而不是假数据。
// Map.delete() 返回「是否真的删掉了」，恰好就是契约里的 deleted 字段。
const threads = new Map();

// ---------- 知识库（内存态：可真增真删，让前端全流程自测） ----------
let documents = [
  { doc_id: 'mock-doc-1', filename: 'notes.md', title: 'notes', chunks: 12, size_bytes: 2048, uploaded_at: '2026-09-11T09:00:00Z' },
];
const builtin = { docs: 88, chunks: 2885 };
const ALLOWED = ['.md', '.txt', '.pdf'];
const MAX_BYTES = 20 * 1024 * 1024;

const frame = (event, data) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const json = (res, code, obj) => {
  res.writeHead(code, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(obj));
};
function cors(res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET,POST,DELETE,OPTIONS');
  // ⚠ 必须含 Authorization：P2 起 chat / kb 都带 Bearer 头，漏了它浏览器预检直接失败，
  //   表现为「所有请求都报 CORS 错」，很容易误判成后端挂了。
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization');
}
/** mock 不真解析 multipart，仅从 body 里粗提 filename */
function pickFilename(buf) {
  const m = /filename="([^"]+)"/.exec(buf.toString('latin1'));
  return m ? m[1] : 'unknown.bin';
}

// ---------- 账号与令牌（内存态）----------
// 预置一个演示账号，与后端 MySQL 里的测试账号一致：省得起 mock 后还要先注册一遍。
const users = new Map([['hao', { id: 1, username: 'hao', password: 'abcd1234' }]]);
let nextUserId = 2;

/** mock 的 token 不是真 JWT（没有签名），结构为 `mock.<base64url(payload)>.sig`。
 *  够用的理由：mock 只需要能判断「带没带、能不能解出未过期的 sub」，用来跑通前端的
 *  401 → 自动登出这条链路；真正的签名校验由后端 pyjwt 负责，不在这里重复实现。 */
const issueToken = user => `mock.${Buffer.from(JSON.stringify({
  sub: String(user.id), username: user.username, exp: Date.now() + 7 * 864e5,
})).toString('base64url')}.sig`;

/** Authorization 头 → user；缺失 / 格式错 / 过期一律 null */
function currentUser(req) {
  const m = /^Bearer\s+(.+)$/i.exec(req.headers.authorization ?? '');
  if (!m) return null;
  const parts = m[1].split('.');
  if (parts.length !== 3 || parts[0] !== 'mock') return null;
  try {
    const payload = JSON.parse(Buffer.from(parts[1], 'base64url').toString('utf8'));
    if (!payload.sub || !payload.exp || payload.exp < Date.now()) return null;
    return { id: Number(payload.sub), username: String(payload.username ?? '') };
  } catch { return null; }
}

/** 未登录 → 写 401 并返回 true（调用方直接 return） */
function requireAuth(req, res) {
  if (currentUser(req)) return false;
  json(res, 401, { code: 'UNAUTHORIZED', message: '缺少或无效的 Authorization 头' });
  return true;
}

const readJson = async req => {
  const parts = [];
  for await (const c of req) parts.push(c);
  try { return JSON.parse(Buffer.concat(parts).toString('utf8')); } catch { return null; }
};

const server = http.createServer(async (req, res) => {
  cors(res);
  if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return; }
  const url = new URL(req.url, `http://localhost:${PORT}`);

  if (req.method === 'GET' && url.pathname === '/api/health') {
    json(res, 200, { status: 'ok', kb_count: builtin.chunks, model: 'qwen3.7-text-embedding' });
    return;
  }

  // ---------- 鉴权：三个公开端点 ----------
  if (req.method === 'POST' && url.pathname === '/api/auth/register') {
    const body = await readJson(req);
    const username = String(body?.username ?? '');
    const password = String(body?.password ?? '');
    // 与真后端同一套约束（Auth 模型）：用户名 3-32 位字母/数字/下划线，密码 ≥ 8 位
    if (!/^[A-Za-z0-9_]{3,32}$/.test(username) || password.length < 8) {
      json(res, 422, {
        code: 'VALIDATION_ERROR',
        message: 'username: 需 3-32 位字母/数字/下划线；password: 至少 8 位',
      });
      return;
    }
    if (users.has(username)) {
      json(res, 409, { code: 'USERNAME_TAKEN', message: '用户名已存在' });
      return;
    }
    const user = { id: nextUserId++, username, password };
    users.set(username, user);
    json(res, 201, { id: user.id, username: user.username });
    return;
  }
  if (req.method === 'POST' && url.pathname === '/api/auth/login') {
    const body = await readJson(req);
    const user = users.get(String(body?.username ?? ''));
    // 故意不区分「用户不存在」与「密码错」，且文案逐字相同 —— 与真后端一致。
    // 若这里能区分，联调时就会以为前端「在 mock 上能辨出用户名、在真后端却不行」。
    if (!user || user.password !== String(body?.password ?? '')) {
      json(res, 401, { code: 'INVALID_CREDENTIALS', message: '用户名或密码错误' });
      return;
    }
    json(res, 200, {
      access_token: issueToken(user), token_type: 'bearer',
      user: { id: user.id, username: user.username },
    });
    return;
  }
  if (req.method === 'GET' && url.pathname === '/api/auth/me') {
    const user = currentUser(req);
    if (!user) { json(res, 401, { code: 'UNAUTHORIZED', message: 'token 无效或已过期' }); return; }
    json(res, 200, { id: user.id, username: user.username });
    return;
  }

  // ---- 以下端点自 P2 起全部要求登录（/api/health 是唯一公开的探活）----
  if (requireAuth(req, res)) return;

  if (req.method === 'GET' && url.pathname === '/api/chat/history') {
    const tid = url.searchParams.get('thread_id') ?? '';
    // 未知/已删除的 thread → 200 + 空数组（契约：不报 404）
    json(res, 200, { thread_id: tid, messages: threads.get(tid)?.messages ?? [] });
    return;
  }
  // ---------- 会话列表（P3）----------
  if (req.method === 'GET' && url.pathname === '/api/chat/threads') {
    const all = [...threads.entries()]
      .map(([thread_id, t]) => ({
        thread_id, title: t.title, created_at: t.created_at, updated_at: t.updated_at,
      }))
      // 按 updated_at 倒序，与契约一致；total 是**真实总数**，列表最多回 50 条
      .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
    json(res, 200, { threads: all.slice(0, 50), total: all.length });
    return;
  }
  if (req.method === 'POST' && url.pathname === '/api/chat/stream') {
    const body = await new Promise(resolve => { let b = ''; req.on('data', c => b += c); req.on('end', () => resolve(b)); });
    let thread_id = 'mock', question = '';
    try { const j = JSON.parse(body); thread_id = j.thread_id ?? 'mock'; question = j.question ?? ''; } catch { /* 忽略非法 body */ }
    // 问题里带「无依据 / 随便问 / ungrounded」→ 模拟未命中知识库，便于在浏览器里看警示横幅
    const ungrounded = /ungrounded|无依据|随便问/.test(question);
    res.writeHead(200, {
      'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache',
      Connection: 'keep-alive', 'X-Accel-Buffering': 'no',
    });
    for (const [ev, data] of steps) { res.write(frame(ev, data)); await sleep(200); }
    if (ungrounded) {
      res.write(frame('token', { text: '⚠ 本回答未命中知识库，基于模型通用知识，可能过时或有误，请自行核实。\n\n' }));
      await sleep(150);
      res.write(frame('token', { text: '（模拟）LangGraph 通过 checkpointer 在每个 super-step 后保存状态。' }));
    } else {
      for (const t of tokens) { res.write(frame('token', { text: t })); await sleep(120); }
    }
    res.write(frame('sources', { sources: ungrounded ? [] : sources }));
    await sleep(100);
    res.write(frame('done', { thread_id, rewrites: ungrounded ? 2 : 0, grounded: !ungrounded }));
    // 记录本轮问答，让 history / 列表 / 删除都有真实语义
    const now = new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
    const t = threads.get(thread_id)
      ?? { title: question.slice(0, 20), created_at: now, updated_at: now, messages: [] };
    t.messages.push({ role: 'user', content: question });
    t.messages.push({
      role: 'assistant',
      content: ungrounded ? '⚠ 本回答未命中知识库，基于模型通用知识（模拟）。' : tokens.join(''),
      sources: ungrounded ? [] : sources,
      grounded: !ungrounded,
    });
    t.updated_at = now;              // 每次提问推进，列表据此倒序
    threads.set(thread_id, t);
    res.end();
    return;
  }

  // ---------- 聊天: 单条会话（删除 / 重命名）----------
  const threadPath = /^\/api\/chat\/threads\/([^/]+)$/.exec(url.pathname);
  if (threadPath) {
    const id = decodeURIComponent(threadPath[1]);
    if (req.method === 'DELETE') {
      // Map.delete 的返回值就是契约的 deleted：本来不存在 → false，仍是 200（幂等，不报 404）
      json(res, 200, { thread_id: id, deleted: threads.delete(id) });
      return;
    }
    if (req.method === 'PATCH') {
      const body = await readJson(req);
      const title = String(body?.title ?? '').trim();
      if (!title || title.length > 60) {
        json(res, 422, { code: 'VALIDATION_ERROR', message: 'title: 需 1-60 字' });
        return;
      }
      const t = threads.get(id);
      // mock 只有一个用户，做不出「别人的会话 → 403」；真后端的 403 由 pytest 守
      if (!t) { json(res, 404, { code: 'NOT_FOUND', message: `会话不存在: ${id}` }); return; }
      // 只改标题、**不动 updated_at**：重命名不该把会话顶到列表最前面
      t.title = title;
      json(res, 200, { thread_id: id, title });
      return;
    }
  }

  // ---------- KB: 列表 ----------
  if (req.method === 'GET' && url.pathname === '/api/kb/documents') {
    const sorted = [...documents].sort((a, b) => b.uploaded_at.localeCompare(a.uploaded_at));
    json(res, 200, { documents: sorted, builtin });
    return;
  }

  // ---------- KB: 上传（SSE 进度） ----------
  if (req.method === 'POST' && url.pathname === '/api/kb/documents') {
    const parts = [];
    for await (const c of req) parts.push(c);
    const buf = Buffer.concat(parts);
    const filename = pickFilename(buf);
    const lower = filename.toLowerCase();
    // 流开始前的错误用 HTTP 状态码（契约两段式）
    if (!ALLOWED.some(ext => lower.endsWith(ext))) {
      json(res, 415, { code: 'UNSUPPORTED_FILE_TYPE', message: `仅支持 ${ALLOWED.join(' / ')}` });
      return;
    }
    if (buf.length > MAX_BYTES) {
      json(res, 413, { code: 'FILE_TOO_LARGE', message: '文件超过 20 MB' });
      return;
    }
    res.writeHead(200, {
      'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache',
      Connection: 'keep-alive', 'X-Accel-Buffering': 'no',
    });
    const total = 23;
    const doc_id = `mock-${Date.now().toString(16)}`;
    const idx = documents.findIndex(d => d.filename === filename);
    const replaced = idx >= 0;                       // 同名视为替换
    if (replaced) documents.splice(idx, 1);

    res.write(frame('progress', { stage: 'saved', message: `已接收 ${filename} (${(buf.length / 1024).toFixed(1)} KB)` }));
    await sleep(250);
    res.write(frame('progress', { stage: 'parsed', message: '解析出 18432 字符' }));
    await sleep(250);
    res.write(frame('progress', { stage: 'split', current: total, total, message: `切分为 ${total} 段` }));
    await sleep(200);
    for (let cur = 5; cur < total; cur += 6) {
      res.write(frame('progress', { stage: 'embedding', current: cur, total, message: `嵌入 ${cur}/${total}` }));
      await sleep(220);
    }
    res.write(frame('progress', { stage: 'embedding', current: total, total, message: `嵌入 ${total}/${total}` }));
    await sleep(150);

    const uploaded_at = new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
    documents.push({
      doc_id, filename, title: filename.replace(/\.[^.]+$/, ''),
      chunks: total, size_bytes: buf.length, uploaded_at,
    });
    res.write(frame('done', { doc_id, filename, chunks: total, replaced, uploaded_at }));
    res.end();
    return;
  }

  // ---------- KB: 删除 ----------
  const del = /^\/api\/kb\/documents\/([^/]+)$/.exec(url.pathname);
  if (req.method === 'DELETE' && del) {
    const id = decodeURIComponent(del[1]);
    const i = documents.findIndex(d => d.doc_id === id);
    if (i < 0) { json(res, 404, { code: 'NOT_FOUND', message: `文档不存在: ${id}` }); return; }
    const [gone] = documents.splice(i, 1);
    json(res, 200, { doc_id: gone.doc_id, filename: gone.filename, deleted_chunks: gone.chunks });
    return;
  }

  json(res, 404, { code: 'NOT_FOUND', message: 'no route' });
});

server.listen(PORT, () => console.log(
  `[mock] SSE 服务已起: http://localhost:${PORT}（10 个端点）\n` +
  '[mock] 演示账号 hao / abcd1234；自 P2 起 chat 与 kb 端点都要求 Bearer 头'));
