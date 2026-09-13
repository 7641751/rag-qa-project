import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

const { streamChatMock, fetchHistoryMock, uploadDocumentMock, fetchDocumentsMock, deleteDocumentMock } = vi.hoisted(() => ({
  streamChatMock: vi.fn(), fetchHistoryMock: vi.fn(),
  uploadDocumentMock: vi.fn(), fetchDocumentsMock: vi.fn(), deleteDocumentMock: vi.fn(),
}));
// ⚠ App 现在也依赖 KB 三个函数；mock 工厂必须全部提供，否则挂载即 TypeError
vi.mock('../api/client', () => ({
  streamChat: streamChatMock,
  fetchHistory: fetchHistoryMock,
  uploadDocument: uploadDocumentMock,
  fetchDocuments: fetchDocumentsMock,
  deleteDocument: deleteDocumentMock,
}));

import App from '../App';

const flush = () => act(async () => { await new Promise(r => setTimeout(r, 0)); });
const mdFile = () => new File(['# hi'], 'a.md', { type: 'text/markdown' });

beforeEach(() => {
  localStorage.clear();
  streamChatMock.mockReset();
  fetchHistoryMock.mockReset();
  uploadDocumentMock.mockReset();
  fetchDocumentsMock.mockReset();
  deleteDocumentMock.mockReset();
  fetchHistoryMock.mockResolvedValue({ thread_id: 't', messages: [] });
  fetchDocumentsMock.mockResolvedValue({ documents: [], builtin: { docs: 88, chunks: 2885 } });
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
});
