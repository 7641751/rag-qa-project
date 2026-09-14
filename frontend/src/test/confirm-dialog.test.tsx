import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ConfirmDialog } from '../components/ConfirmDialog';

const base = {
  open: true,
  title: '删除当前对话？',
  body: '将同时删除服务端保存的历史，此操作不可恢复。',
  onConfirm: vi.fn(),
  onCancel: vi.fn(),
};

describe('ConfirmDialog', () => {
  it('open=false 时不渲染任何内容（含遮罩，避免拦截点击）', () => {
    const { container } = render(<ConfirmDialog {...base} open={false} />);
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByTestId('confirm-backdrop')).not.toBeInTheDocument();
  });

  it('初始焦点落在「取消」按钮（不可逆操作，防 Enter 误删）', () => {
    render(<ConfirmDialog {...base} />);
    expect(screen.getByTestId('confirm-cancel')).toHaveFocus();
  });

  it('Esc 触发 onCancel', () => {
    const onCancel = vi.fn();
    render(<ConfirmDialog {...base} onCancel={onCancel} />);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('点遮罩关闭，点面板本身不关闭（选中文字时不误触）', () => {
    const onCancel = vi.fn();
    render(<ConfirmDialog {...base} onCancel={onCancel} />);
    fireEvent.click(screen.getByTestId('confirm-dialog'));
    expect(onCancel).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('confirm-backdrop'));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('busy 时两按钮禁用、Esc 与遮罩均无效、确认文案变「删除中…」', () => {
    const onCancel = vi.fn();
    render(<ConfirmDialog {...base} busy onCancel={onCancel} />);
    expect(screen.getByTestId('confirm-ok')).toBeDisabled();
    expect(screen.getByTestId('confirm-cancel')).toBeDisabled();
    expect(screen.getByTestId('confirm-ok')).toHaveTextContent('删除中');
    fireEvent.keyDown(window, { key: 'Escape' });
    fireEvent.click(screen.getByTestId('confirm-backdrop'));
    expect(onCancel).not.toHaveBeenCalled();
  });

  it('error 非空时渲染错误行；点确认触发 onConfirm', () => {
    const onConfirm = vi.fn();
    render(<ConfirmDialog {...base} error="删除失败，请重试" onConfirm={onConfirm} />);
    expect(screen.getByTestId('confirm-error')).toHaveTextContent('删除失败，请重试');
    fireEvent.click(screen.getByTestId('confirm-ok'));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});
