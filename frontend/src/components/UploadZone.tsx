import { useRef, useState } from 'react';
import type { UploadItem } from '../hooks/useKnowledgeBase';
import { toPercent } from '../hooks/useKnowledgeBase';

/** 拖拽/点选上传区 + 队列条目（进度条 / 状态 / 取消 / 移除）。
 *  不合格的文件也会出现在队列里并标 error（由 useKnowledgeBase 拦下），
 *  因此本组件只管展示，不重复校验。 */
export function UploadZone({ uploads, onFiles, onCancel, onDismiss }: {
  uploads: UploadItem[];
  onFiles: (files: File[]) => void;
  onCancel: (id: string) => void;
  onDismiss: (id: string) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const pick = (list: FileList | null) => { if (list && list.length > 0) onFiles(Array.from(list)); };

  return (
    <section>
      <div
        data-testid="upload-zone"
        onClick={() => inputRef.current?.click()}
        onDragOver={e => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={e => { e.preventDefault(); setDragging(false); pick(e.dataTransfer.files); }}
        className={`cursor-pointer rounded-lg border-2 border-dashed px-3 py-5 text-center transition-colors ${
          dragging ? 'border-blue-500 bg-blue-50' : 'border-blue-200 bg-blue-50/40 hover:bg-blue-50'
        }`}
      >
        <p className="text-sm text-blue-700">⬆ 拖拽或点击上传</p>
        <p className="mt-1 text-[11px] text-gray-500">支持 .md / .txt / .pdf，单个 ≤ 20 MB，一次最多 5 个</p>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept=".md,.txt,.pdf"
          className="hidden"
          onChange={e => { pick(e.target.files); e.target.value = ''; }}
        />
      </div>

      {uploads.length > 0 && (
        <ul className="mt-3 space-y-1.5">
          {uploads.map(u => <UploadRow key={u.id} item={u} onCancel={onCancel} onDismiss={onDismiss} />)}
        </ul>
      )}
    </section>
  );
}

function UploadRow({ item, onCancel, onDismiss }: {
  item: UploadItem;
  onCancel: (id: string) => void;
  onDismiss: (id: string) => void;
}) {
  const pct = toPercent(item);
  const active = item.status === 'pending' || item.status === 'uploading';
  return (
    <li className="rounded-md border bg-white px-2 py-1.5 text-xs">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate font-medium text-gray-700" title={item.file.name}>{item.file.name}</span>
        {active ? (
          <button onClick={() => onCancel(item.id)} className="shrink-0 text-gray-400 hover:text-red-500">取消</button>
        ) : (
          <button onClick={() => onDismiss(item.id)} aria-label="移除" className="shrink-0 text-gray-400 hover:text-gray-600">✕</button>
        )}
      </div>

      {active && (
        <div className="mt-1 h-1.5 w-full overflow-hidden rounded bg-gray-100">
          {/* pct 为 null 表示阶段尚未给出可算比例 → 渲染不定进度，避免假百分比 */}
          {pct === null ? (
            <div className="h-full w-1/3 animate-pulse rounded bg-blue-400" />
          ) : (
            <div className="h-full rounded bg-blue-500 transition-all" style={{ width: `${pct}%` }} />
          )}
        </div>
      )}

      <p className={`mt-1 ${item.cancelled ? 'text-gray-400' : item.status === 'error' ? 'text-red-500' : 'text-gray-500'}`}>
        {item.status === 'pending' && '排队中…'}
        {item.status === 'uploading' && (item.message ?? '处理中…')}
        {item.status === 'done' && (item.replaced ? '已更新（替换同名文档）' : '已入库')}
        {item.status === 'error' && (item.error ?? '上传失败')}
      </p>
    </li>
  );
}
