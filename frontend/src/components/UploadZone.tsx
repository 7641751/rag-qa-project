import { useRef, useState } from 'react';
import type { UploadItem } from '../hooks/useKnowledgeBase';
import { toPercent } from '../hooks/useKnowledgeBase';
import { Button } from '../ui/Button';
import { IconButton } from '../ui/IconButton';
import { IconUpload, IconX } from '../ui/icons';

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
  const openPicker = () => inputRef.current?.click();

  return (
    <section>
      {/* 无障碍：原先是「纯 div + onClick」——Tab 键聚焦不到、回车/空格无效，
          对只用键盘的用户等于「上传功能不存在」。role="button" + tabIndex + 键盘处理
          是这类非原生按钮的标准补偿（用 <label> 包 file input 也能点，但 label 不进
          Tab 顺序，键盘用户依然够不到）。 */}
      <div
        data-testid="upload-zone"
        role="button"
        tabIndex={0}
        aria-label="上传文档：可拖拽文件到此处，或按回车选择文件"
        onClick={openPicker}
        onKeyDown={e => {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openPicker(); }
        }}
        onDragOver={e => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={e => { e.preventDefault(); setDragging(false); pick(e.dataTransfer.files); }}
        className={`cursor-pointer rounded-xl border-2 border-dashed px-4 py-6 text-center transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 ${
          dragging ? 'border-blue-500 bg-blue-50' : 'border-blue-200 bg-blue-50/40 hover:bg-blue-50'
        }`}
      >
        <IconUpload size={20} className="mx-auto text-blue-600" />
        <p className="mt-1.5 text-sm font-medium text-blue-700">拖拽或点击上传</p>
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
    <li className="rounded-lg border bg-white px-2.5 py-2 text-xs">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate font-medium text-gray-700" title={item.file.name}>{item.file.name}</span>
        {active ? (
          <Button variant="dangerGhost" size="sm" onClick={() => onCancel(item.id)}>取消</Button>
        ) : (
          <IconButton aria-label="移除" title="移除" onClick={() => onDismiss(item.id)}>
            <IconX size={12} />
          </IconButton>
        )}
      </div>

      {active && (
        <div
          role="progressbar"
          aria-label={`${item.file.name} 上传进度`}
          aria-valuemin={0}
          aria-valuemax={100}
          // pct 为 null 表示阶段尚未给出可算比例 → 不设 valuenow，即 ARIA 的「不定进度」
          aria-valuenow={pct ?? undefined}
          className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-gray-100"
        >
          {pct === null ? (
            <div className="h-full w-1/3 animate-pulse rounded-full bg-blue-400" />
          ) : (
            <div className="h-full rounded-full bg-blue-500 transition-all" style={{ width: `${pct}%` }} />
          )}
        </div>
      )}

      <p className={`mt-1 ${item.cancelled ? 'text-gray-500' : item.status === 'error' ? 'text-red-600' : 'text-gray-500'}`}>
        {item.status === 'pending' && '排队中…'}
        {item.status === 'uploading' && (item.message ?? '处理中…')}
        {item.status === 'done' && (item.replaced ? '已更新（替换同名文档）' : '已入库')}
        {item.status === 'error' && (item.error ?? '上传失败')}
      </p>
    </li>
  );
}
