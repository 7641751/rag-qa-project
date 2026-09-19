import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

const {
  streamChatMock, fetchHistoryMock, uploadDocumentMock, fetchDocumentsMock, deleteDocumentMock,
  deleteThreadMock, fetchMeMock, loginMock, registerMock, setUnauthorizedHandlerMock,
  fetchThreadsMock, renameThreadMock,
} = vi.hoisted(() => ({
  streamChatMock: vi.fn(), fetchHistoryMock: vi.fn(),
  uploadDocumentMock: vi.fn(), fetchDocumentsMock: vi.fn(), deleteDocumentMock: vi.fn(),
  deleteThreadMock: vi.fn(),
  // P2：App 多了 useAuth，而 useAuth 依赖这 4 个导出（spec §8.5 已预警这一点）
  fetchMeMock: vi.fn(), loginMock: vi.fn(), registerMock: vi.fn(),
  setUnauthorizedHandlerMock: vi.fn(),
  // P3：App 多了 useConversations / useSidebar，依赖这两个导出
  fetchThreadsMock: vi.fn(), renameThreadMock: vi.fn(),
}));
// ⚠ App 依赖 client 的全部导出（聊天 3 + KB 3 + 鉴权 4 + 会话列表 2）；mock 工厂必须
//   全部提供，否则挂载即 TypeError。
vi.mock('../api/client', () => ({
  streamChat: streamChatMock,
  fetchHistory: fetchHistoryMock,
  deleteThread: deleteThreadMock,
  uploadDocument: uploadDocumentMock,
  fetchDocuments: fetchDocumentsMock,
  deleteDocument: deleteDocumentMock,
  fetchMe: fetchMeMock,
  login: loginMock,
  register: registerMock,
  setUnauthorizedHandler: setUnauthorizedHandlerMock,
  fetchThreads: fetchThreadsMock,
  renameThread: renameThreadMock,
}));

import App from '../App';
import { getToken, setToken } from '../api/tokenStore';

const flush = () => act(async () => { await new Promise(r => setTimeout(r, 0)); });
const mdFile = () => new File(['# hi'], 'a.md', { type: 'text/markdown' });

beforeEach(() => {
  localStorage.clear();
  // 既有用例测的都是「已登录之后」的行为，所以统一预置 token：useAuth 启动时会拿它调
  // fetchMe，解析成功即 authed，主界面照常渲染。未登录场景由文件末尾两条新用例自己清 token。
  setToken('test-token');
  streamChatMock.mockReset();
  fetchHistoryMock.mockReset();
  uploadDocumentMock.mockReset();
  fetchDocumentsMock.mockReset();
  deleteDocumentMock.mockReset();
  deleteThreadMock.mockReset();
  fetchMeMock.mockReset();
  loginMock.mockReset();
  registerMock.mockReset();
  setUnauthorizedHandlerMock.mockReset();
  fetchThreadsMock.mockReset();
  renameThreadMock.mockReset();
  deleteThreadMock.mockResolvedValue({ thread_id: 't', deleted: true });
  fetchHistoryMock.mockResolvedValue({ thread_id: 't', messages: [] });
  fetchDocumentsMock.mockResolvedValue({ documents: [], builtin: { docs: 88, chunks: 2885 } });
  fetchMeMock.mockResolvedValue({ id: 1, username: 'hao' });
  // 默认空列表：绝大多数用例只关心聊天区，侧栏空着即可
  fetchThreadsMock.mockResolvedValue({ threads: [], total: 0 });
  renameThreadMock.mockResolvedValue({ thread_id: 't', title: 'x' });
});

const conv = (id: string, title: string, updatedAt: string) =>
  ({ thread_id: id, title, created_at: '2026-09-13T05:00:00Z', updated_at: updatedAt });

describe('App 集成', () => {
  it('输入并发送后, 界面出现用户问题与流式答案', async () => {
    streamChatMock.mockImplementation(async (_b: unknown, h: any) => {
      h.onToken({ text: '你好' });
      h.onDone({ thread_id: 't', rewrites: 0, grounded: true });
    });
    render(<App />);
    await flush();                                  // 挂载时的历史 + 知识库列表加载
    fireEvent.change(screen.getByPlaceholderText('输入问题…'), { target: { value: '什么是 LangChain' } });
    fireEvent.click(screen.getByText('发送'));
    await waitFor(() => expect(screen.getByText('什么是 LangChain')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText(/你好/)).toBeInTheDocument());
  });

  it('点「📚 知识库」打开抽屉，展示上传区与预置摘要', async () => {
    render(<App />);
    await flush();
    expect(screen.queryByTestId('upload-zone')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('📚 知识库'));
    await waitFor(() => expect(screen.getByTestId('upload-zone')).toBeInTheDocument());
    expect(screen.getByTestId('builtin-summary')).toHaveTextContent('88');
    expect(screen.getByText('还没有上传任何文档')).toBeInTheDocument();
  });

  it('Esc 与点遮罩都能关闭抽屉', async () => {
    render(<App />);
    await flush();
    fireEvent.click(screen.getByText('📚 知识库'));
    await waitFor(() => expect(screen.getByTestId('upload-zone')).toBeInTheDocument());
    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByTestId('upload-zone')).not.toBeInTheDocument());
    fireEvent.click(screen.getByText('📚 知识库'));
    await waitFor(() => expect(screen.getByTestId('upload-zone')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('kb-overlay'));
    await waitFor(() => expect(screen.queryByTestId('upload-zone')).not.toBeInTheDocument());
  });

  it('关抽屉后上传不中断 → Header 角标仍显示', async () => {
    uploadDocumentMock.mockImplementation(() => new Promise<void>(() => {}));   // 悬着，模拟长上传
    render(<App />);
    await flush();
    fireEvent.click(screen.getByText('📚 知识库'));
    await waitFor(() => expect(screen.getByTestId('upload-zone')).toBeInTheDocument());
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await act(async () => { fireEvent.change(input, { target: { files: [mdFile()] } }); });
    await waitFor(() => expect(screen.getByTestId('kb-badge')).toHaveTextContent('1'));
    fireEvent.keyDown(window, { key: 'Escape' });                       // 关抽屉
    await waitFor(() => expect(screen.queryByTestId('upload-zone')).not.toBeInTheDocument());
    expect(screen.getByTestId('kb-badge')).toHaveTextContent('1');       // 角标仍在：状态住 App 层
  });

  /** 发一条消息并等它完成，让删除按钮从禁用变可用 */
  async function askOnce(question = '什么是 LangChain') {
    streamChatMock.mockImplementation(async (_b: unknown, h: any) => {
      h.onToken({ text: '你好' });
      h.onDone({ thread_id: 't', rewrites: 0, grounded: true });
    });
    fireEvent.change(screen.getByPlaceholderText('输入问题…'), { target: { value: question } });
    fireEvent.click(screen.getByText('发送'));
    await waitFor(() => expect(screen.getByText(question)).toBeInTheDocument());
  }

  it('空会话时删除按钮禁用，发一条消息后启用', async () => {
    render(<App />);
    await flush();
    expect(screen.getByTestId('btn-delete-chat')).toBeDisabled();
    await askOnce();
    await waitFor(() => expect(screen.getByTestId('btn-delete-chat')).not.toBeDisabled());
  });

  it('点删除 → 弹窗 → 确认 → 调 DELETE、消息清空、弹窗关闭', async () => {
    render(<App />);
    await flush();
    await askOnce('待删的问题');
    expect(screen.queryByTestId('confirm-dialog')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('btn-delete-chat'));
    await waitFor(() => expect(screen.getByTestId('confirm-dialog')).toBeInTheDocument());
    expect(screen.getByTestId('confirm-cancel')).toHaveFocus();      // 安全默认：焦点在取消

    fireEvent.click(screen.getByTestId('confirm-ok'));
    await waitFor(() => expect(deleteThreadMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByTestId('confirm-dialog')).not.toBeInTheDocument());
    expect(screen.queryByText('待删的问题')).not.toBeInTheDocument();  // 消息已清
  });

  it('删除失败时弹窗保持打开、显示错误行、消息不清空', async () => {
    deleteThreadMock.mockRejectedValue(new Error('delete thread HTTP 500'));
    render(<App />);
    await flush();
    await askOnce('不能丢的问题');

    fireEvent.click(screen.getByTestId('btn-delete-chat'));
    fireEvent.click(screen.getByTestId('confirm-ok'));

    await waitFor(() => expect(screen.getByTestId('confirm-error')).toHaveTextContent('删除失败，请重试'));
    expect(screen.getByTestId('confirm-dialog')).toBeInTheDocument();     // 没关，可重试
    expect(screen.getByText('不能丢的问题')).toBeInTheDocument();          // 悲观：消息保留
  });

  // ---------- 鉴权门槛（P2 新增） ----------
  it('未登录（无 token）→ 渲染登录页，不渲染主界面', async () => {
    localStorage.clear();
    render(<App />);
    await flush();

    expect(screen.getByTestId('login-page')).toBeInTheDocument();
    expect(screen.queryByText('📚 知识库')).not.toBeInTheDocument();
    expect(fetchMeMock).not.toHaveBeenCalled();          // 没 token 就不必去问后端
    expect(fetchHistoryMock).not.toHaveBeenCalled();     // 也不该白跑一次注定 401 的请求
  });

  it('校验本地 token 通过后才渲染主界面（不闪一下主界面）', async () => {
    render(<App />);

    expect(screen.getByTestId('auth-loading')).toBeInTheDocument();

    await flush();

    expect(screen.queryByTestId('login-page')).not.toBeInTheDocument();
    expect(screen.getByText('📚 知识库')).toBeInTheDocument();
    expect(fetchMeMock).toHaveBeenCalledTimes(1);
  });

  it('点「退出」→ 清 token 并回到登录页', async () => {
    render(<App />);
    await flush();
    expect(screen.getByText('📚 知识库')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('btn-logout'));
    await flush();

    expect(screen.getByTestId('login-page')).toBeInTheDocument();
    expect(screen.queryByText('📚 知识库')).not.toBeInTheDocument();
    expect(getToken()).toBeNull();                       // 真的清了凭证，不只是切了视图
  });

  // ---------- P3：侧栏与会话切换 ----------
  it('切换会话后消息区换成新会话的内容', async () => {
    fetchThreadsMock.mockResolvedValue({
      threads: [conv('tA', '会话A', '2026-09-13T07:00:00Z'), conv('tB', '会话B', '2026-09-13T06:00:00Z')],
      total: 2,
    });
    // 当前会话 A 先有消息 —— 这样才证明「切换后是被替换了」，而不是本来就空
    fetchHistoryMock.mockResolvedValue({ thread_id: 'tA', messages: [
      { role: 'user', content: 'A 的问题' },
      { role: 'assistant', content: 'A 的答案' },
    ]});
    render(<App />);
    await flush();
    expect(screen.getByText('A 的答案')).toBeInTheDocument();

    // 切到 B。回填时 A 的消息还在 state 里 —— 若仍沿用「无消息才回填」的旧守卫，
    // 这里会一直显示 A 的内容（§10.3 要覆盖的正是这个）
    fetchHistoryMock.mockResolvedValue({ thread_id: 'tB', messages: [
      { role: 'user', content: 'B 的问题' },
      { role: 'assistant', content: 'B 的答案' },
    ]});
    fireEvent.click(screen.getByText('会话B'));
    await flush();

    expect(screen.getByText('B 的答案')).toBeInTheDocument();
    expect(screen.queryByText('A 的答案')).not.toBeInTheDocument();
  });

  it('切换会话时先 abort 在飞的流（否则旧流的 token 会写进新会话的气泡）', async () => {
    let signal: AbortSignal | undefined;
    streamChatMock.mockImplementation((_b: unknown, h: any) =>
      new Promise<void>(() => { signal = h.signal as AbortSignal; }));   // 悬着不结束
    fetchThreadsMock.mockResolvedValue({ threads: [conv('t9', '另一个会话', '2026-09-13T06:00:00Z')], total: 1 });

    render(<App />);
    await flush();
    fireEvent.change(screen.getByPlaceholderText('输入问题…'), { target: { value: '一个会悬着的问题' } });
    fireEvent.click(screen.getByText('发送'));
    await flush();
    expect(signal?.aborted).toBe(false);

    fireEvent.click(screen.getByText('另一个会话'));
    await flush();

    expect(signal?.aborted).toBe(true);
  });

  it('一轮问答 done 之后刷新侧栏（新会话的行是后端首次提问时才 upsert 的）', async () => {
    streamChatMock.mockImplementation(async (_b: unknown, h: any) => {
      h.onToken({ text: '答' });
      h.onDone({ thread_id: 't', rewrites: 0, grounded: true });
    });
    render(<App />);
    await flush();
    const before = fetchThreadsMock.mock.calls.length;      // 挂载时已拉过一次

    fireEvent.change(screen.getByPlaceholderText('输入问题…'), { target: { value: '新会话的第一问' } });
    fireEvent.click(screen.getByText('发送'));
    await waitFor(() => expect(screen.getByText('答')).toBeInTheDocument());

    await waitFor(() => expect(fetchThreadsMock.mock.calls.length).toBeGreaterThan(before));
  });
});
