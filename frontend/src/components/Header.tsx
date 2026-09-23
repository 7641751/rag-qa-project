import { Button } from '../ui/Button';
import { IconLogout, IconPlus, IconRobot, IconTrash } from '../ui/icons';

/** 顶部栏：标题 + 知识库入口（带进行中角标）+ 新对话 + 删除对话 + 退出登录
 *
 *  按钮顺序刻意把危险操作放最右，且与「新对话」相邻，便于对比两者语义：
 *    ＋ 新对话   = 开一段新的（换 thread_id，旧的留着）
 *    🗑 删除对话 = 把当前这段彻底清掉（调 DELETE，保留 thread_id）
 *  退出登录再往右并加了一道竖线隔开 —— 它紧邻「删除对话」，隔一下降低误点概率；
 *  同时它**不随 canDelete 禁用**：空会话也应该能退出。
 *
 *  响应式：三个带文字的按钮都用 `hidden sm:inline` 收起文字，只留图标。
 *  ⚠ 收起文字意味着**可访问名会变空**（display:none 的内容不计入 accessible name）
 *    —— 所以这三个按钮必须各带 aria-label，否则窄屏下屏幕阅读器只知道「按钮」。
 *    这也是 aria-label 与可见文字保持一致（或含之）的原因：WCAG 2.5.3「标签名匹配」。
 *
 *  ⚠ 「📚 知识库」四个字保留 emoji 与完整文字：测试用 getByText('📚 知识库') 精确匹配它
 *    （测试契约优先）。
 */
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
    <header className="flex items-center justify-between gap-2 border-b bg-white/95 px-3 py-2.5 shadow-sm backdrop-blur sm:px-4">
      {/* min-w-0 + truncate：窄屏下让标题先被压缩，而不是把右侧按钮挤出屏幕 */}
      <h1 className="flex min-w-0 items-center gap-1.5 text-sm font-semibold text-gray-800">
        <IconRobot size={17} className="shrink-0 text-blue-600" />
        <span className="truncate">LangChain 智能问答</span>
      </h1>
      <div className="flex shrink-0 items-center gap-2">
        <Button variant="ghost" size="sm" onClick={onOpenKb} className="relative">
          📚 知识库
          {kbBadge > 0 && (
            <span
              data-testid="kb-badge"
              aria-label={`${kbBadge} 个上传进行中`}
              className="absolute -right-1.5 -top-1.5 rounded-full bg-blue-500 px-1 text-[10px] leading-4 text-white"
            >↑{kbBadge}</span>
          )}
        </Button>
        <Button variant="ghost" size="sm" onClick={onNewChat} aria-label="新对话" title="开始一段新对话">
          <IconPlus size={13} />
          <span className="hidden sm:inline">新对话</span>
        </Button>
        <Button
          variant="dangerGhost"
          size="sm"
          onClick={onDeleteChat}
          disabled={!canDelete}
          data-testid="btn-delete-chat"
          aria-label="删除对话"
          title={canDelete ? '删除当前对话（不可恢复）' : '当前没有可删除的对话'}
        >
          <IconTrash size={13} />
          <span className="hidden sm:inline">删除对话</span>
        </Button>
        <div className="flex items-center gap-2 border-l pl-3">
          {username && (
            <span
              data-testid="current-user"
              className="hidden text-xs text-gray-500 sm:inline"
            >{username}</span>
          )}
          <Button
            variant="ghost"
            size="sm"
            onClick={onLogout}
            data-testid="btn-logout"
            aria-label="退出登录"
            title="退出登录（只清本地凭证，服务端历史保留）"
          >
            <IconLogout size={13} />
            <span className="hidden sm:inline">退出</span>
          </Button>
        </div>
      </div>
    </header>
  );
}
