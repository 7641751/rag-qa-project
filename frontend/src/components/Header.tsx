/** 顶部栏：标题 + 知识库入口（带进行中角标）+ 新对话 + 删除对话 + 退出登录
 *
 *  按钮顺序刻意把危险操作放最右，且与「新对话」相邻，便于对比两者语义：
 *    ＋ 新对话   = 开一段新的（换 thread_id，旧的留着）
 *    🗑 删除对话 = 把当前这段彻底清掉（调 DELETE，保留 thread_id）
 *  退出登录再往右并加了一道竖线隔开 —— 它紧邻「删除对话」，隔一下降低误点概率；
 *  同时它**不随 canDelete 禁用**：空会话也应该能退出。
 *  窄屏只留图标（hidden sm:inline），避免五个元素挤爆 Header。 */
export function Header({ onNewChat, onOpenKb, onDeleteChat, canDelete, kbBadge, onLogout, username }: {
  onNewChat: () => void;
  onOpenKb: () => void;
  onDeleteChat: () => void;
  /** 空会话时禁用删除：没东西可删，不必发一次无意义请求 */
  canDelete: boolean;
  kbBadge: number;
  /** 退出登录。只清本地凭证、不调后端 —— JWT 无状态，契约里没有 logout 端点（spec §2 非目标）。
   *  副作用是 token 在有效期内仍然有效，这是已知风险（spec §11），不是这里的缺陷。 */
  onLogout: () => void;
  /** 当前用户名，仅用于显示；未传时不渲染那一行 */
  username?: string | null;
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
        <div className="flex items-center gap-2 border-l pl-3">
          {username && (
            <span
              data-testid="current-user"
              className="hidden text-xs text-gray-400 sm:inline"
            >{username}</span>
          )}
          <button
            onClick={onLogout}
            data-testid="btn-logout"
            title="退出登录（只清本地凭证，服务端历史保留）"
            className="rounded-md border px-3 py-1 text-xs text-gray-500 hover:bg-gray-100"
          >
            🚪<span className="hidden sm:inline"> 退出</span>
          </button>
        </div>
      </div>
    </header>
  );
}
