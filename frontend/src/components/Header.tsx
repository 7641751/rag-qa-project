/** 顶部栏：标题 + 知识库入口（带进行中角标）+ 新对话 */
export function Header({ onNewChat, onOpenKb, kbBadge }: {
  onNewChat: () => void;
  onOpenKb: () => void;
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
      </div>
    </header>
  );
}
