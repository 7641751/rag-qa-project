import { memo, useEffect, useRef, useState } from 'react';
import type { ConversationSummary } from '../api/types';
import { RENAME_MAX_CHARS } from '../hooks/useConversations';
import { IconButton } from '../ui/IconButton';
import { IconPencil, IconTrash } from '../ui/icons';

/** ISO 时间 → 相对时间。「刚刚 / N 分钟前 / N 小时前 / N 天前」；超 30 天直接给日期。
 *
 *  **为什么超过 30 天不用「137 天前」**：那个数字要用户自己算，不如直接给 `2026-08-13`。
 *  日期取 ISO 串前 10 位而非 `toLocaleDateString()` —— 后者在不同时区/语言下渲染不同，
 *  会让测试变脆。
 *
 *  `now` 可注入：否则「刚刚」「5 分钟前」这类断言会随真实时间漂移。
 */
export function relTime(iso: string, now: number = Date.now()): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '';              // 脏数据不显示，而不是显示「NaN 天前」
  const MIN = 60_000, HOUR = 60 * MIN, DAY = 24 * HOUR;
  const d = now - t;
  if (d < MIN) return '刚刚';
  if (d < HOUR) return `${Math.floor(d / MIN)} 分钟前`;
  if (d < DAY) return `${Math.floor(d / HOUR)} 小时前`;
  if (d < 30 * DAY) return `${Math.floor(d / DAY)} 天前`;
  return iso.slice(0, 10);
}

export interface ConversationItemProps {
  conv: ConversationSummary;
  active: boolean;
  onSelect: (threadId: string) => void;
  onRename: (threadId: string, title: string) => void;
  onDelete: (threadId: string) => void;
  /** 该行自己的错误（来自 useConversations.itemError，已被 Sidebar 按 threadId 过滤过） */
  error?: string | null;
}

/** 侧栏里的单条会话：标题 + 相对时间 + hover/键盘聚焦出的 ✎ / 🗑。
 *
 *  操作按钮的可见性用**状态**而非纯 CSS hover：jsdom 不实现 :hover，纯 CSS 的话
 *  「hover 出按钮」这条行为就完全测不到（conversation-item.test.tsx 依赖此）。
 *  当前项（active）常显，键盘用户 Tab 到该行时同样显示（无障碍兜底）。
 */
export const ConversationItem = memo(function ConversationItem({
  conv, active, onSelect, onRename, onDelete, error = null,
}: ConversationItemProps) {
  const [hovered, setHovered] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(conv.title);
  const [localError, setLocalError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  /** 本轮编辑是否已收尾（提交或取消）。防止「Enter 提交」后 input 卸载又触发 onBlur，
   *  把同一次编辑提交两遍、发两条 PATCH。 */
  const settled = useRef(false);

  useEffect(() => {
    if (editing) { inputRef.current?.focus(); inputRef.current?.select(); }   // 预填并全选
  }, [editing]);

  function startEdit() {
    settled.current = false;
    setDraft(conv.title);
    setLocalError(null);
    setEditing(true);
  }

  function commit() {
    if (settled.current) return;
    const title = draft.trim();
    // 前端先拦空标题与超长（§10.5）：明知会被 422 就别白发一次请求。
    // 注意这里**不**置 settled —— 用户还在编辑态，改完还能再提交。
    if (!title) { setLocalError('标题不能为空'); return; }
    if (title.length > RENAME_MAX_CHARS) { setLocalError(`标题不能超过 ${RENAME_MAX_CHARS} 字`); return; }
    settled.current = true;
    setEditing(false);
    if (title !== conv.title) onRename(conv.thread_id, title);   // 没改就不发请求
  }

  function cancel() {
    settled.current = true;
    setEditing(false);
    setLocalError(null);
  }

  const showActions = hovered || active;
  // 本地校验提示优先：它针对「当前这次输入」，比上一次失败的旧错误更相关
  const shownError = localError ?? error;

  return (
    <div>
      <div
        data-testid="conv-item"
        data-active={active ? 'true' : 'false'}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        onFocus={e => {
          // 键盘可达性：hover 才出按钮的话，Tab 用户永远够不到 ✎/🗑。
          // focus 进入本行（含标题按钮）即视同 hover；离开整行才收起。
          if (e.currentTarget.contains(e.target)) setHovered(true);
        }}
        onBlur={e => {
          if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setHovered(false);
        }}
        className={`group flex items-center gap-1 rounded-md px-2 py-1.5 text-xs ${
          active ? 'bg-blue-50 text-gray-900' : 'text-gray-600 hover:bg-gray-100'
        }`}
      >
        {editing ? (
          <input
            ref={inputRef}
            data-testid="rename-input"
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') commit();
              else if (e.key === 'Escape') cancel();
            }}
            onBlur={commit}
            className="min-w-0 flex-1 rounded border px-1 py-0.5 text-xs outline-none focus:border-gray-400"
          />
        ) : (
          <button
            type="button"
            data-testid="conv-title"
            onClick={() => onSelect(conv.thread_id)}
            title={conv.title}
            // 当前会话：屏幕阅读器报「当前」而不是靠背景色暗示（颜色对读屏用户不可见）
            aria-current={active ? 'true' : undefined}
            className="min-w-0 flex-1 truncate text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60"
          >{conv.title}</button>
        )}

        {!editing && (
          <span data-testid="conv-time" className="shrink-0 text-[10px] text-gray-500">
            {relTime(conv.updated_at)}
          </span>
        )}

        {showActions && !editing && (
          <>
            <IconButton
              aria-label="重命名"
              title="重命名"
              data-testid="rename-btn"
              onClick={startEdit}
            ><IconPencil size={12} /></IconButton>
            <IconButton
              aria-label="删除这个会话"
              title="删除这个会话"
              tone="danger"
              data-testid="delete-btn"
              onClick={() => onDelete(conv.thread_id)}
            ><IconTrash size={12} /></IconButton>
          </>
        )}
      </div>

      {/* 行内错误要**看得见**：这是用户知道「刚才那次重命名/删除失败了」的唯一途径 */}
      {shownError && (
        <p
          data-testid={localError ? 'rename-error' : 'conv-error'}
          className="mx-2 mt-0.5 text-[10px] leading-relaxed text-red-600"
        >{shownError}</p>
      )}
    </div>
  );
});
