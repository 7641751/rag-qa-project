import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

const { uploadDocumentMock, fetchDocumentsMock, deleteDocumentMock } = vi.hoisted(() => ({
  uploadDocumentMock: vi.fn(), fetchDocumentsMock: vi.fn(), deleteDocumentMock: vi.fn(),
}));
vi.mock('../api/client', () => ({
  uploadDocument: uploadDocumentMock,
  fetchDocuments: fetchDocumentsMock,
  deleteDocument: deleteDocumentMock,
}));

import { useKnowledgeBase, toPercent, MAX_FILES_PER_PICK } from '../hooks/useKnowledgeBase';

const md = (name = 'a.md') => new File(['# hi'], name, { type: 'text/markdown' });
const doc1 = () => ({ doc_id: 'd1', filename: 'a.md', chunks: 3, uploaded_at: '2026-09-11T10:00:00Z' });
const flush = () => act(async () => { await new Promise(r => setTimeout(r, 0)); });
/** 永不 resolve 的上传 mock：模拟长上传，避免串行泵逐个完成产生级联 dispatch（会漏到 act 外报警告） */
const hangForever = () => uploadDocumentMock.mockImplementation(() => new Promise<void>(() => {}));

beforeEach(() => {
  uploadDocumentMock.mockReset();
  fetchDocumentsMock.mockReset();
  deleteDocumentMock.mockReset();
  fetchDocumentsMock.mockResolvedValue({ documents: [] });
  deleteDocumentMock.mockResolvedValue({ doc_id: 'd1', filename: 'a.md', deleted_chunks: 3 });
});

describe('useKnowledgeBase', () => {
  it('不支持的格式被拦截，不发请求但让用户看见原因', async () => {
    const { result } = renderHook(() => useKnowledgeBase());
    await flush();
    act(() => { result.current.addFiles([new File(['x'], 'virus.exe')]); });
    expect(uploadDocumentMock).not.toHaveBeenCalled();
    expect(result.current.uploads[0]?.status).toBe('error');
    expect(result.current.uploads[0]?.error).toContain('不支持的格式');
  });

  it('超过 MAX_FILES_PER_PICK 的多余项标 error', async () => {
    hangForever();
    const { result } = renderHook(() => useKnowledgeBase());
    await flush();
    const many = Array.from({ length: MAX_FILES_PER_PICK + 2 }, (_, i) => md(`f${i}.md`));
    await act(async () => { result.current.addFiles(many); await new Promise(r => setTimeout(r, 0)); });
    expect(result.current.uploads).toHaveLength(MAX_FILES_PER_PICK + 2);
    expect(result.current.uploads[MAX_FILES_PER_PICK]?.status).toBe('error');
    expect(result.current.uploads[MAX_FILES_PER_PICK]?.error).toContain('一次最多上传');
  });

  it('串行上传：第 2 个在第 1 个完成前保持 pending', async () => {
    let releaseFirst: () => void = () => {};
    uploadDocumentMock
      .mockImplementationOnce(() => new Promise<void>(r => { releaseFirst = r; }))
      .mockImplementationOnce(async () => {});
    const { result } = renderHook(() => useKnowledgeBase());
    await flush();
    await act(async () => { result.current.addFiles([md('a.md'), md('b.md')]); });
    expect(uploadDocumentMock).toHaveBeenCalledTimes(1);
    expect(result.current.uploads[0]?.status).toBe('uploading');
    expect(result.current.uploads[1]?.status).toBe('pending');
    expect(result.current.activeCount).toBe(2);
    await act(async () => { releaseFirst(); await new Promise(r => setTimeout(r, 0)); });
    expect(uploadDocumentMock).toHaveBeenCalledTimes(2);
  });

  it('onProgress 更新 stage/current/total；onDone 标 done 并刷新列表', async () => {
    uploadDocumentMock.mockImplementation(async (_f: File, h: any) => {
      h.onProgress({ stage: 'split', current: 7, total: 7, message: '切分为 7 段' });
      h.onProgress({ stage: 'embedding', current: 3, total: 7, message: '嵌入 3/7' });
      h.onDone({ doc_id: 'd9', filename: 'a.md', chunks: 7, replaced: false, uploaded_at: '2026-09-11T10:00:00Z' });
    });
    const { result } = renderHook(() => useKnowledgeBase());
    await flush();
    await act(async () => { result.current.addFiles([md()]); await new Promise(r => setTimeout(r, 0)); });
    const u = result.current.uploads[0]!;
    expect(u.status).toBe('done');
    expect(u.docId).toBe('d9');
    expect(toPercent(u)).toBe(100);
    expect(fetchDocumentsMock).toHaveBeenCalledTimes(2);   // 挂载 1 次 + done 后刷新 1 次
  });

  it('cancelUpload 中止进行中的项并标 cancelled', async () => {
    let captured: AbortSignal | undefined;
    uploadDocumentMock.mockImplementation((_f: File, h: any) =>
      new Promise<void>((_resolve, reject) => {
        captured = h.signal as AbortSignal;
        captured.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
      }));
    const { result } = renderHook(() => useKnowledgeBase());
    await flush();
    await act(async () => { result.current.addFiles([md()]); });
    const id = result.current.uploads[0]!.id;
    await act(async () => { result.current.cancelUpload(id); await new Promise(r => setTimeout(r, 0)); });
    expect(captured?.aborted).toBe(true);
    expect(result.current.uploads[0]?.status).toBe('error');
    expect(result.current.uploads[0]?.cancelled).toBe(true);
    expect(result.current.uploads[0]?.error).toBe('已取消');
  });

  it('cancelUpload 对尚未开始的 pending 项直接从队列摘除', async () => {
    let releaseFirst: () => void = () => {};
    uploadDocumentMock
      .mockImplementationOnce(() => new Promise<void>(r => { releaseFirst = r; }))
      .mockImplementationOnce(async () => {});
    const { result } = renderHook(() => useKnowledgeBase());
    await flush();
    await act(async () => { result.current.addFiles([md('a.md'), md('b.md')]); });
    const secondId = result.current.uploads[1]!.id;
    act(() => { result.current.cancelUpload(secondId); });
    expect(result.current.uploads[1]?.status).toBe('error');
    expect(result.current.uploads[1]?.cancelled).toBe(true);
    await act(async () => { releaseFirst(); await new Promise(r => setTimeout(r, 0)); });
    expect(uploadDocumentMock).toHaveBeenCalledTimes(1);   // 第 2 个已摘除，不会再发
  });

  it('removeDoc 乐观移除；删除失败则回滚原位并设 docError', async () => {
    fetchDocumentsMock.mockResolvedValue({ documents: [doc1()] });
    let rejectDelete: (e: Error) => void = () => {};
    deleteDocumentMock.mockImplementation(() => new Promise((_r, rej) => { rejectDelete = rej; }));
    const { result } = renderHook(() => useKnowledgeBase());
    await flush();
    expect(result.current.documents).toHaveLength(1);
    // 删除请求悬着时：已乐观移除
    await act(async () => { void result.current.removeDoc('d1'); await new Promise(r => setTimeout(r, 0)); });
    expect(result.current.documents).toHaveLength(0);
    expect(result.current.docError).toBeUndefined();
    // 删除失败：回滚到原位 + 设 docError
    await act(async () => { rejectDelete(new Error('kb delete HTTP 500')); await new Promise(r => setTimeout(r, 0)); });
    expect(result.current.documents).toHaveLength(1);
    expect(result.current.documents[0]?.doc_id).toBe('d1');
    expect(result.current.docError).toContain('500');
  });

  it('removeDoc 删除成功后列表保持移除且无 docError', async () => {
    fetchDocumentsMock.mockResolvedValue({ documents: [doc1()] });
    const { result } = renderHook(() => useKnowledgeBase());
    await flush();
    await act(async () => { await result.current.removeDoc('d1'); });
    expect(result.current.documents).toHaveLength(0);
    expect(result.current.docError).toBeUndefined();
    expect(deleteDocumentMock).toHaveBeenCalledWith('d1');
  });

  it('流无 done 也无 error 就关闭 → 标为连接中断（不卡在 uploading）', async () => {
    uploadDocumentMock.mockImplementation(async () => {});   // 什么都不回调就 resolve
    const { result } = renderHook(() => useKnowledgeBase());
    await flush();
    await act(async () => { result.current.addFiles([md()]); await new Promise(r => setTimeout(r, 0)); });
    expect(result.current.uploads[0]?.status).toBe('error');
    expect(result.current.uploads[0]?.error).toContain('连接中断');
    expect(result.current.activeCount).toBe(0);
  });

  it('LIST_FAIL 设 listError 且 loadingList 归位', async () => {
    fetchDocumentsMock.mockRejectedValue(new Error('kb list HTTP 500'));
    const { result } = renderHook(() => useKnowledgeBase());
    await flush();
    expect(result.current.listError).toContain('500');
    expect(result.current.loadingList).toBe(false);
  });

  it('关抽屉后 uploads 保留（状态在 App 层，上传不中断）', async () => {
    hangForever();
    const { result } = renderHook(() => useKnowledgeBase());
    await flush();
    await act(async () => { result.current.openKb(); await new Promise(r => setTimeout(r, 0)); });
    expect(result.current.open).toBe(true);
    await act(async () => { result.current.addFiles([md()]); await new Promise(r => setTimeout(r, 0)); });
    act(() => { result.current.closeKb(); });
    expect(result.current.open).toBe(false);
    expect(result.current.uploads).toHaveLength(1);
    expect(result.current.activeCount).toBe(1);      // 仍在进行：关抽屉没中断它
  });
});

describe('toPercent', () => {
  it('各 stage / status 映射正确', () => {
    const base = { id: 'x', file: md(), status: 'uploading' as const };
    expect(toPercent({ ...base, stage: undefined })).toBeNull();
    expect(toPercent({ ...base, stage: 'saved' })).toBe(6);
    expect(toPercent({ ...base, stage: 'parsed' })).toBe(12);
    expect(toPercent({ ...base, stage: 'split' })).toBe(20);
    expect(toPercent({ ...base, stage: 'embedding', current: 5, total: 10 })).toBe(60);
    expect(toPercent({ ...base, status: 'done' })).toBe(100);
    expect(toPercent({ ...base, status: 'error' })).toBeNull();
  });
});
