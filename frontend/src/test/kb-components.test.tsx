import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { KnowledgeBaseDrawer } from '../components/KnowledgeBaseDrawer';
import { UploadZone } from '../components/UploadZone';
import { DocList } from '../components/DocList';
import type { UploadItem } from '../hooks/useKnowledgeBase';

const md = (name = 'a.md') => new File(['# hi'], name, { type: 'text/markdown' });
const item = (over: Partial<UploadItem> = {}): UploadItem => ({ id: 'u1', file: md(), status: 'pending', ...over });
const noop = () => {};
const docs = [{ doc_id: 'd1', filename: 'a.md', title: 'A 文档', chunks: 12, size_bytes: 2048, uploaded_at: '2026-09-11T10:00:00Z' }];

describe('KnowledgeBaseDrawer', () => {
  it('open=false 不渲染', () => {
    const { container } = render(
      <KnowledgeBaseDrawer open={false} onClose={noop}><span>x</span></KnowledgeBaseDrawer>,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('open=true 渲染 children；点遮罩关闭', () => {
    const onClose = vi.fn();
    render(<KnowledgeBaseDrawer open onClose={onClose}><span>面板内容</span></KnowledgeBaseDrawer>);
    expect(screen.getByText('面板内容')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('kb-overlay'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('Esc 关闭', () => {
    const onClose = vi.fn();
    render(<KnowledgeBaseDrawer open onClose={onClose}><span>x</span></KnowledgeBaseDrawer>);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe('UploadZone', () => {
  it('选择文件后回调 onFiles', () => {
    const onFiles = vi.fn();
    render(<UploadZone uploads={[]} onFiles={onFiles} onCancel={noop} onDismiss={noop} />);
    expect(screen.getByText(/拖拽或点击上传/)).toBeInTheDocument();
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [md()] } });
    expect(onFiles).toHaveBeenCalledTimes(1);
    expect(onFiles.mock.calls[0][0]).toHaveLength(1);
  });

  it('error 项显示原因且提供移除按钮', () => {
    const onDismiss = vi.fn();
    render(
      <UploadZone
        uploads={[item({ status: 'error', error: '不支持的格式，仅 .md / .txt / .pdf' })]}
        onFiles={noop} onCancel={noop} onDismiss={onDismiss}
      />,
    );
    expect(screen.getByText(/不支持的格式/)).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('移除'));
    expect(onDismiss).toHaveBeenCalledWith('u1');
  });

  it('uploading 项显示进度文案与取消按钮', () => {
    const onCancel = vi.fn();
    render(
      <UploadZone
        uploads={[item({ status: 'uploading', stage: 'embedding', current: 5, total: 10, message: '嵌入 5/10' })]}
        onFiles={noop} onCancel={onCancel} onDismiss={noop}
      />,
    );
    expect(screen.getByText('嵌入 5/10')).toBeInTheDocument();
    fireEvent.click(screen.getByText('取消'));
    expect(onCancel).toHaveBeenCalledWith('u1');
  });

  it('done 项提示已入库；replaced 项提示已更新', () => {
    render(
      <UploadZone
        uploads={[item({ id: 'a', status: 'done', docId: 'd1' }), item({ id: 'b', status: 'done', replaced: true })]}
        onFiles={noop} onCancel={noop} onDismiss={noop}
      />,
    );
    expect(screen.getByText('已入库')).toBeInTheDocument();
    expect(screen.getByText(/已更新/)).toBeInTheDocument();
  });
});

describe('DocList', () => {
  it('builtin 存在时渲染摘要行', () => {
    render(<DocList documents={docs} builtin={{ docs: 88, chunks: 2885 }} loading={false} onRefresh={noop} onDelete={noop} />);
    expect(screen.getByTestId('builtin-summary')).toHaveTextContent('88');
    expect(screen.getByTestId('builtin-summary')).toHaveTextContent('2885');
  });

  it('builtin 缺失时不渲染摘要行', () => {
    render(<DocList documents={docs} loading={false} onRefresh={noop} onDelete={noop} />);
    expect(screen.queryByTestId('builtin-summary')).not.toBeInTheDocument();
  });

  it('点删除回调 onDelete(doc_id)', () => {
    const onDelete = vi.fn();
    render(<DocList documents={docs} loading={false} onRefresh={noop} onDelete={onDelete} />);
    fireEvent.click(screen.getByLabelText('删除 a.md'));
    expect(onDelete).toHaveBeenCalledWith('d1');
  });

  it('空列表显示提示；listError 显示横幅', () => {
    render(<DocList documents={[]} loading={false} error="kb list HTTP 500" onRefresh={noop} onDelete={noop} />);
    expect(screen.getByText('还没有上传任何文档')).toBeInTheDocument();
    expect(screen.getByText(/列表加载失败/)).toBeInTheDocument();
  });
});
