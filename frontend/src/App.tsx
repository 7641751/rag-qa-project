import { useChat } from './hooks/useChat';
import { useKnowledgeBase } from './hooks/useKnowledgeBase';
import { Header } from './components/Header';
import { ChatWindow } from './components/ChatWindow';
import { Composer } from './components/Composer';
import { KnowledgeBaseDrawer } from './components/KnowledgeBaseDrawer';
import { UploadZone } from './components/UploadZone';
import { DocList } from './components/DocList';

export default function App() {
  const { messages, streaming, send, abort, newChat } = useChat();
  // 知识库状态住在这里（而非抽屉内部），所以关掉抽屉上传仍继续跑（契约 §4.9）
  const kb = useKnowledgeBase();
  return (
    <div className="flex h-screen flex-col bg-gray-50">
      <Header onNewChat={newChat} onOpenKb={kb.openKb} kbBadge={kb.activeCount} />
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
    </div>
  );
}
