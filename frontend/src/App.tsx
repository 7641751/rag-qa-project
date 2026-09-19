import { useState } from 'react';
import { useChat } from './hooks/useChat';
import { useKnowledgeBase } from './hooks/useKnowledgeBase';
import { useAuth } from './hooks/useAuth';
import { LoginPage } from './components/LoginPage';
import { Header } from './components/Header';
import { ChatWindow } from './components/ChatWindow';
import { Composer } from './components/Composer';
import { KnowledgeBaseDrawer } from './components/KnowledgeBaseDrawer';
import { UploadZone } from './components/UploadZone';
import { DocList } from './components/DocList';
import { ConfirmDialog } from './components/ConfirmDialog';

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

/** 已登录后的主界面。
 *
 *  刻意拆成独立组件，而不是在 App 里做条件渲染：`useChat` / `useKnowledgeBase`
 *  一挂载就发请求（fetchHistory / fetchDocuments）。若把它们留在 App 顶层，未登录时也会
 *  各发一次注定 401 的请求 —— 白白在控制台刷两条红字，还会让「刚打开页面就报错」误导排查。
 *
 *  Hooks 不能条件调用，所以「拆组件」是这里唯一干净的解法。
 */
interface AuthedAppProps {
  onLogout: () => void;
  username?: string | null;
}

function AuthedApp({ onLogout, username }: AuthedAppProps) {
  const {
    messages, streaming, send, abort, newChat,
    deleteChat, deleting, deleteError, clearDeleteError,
  } = useChat();
  // 知识库状态住在这里（而非抽屉内部），所以关掉抽屉上传仍继续跑（契约 §4.9）
  const kb = useKnowledgeBase();
  // 弹窗开关也住 App 层（与 KB 抽屉同一层级决策）：ConfirmDialog 自身无状态，
  // 失败时靠 deleteError 留在弹窗里显示、弹窗不关；只有成功才关。
  const [confirmDelete, setConfirmDelete] = useState(false);

  return (
    <div className="flex h-screen flex-col bg-gray-50">
      <Header
        onNewChat={newChat}
        onOpenKb={kb.openKb}
        kbBadge={kb.activeCount}
        canDelete={messages.length > 0 || streaming}
        onDeleteChat={() => { clearDeleteError(); setConfirmDelete(true); }}
        username={username}
        // 正在生成时先 abort 再退出：否则那条流还在跑（后端白跑一次 LLM 调用），
        // 而组件已卸载，这半截回答再也回不到界面上。
        onLogout={() => { if (streaming) abort(); onLogout(); }}
      />
      <ChatWindow messages={messages} />
      <Composer streaming={streaming} onSend={send} onStop={abort} />
      <KnowledgeBaseDrawer open={kb.open} onClose={kb.closeKb}>
        <UploadZone
          uploads={kb.uploads}
          onFiles={kb.addFiles}
          onCancel={kb.cancelUpload}
          onDismiss={kb.dismiss}
        />
        <DocList
          documents={kb.documents}
          builtin={kb.builtin}
          loading={kb.loadingList}
          error={kb.listError}
          deleteError={kb.docError}
          onRefresh={kb.refresh}
          onDelete={kb.removeDoc}
        />
      </KnowledgeBaseDrawer>
      {/* 渲染在抽屉之后且 z-50 > 抽屉的 z-40：两者同时开时弹窗在上 */}
      <ConfirmDialog
        open={confirmDelete}
        title="删除当前对话？"
        body="将同时删除服务端保存的历史，此操作不可恢复。"
        busy={deleting}
        error={deleteError}
        onConfirm={async () => { if (await deleteChat()) setConfirmDelete(false); }}
        onCancel={() => setConfirmDelete(false)}
      />
    </div>
  );
}
