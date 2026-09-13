import type { KbBuiltinSummary, KbDocument } from '../api/types';

/** builtin 摘要行（可选）+ 上传文档列表 + 删除 + 错误横幅。
 *  builtin 缺失时不渲染摘要行（契约 §4.5：整个对象可选）。 */
export function DocList({ documents, builtin, loading, error, deleteError, onRefresh, onDelete }: {
  documents: KbDocument[];
  builtin?: KbBuiltinSummary;
  loading: boolean;
  error?: string;
  deleteError?: string;
  onRefresh: () => void;
  onDelete: (docId: string) => void;
}) {
  return (
    <section className="mt-4">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">我的文档</h3>
        <button onClick={onRefresh} className="text-xs text-blue-600 hover:underline">刷新</button>
      </div>

      {builtin && (
        <p className="mb-2 rounded bg-gray-50 px-2 py-1 text-[11px] text-gray-500" data-testid="builtin-summary">
          预置文档 {builtin.docs} 篇 / {builtin.chunks} 段（只读）
        </p>
      )}

      {error && (
        <p className="mb-2 rounded bg-red-50 px-2 py-1 text-[11px] text-red-600">列表加载失败：{error}</p>
      )}
      {deleteError && (
        <p className="mb-2 rounded bg-red-50 px-2 py-1 text-[11px] text-red-600">删除失败：{deleteError}</p>
      )}

      {loading && documents.length === 0 ? (
        <p className="text-xs text-gray-400">加载中…</p>
      ) : documents.length === 0 ? (
        <p className="text-xs text-gray-400">还没有上传任何文档</p>
      ) : (
        <ul className="space-y-1.5">
          {documents.map(d => (
            <li
              key={d.doc_id}
              className="flex items-center justify-between gap-2 rounded-md border border-orange-100 bg-orange-50/50 px-2 py-1.5"
            >
              <div className="min-w-0">
                <p className="truncate text-xs font-medium text-gray-700" title={d.title ?? d.filename}>
                  {d.title ?? d.filename}
                </p>
                <p className="text-[11px] text-gray-400">
                  {d.chunks} 段
                  {typeof d.size_bytes === 'number' ? ` · ${formatBytes(d.size_bytes)}` : ''}
                  {' · '}
                  {formatDate(d.uploaded_at)}
                </p>
              </div>
              <button
                onClick={() => onDelete(d.doc_id)}
                aria-label={`删除 ${d.filename}`}
                className="shrink-0 rounded px-1.5 py-0.5 text-xs text-gray-400 hover:bg-red-50 hover:text-red-500"
              >🗑</button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/** 后端时间格式不合预期时原样显示，不抛错 */
function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => (n < 10 ? `0${n}` : String(n));
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
