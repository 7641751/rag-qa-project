import { Suspense, lazy, useCallback, useState } from 'react';
import { useChat } from './hooks/useChat';
import { useKnowledgeBase } from './hooks/useKnowledgeBase';
import { useAuth } from './hooks/useAuth';
import { useConversations } from './hooks/useConversations';
import { useSidebar } from './hooks/useSidebar';
import { LoginPage } from './components/LoginPage';
import { Sidebar } from './components/Sidebar';
import { Header } from './components/Header';
import { ChatWindow } from './components/ChatWindow';
import { Composer } from './components/Composer';
import { ConfirmDialog } from './components/ConfirmDialog';

// 知识库面板按需加载：它是 App 里唯一「不打开就永远用不到」的大块 UI。
// 首次点开「📚 知识库」时才下载这块代码（抽屉 + 上传区 + 文档列表），首屏少解析一截。
// ⚠ 测试兼容点：app.test 用的是 `await waitFor(getByTestId('upload-zone'))` 重试式断言，
//   动态 import 在下一个轮询周期即出现，不会红。
const KnowledgeBasePanel = lazy(() => import('./components/KnowledgeBasePanel'));

/** 鉴权门槛：loading → 校验中，anon → 登录页，authed → 主界面。 */
export default function App() {
  const auth = useAuth();

  // 有 token 时先显示「校验中」而不是直接放行主界面：
  // 直接放行会让 token 已过期时先闪一下主界面、再被 401 拽回登录页。
  if (auth.status === 'loading') {
    return (
      <div
        data-testid="auth-loading"
        className="flex min-h-screen items-center justify-center bg-gray-50 text-sm text-gray-400"
      >正在校验登录状态…</div>
    );
  }

  if (auth.status === 'anon') {
    return <LoginPage onLogin={auth.login} onRegister={auth.register} error={auth.error} />;
  }

  return <AuthedApp onLogout={auth.logout} username={auth.user?.username ?? null} />;
}

interface AuthedAppProps {
  onLogout: () => void;
  username?: string | null;
}

/** 已登录后的主界面：左侧会话列表 + 右侧聊天区。
 *
 *  刻意拆成独立组件，而不是在 App 里做条件渲染：这几个 hook 一挂载就发请求
 *  （fetchHistory / fetchDocuments / fetchThreads）。若把它们留在 App 顶层，未登录时也会
 *  各发一次注定 401 的请求 —— 白白在控制台刷红字，还会让「刚打开页面就报错」误导排查。
 *  Hooks 不能条件调用，所以「拆组件」是唯一干净的解法。
 */
function AuthedApp({ onLogout, username }: AuthedAppProps) {
  // useConversations 先于 useChat：后者的 onThreadTouched 要接前者的 refresh
  const conv = useConversations();
  const {
    messages, streaming, send, abort, newChat,
    deleteChat, deleting, deleteError, clearDeleteError,
    threadId, setThreadId,
  } = useChat({ onThreadTouched: conv.refresh });
  // 知识库状态住在这里（而非抽屉内部），所以关掉抽屉上传仍继续跑（契约 §4.9）
  const kb = useKnowledgeBase();
  const sb = useSidebar();

  /** 待确认删除的会话；null = 弹窗关闭。
   *  刻意存 **threadId 而不是 boolean** —— 侧栏每一行都能发起删除，弹窗必须知道删的是哪一条。 */
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);

  const switchTo = useCallback((id: string) => {
    if (id === threadId) return;      // 已经在目标会话，别白拉一次历史
    // abort 是必须的：SSE 回调直接 dispatch 到「最后一条 assistant 消息」，
    // 不中断的话旧会话的 token 会追加到新会话的气泡里（§10.3）。
    abort();
    setThreadId(id);                  // useChat 的 effect([threadId]) 自动 fetchHistory 回填
  }, [abort, setThreadId, threadId]);

  const pendingTitle = pendingDelete
    ? conv.threads.find(t => t.thread_id === pendingDelete)?.title
    : undefined;
  const deletingCurrent = pendingDelete !== null && pendingDelete === threadId;

  // ConversationItem 是 memo 组件：传给它的回调若每次渲染都新建（内联箭头），
  // memo 就永远失效。所以这里必须 useCallback。
  const requestDelete = useCallback((id: string) => {
    clearDeleteError();
    setPendingDelete(id);
  }, [clearDeleteError]);

  const confirmDelete = useCallback(async () => {
    const id = pendingDelete;
    if (!id) return;
    // 两条路径刻意分开（§10.6）：
    //  当前会话 → useChat.deleteChat：它先 abort 在飞的流、等流真正结束才发 DELETE，
    //             成功后清空消息区。顺序是硬约束，否则被取消的 astream 可能在 DELETE
    //             之后才写 checkpoint，留下删不掉的残行。
    //  其它会话 → conv.remove：只动列表，聊天区不受影响。
    const ok = id === threadId ? await deleteChat() : await conv.remove(id);
    if (!ok) return;                  // 失败：弹窗保持打开、行内错误可见，用户可重试

    setPendingDelete(null);
    if (id === threadId) {
      // 当前会话已删：切到列表第一条（此时列表已不含刚删的那条），列表空则开新会话
      const rest = conv.threads.filter(t => t.thread_id !== id);
      if (rest[0]) switchTo(rest[0].thread_id); else newChat();
    }
    void conv.refresh();              // 两种路径都要拿准 total（乐观更新不改 total）
  }, [pendingDelete, threadId, deleteChat, conv, switchTo, newChat]);

  return (
    // 横向外壳：Sidebar + 主区。主区的 min-w-0 是关键 —— flex 子项默认 min-width:auto，
    // 长标题（会话标题 / 代码块）会把侧栏顶开、把对话区挤出屏幕。
    <div className="flex h-screen bg-gray-50">
      <Sidebar
        collapsed={sb.collapsed}
        onToggle={sb.toggle}
        threads={conv.threads}
        total={conv.total}
        loading={conv.loading}
        error={conv.error}
        itemError={conv.itemError}
        activeId={threadId}
        onSelect={switchTo}
        onRename={conv.rename}
        onDelete={requestDelete}
        onRetry={conv.refresh}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <Header
          onNewChat={newChat}
          onOpenKb={kb.openKb}
          kbBadge={kb.activeCount}
          canDelete={messages.length > 0 || streaming}
          onDeleteChat={() => { clearDeleteError(); setPendingDelete(threadId); }}
          username={username}
          // 正在生成时先 abort 再退出：否则那条流还在跑（后端白跑一次 LLM 调用），
          // 而组件已卸载，这半截回答再也回不到界面上。
          onLogout={() => { if (streaming) abort(); onLogout(); }}
        />
        <ChatWindow messages={messages} />
        <Composer streaming={streaming} onSend={send} onStop={abort} />
      </div>

      {kb.open && (
        <Suspense fallback={null}>
          <KnowledgeBasePanel kb={kb} />
        </Suspense>
      )}

      <ConfirmDialog
        open={pendingDelete !== null}
        title="删除这个会话？"
        body={deletingCurrent
          ? '将同时删除服务端保存的历史，此操作不可恢复。'
          : `将删除「${pendingTitle ?? '该会话'}」，此操作不可恢复。`}
        busy={deleting}
        error={deleteError}
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}
