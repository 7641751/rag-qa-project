// 无真实后端时联调用。启动: npm run mock (监听 :8000，实现全部 6 个端点)
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
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
}
/** mock 不真解析 multipart，仅从 body 里粗提 filename */
function pickFilename(buf) {
  const m = /filename="([^"]+)"/.exec(buf.toString('latin1'));
  return m ? m[1] : 'unknown.bin';
}

const server = http.createServer(async (req, res) => {
  cors(res);
  if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return; }
  const url = new URL(req.url, `http://localhost:${PORT}`);

  if (req.method === 'GET' && url.pathname === '/api/health') {
    json(res, 200, { status: 'ok', kb_count: builtin.chunks, model: 'qwen3.7-text-embedding' });
    return;
  }
  if (req.method === 'GET' && url.pathname === '/api/chat/history') {
    json(res, 200, { thread_id: url.searchParams.get('thread_id') ?? '', messages: [] });
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
    res.end();
    return;
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

server.listen(PORT, () => console.log(`[mock] SSE 服务已起: http://localhost:${PORT}（6 个端点）`));
