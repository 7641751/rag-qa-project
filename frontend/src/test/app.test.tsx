import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

const {
  streamChatMock, fetchHistoryMock, uploadDocumentMock, fetchDocumentsMock, deleteDocumentMock,
  deleteThreadMock, fetchMeMock, loginMock, registerMock, setUnauthorizedHandlerMock,
} = vi.hoisted(() => ({
  streamChatMock: vi.fn(), fetchHistoryMock: vi.fn(),
  uploadDocumentMock: vi.fn(), fetchDocumentsMock: vi.fn(), deleteDocumentMock: vi.fn(),
  deleteThreadMock: vi.fn(),
  // P2：App 多了 useAuth，而 useAuth 依赖这 4 个导出（spec §8.5 已预警这一点）
  fetchMeMock: vi.fn(), loginMock: vi.fn(), registerMock: vi.fn(),
  setUnauthorizedHandlerMock: vi.fn(),
}));
// ⚠ App 依赖 client 的全部导出（聊天 3 + KB 3 + 鉴权 4）；mock 工厂必须全部提供，
//   否则挂载即 TypeError。
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
  deleteThreadMock.mockResolvedValue({ thread_id: 't', deleted: true });
  fetchHistoryMock.mockResolvedValue({ thread_id: 't', messages: [] });
  fetchDocumentsMock.mockResolvedValue({ documents: [], builtin: { docs: 88, chunks: 2885 } });
  fetchMeMock.mockResolvedValue({ id: 1, username: 'hao' });
});

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
});
