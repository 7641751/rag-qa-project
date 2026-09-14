/** 顶部栏：标题 + 知识库入口（带进行中角标）+ 新对话 + 删除对话
 *
 *  按钮顺序刻意把危险操作放最右，且与「新对话」相邻，便于对比两者语义：
 *    ＋ 新对话   = 开一段新的（换 thread_id，旧的留着）
 *    🗑 删除对话 = 把当前这段彻底清掉（调 DELETE，保留 thread_id）
 *  窄屏只留图标（hidden sm:inline），避免四个元素挤爆 Header。 */
export function Header({ onNewChat, onOpenKb, onDeleteChat, canDelete, kbBadge }: {
  onNewChat: () => void;
  onOpenKb: () => void;
  onDeleteChat: () => void;
  /** 空会话时禁用删除：没东西可删，不必发一次无意义请求 */
  canDelete: boolean;
  kbBadge: number;
}) {
  return (
    <header className="flex items-center justify-between border-b bg-white px-4 py-3">
      <h1 className="text-sm font-semibold text-gray-800">🤖 LangChain 智能问答</h1>
      <div className="flex items-center gap-2">
        <button
          onClick={onOpenKb}
          className="relative rounded-md border px-3 py-1 text-xs text-gray-600 hover:bg-gray-100"
        >
          📚 知识库
          {kbBadge > 0 && (
            <span
              data-testid="kb-badge"
              className="absolute -right-1.5 -top-1.5 rounded-full bg-blue-500 px-1 text-[10px] leading-4 text-white"
            >↑{kbBadge}</span>
          )}
        </button>
        <button onClick={onNewChat} className="rounded-md border px-3 py-1 text-xs text-gray-600 hover:bg-gray-100">
          ＋ 新对话
        </button>
        {/* ⚠ 文本被 <span> 拆成两个节点，测试必须用 data-testid，
            getByText('🗑 删除对话') 匹配不到 */}
        <button
          onClick={onDeleteChat}
          disabled={!canDelete}
          data-testid="btn-delete-chat"
          title={canDelete ? '删除当前对话（不可恢复）' : '当前没有可删除的对话'}
          className="rounded-md border border-red-200 px-3 py-1 text-xs text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:border-gray-200 disabled:text-gray-300 disabled:hover:bg-transparent"
        >
          🗑<span className="hidden sm:inline"> 删除对话</span>
        </button>
      </div>
    </header>
  );
}
