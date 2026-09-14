import { useState } from 'react';
import { useChat } from './hooks/useChat';
import { useKnowledgeBase } from './hooks/useKnowledgeBase';
import { Header } from './components/Header';
import { ChatWindow } from './components/ChatWindow';
import { Composer } from './components/Composer';
import { KnowledgeBaseDrawer } from './components/KnowledgeBaseDrawer';
import { UploadZone } from './components/UploadZone';
import { DocList } from './components/DocList';
import { ConfirmDialog } from './components/ConfirmDialog';

export default function App() {
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
