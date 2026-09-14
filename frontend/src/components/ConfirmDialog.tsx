import { useEffect, useRef } from 'react';

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  body: string;
  confirmLabel?: string;
  cancelLabel?: string;
  busyLabel?: string;
  busy?: boolean;
  error?: string | null;
  onConfirm: () => void | Promise<void>;
  onCancel: () => void;
}

/** 通用确认模态框（不绑定「删除对话」语义，P3 删单条会话可复用）。
 *
 *  三条安全默认：
 *  ① 初始焦点落在「取消」——不可逆操作，防止用户按 Enter 直接确认删除；
 *  ② busy 期间 Esc / 遮罩 / 两个按钮全部失效——请求在飞时关窗会造成状态错乱；
 *  ③ 点面板本身不关闭（只有点遮罩才关），避免选中文本时误触。
 *
 *  z-50 高于知识库抽屉的 z-40（KnowledgeBaseDrawer.tsx:21），两者同时开时弹窗在上。
 *  Esc 监听写法与 KnowledgeBaseDrawer.tsx:12-17 一致（window keydown + 清理）。 */
export function ConfirmDialog({
  open, title, body,
  confirmLabel = '删除', cancelLabel = '取消', busyLabel = '删除中…',
  busy = false, error = null, onConfirm, onCancel,
}: ConfirmDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    cancelRef.current?.focus();                       // ①
  }, [open]);

  useEffect(() => {
    if (!open || busy) return;                        // ②
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, busy, onCancel]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-slate-900/40"
        data-testid="confirm-backdrop"
        onClick={() => { if (!busy) onCancel(); }}
      />
      <div
        role="dialog" aria-modal="true" aria-labelledby="confirm-dialog-title"
        data-testid="confirm-dialog"
        className="dialog-pop relative w-full max-w-sm rounded-lg bg-white p-5 shadow-xl"
      >
        <h2 id="confirm-dialog-title" className="text-sm font-semibold text-gray-900">{title}</h2>
        <p className="mt-2 text-xs leading-relaxed text-gray-600">{body}</p>
        {error && (
          <p
            data-testid="confirm-error"
            className="mt-3 rounded-md border border-red-200 bg-red-50 px-2 py-1.5 text-xs leading-relaxed text-red-700"
          >{error}</p>
        )}
        <div className="mt-5 flex justify-end gap-2">
          <button
            ref={cancelRef}
            data-testid="confirm-cancel"
            disabled={busy}
            onClick={onCancel}
            className="rounded-md border px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-50"
          >{cancelLabel}</button>
          <button
            data-testid="confirm-ok"
            disabled={busy}
            onClick={onConfirm}
            className="rounded-md bg-red-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-50"
          >{busy ? busyLabel : confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}
