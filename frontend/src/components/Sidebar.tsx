import type { ConversationSummary } from '../api/types';
import { LIST_LIMIT } from '../hooks/useConversations';
import { ConversationItem } from './ConversationItem';

export interface SidebarProps {
  collapsed: boolean;
  onToggle: () => void;
  threads: ConversationSummary[];
  total: number;
  loading: boolean;
  /** 列表级错误：整块没加载出来 */
  error?: string | null;
  /** 行级错误，已按 threadId 过滤好后才传给对应行 */
  itemError?: { threadId: string; message: string } | null;
  activeId: string;
  onSelect: (threadId: string) => void;
  onRename: (threadId: string, title: string) => void;
  onDelete: (threadId: string) => void;
  onRetry: () => void;
}

/** 左侧会话列表。
 *
 *  折叠用 `w-0` + 内容 `overflow-hidden`，把手绝对定位在侧栏右边缘 —— 这样折叠后
 *  仍有一个可点的入口，而不是整块消失后无从展开。`transition-[width]` 让折叠有动画。
 *  宽度用 `shrink-0`：否则主区的长内容会把它压扁（主区那边还要配 `min-w-0`，见 App.tsx）。
 */
export function Sidebar({
  collapsed, onToggle, threads, total, loading, error = null, itemError = null,
  activeId, onSelect, onRename, onDelete, onRetry,
}: SidebarProps) {
  const truncated = total > threads.length;

  return (
    <aside
      data-testid="sidebar"
      data-collapsed={collapsed ? 'true' : 'false'}
      className={`relative shrink-0 border-r bg-white transition-[width] duration-150 ${
        collapsed ? 'w-0' : 'w-[250px]'
      }`}
    >
      <div className={`h-full overflow-hidden ${collapsed ? '' : 'w-[250px]'}`}>
        <div className="flex h-full flex-col">
          <div className="flex items-center justify-between gap-2 border-b px-3 py-2.5">
            <span className="text-xs font-semibold text-gray-700">会话</span>
            {truncated && (
              <span data-testid="list-truncated" className="text-[10px] text-gray-400">
                仅显示最近 {LIST_LIMIT} 条
              </span>
            )}
          </div>

          <div className="flex-1 overflow-y-auto p-2">
            {loading && threads.length === 0 && (
              <p data-testid="sidebar-loading" className="px-2 py-3 text-xs text-gray-400">加载中…</p>
            )}

            {error && (
              <div data-testid="sidebar-error" className="px-2 py-3">
                <p className="text-xs leading-relaxed text-red-600">{error}</p>
                <button
                  type="button"
                  data-testid="sidebar-retry"
                  onClick={onRetry}
                  className="mt-1.5 rounded border px-2 py-0.5 text-[11px] text-gray-600 hover:bg-gray-100"
                >重试</button>
              </div>
            )}

            {!loading && !error && threads.length === 0 && (
              <p
                data-testid="sidebar-empty"
                className="px-2 py-6 text-center text-xs leading-relaxed text-gray-400"
              >还没有会话，问一句就开始</p>
            )}

            {threads.map(t => (
              <ConversationItem
                key={t.thread_id}
                conv={t}
                active={t.thread_id === activeId}
                onSelect={onSelect}
                onRename={onRename}
                onDelete={onDelete}
                error={itemError?.threadId === t.thread_id ? itemError.message : null}
              />
            ))}
          </div>
        </div>
      </div>

      {/* 把手挂在侧栏外沿：折叠后侧栏宽度为 0，若把手在内部就点不到了 */}
      <button
        type="button"
        data-testid="sidebar-toggle"
        onClick={onToggle}
        title={collapsed ? '展开会话列表' : '折叠会话列表'}
        className="absolute -right-3 top-4 z-10 flex h-6 w-6 items-center justify-center rounded-full border bg-white text-xs text-gray-500 shadow-sm hover:bg-gray-100"
      >{collapsed ? '»' : '«'}</button>
    </aside>
  );
}
