import { useEffect } from 'react';
import type { ReactNode } from 'react';

/** 右侧抽屉：遮罩 + 面板。Esc 与点击遮罩均可关闭；open=false 时不渲染。
 *  注意：抽屉内容（上传队列）的状态住在 App 层的 useKnowledgeBase，
 *  因此关闭抽屉不会中断正在进行的上传（契约 §4.9）。 */
export function KnowledgeBaseDrawer({ open, onClose, children }: {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40">
      <div className="absolute inset-0 bg-slate-900/30" onClick={onClose} data-testid="kb-overlay" />
      <aside className="kb-drawer-panel absolute right-0 top-0 flex h-full w-full max-w-sm flex-col bg-white shadow-xl">
        <header className="flex items-center justify-between border-b px-4 py-3">
          <h2 className="text-sm font-semibold text-gray-800">📚 知识库</h2>
          <button
            onClick={onClose}
            aria-label="关闭"
            className="rounded-md px-2 py-1 text-xs text-gray-500 hover:bg-gray-100"
          >✕</button>
        </header>
        <div className="flex-1 overflow-y-auto p-3">{children}</div>
      </aside>
    </div>
  );
}
