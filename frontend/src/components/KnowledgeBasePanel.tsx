import { KnowledgeBaseDrawer } from './KnowledgeBaseDrawer';
import { UploadZone } from './UploadZone';
import { DocList } from './DocList';
import type { useKnowledgeBase } from '../hooks/useKnowledgeBase';

type Kb = ReturnType<typeof useKnowledgeBase>;

/** 知识库抽屉的完整内容（Drawer + 上传区 + 文档列表）。
 *
 *  为什么单独成组件：它是 App 里唯一「不打开就永远用不到」的大块 UI，
 *  所以由 App 以 React.lazy 按需加载 —— 首次点开「📚 知识库」才会下载这块代码，
 *  首屏少解析一截。Suspense 的 fallback 是 null：遮罩/面板在下一个渲染周期出现，
 *  测试里 `waitFor(getByTestId('upload-zone'))` 的重试式断言能等到它。
 */
export default function KnowledgeBasePanel({ kb }: { kb: Kb }) {
  return (
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
  );
}
