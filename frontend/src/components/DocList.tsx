import type { KbBuiltinSummary, KbDocument } from '../api/types';
import { Button } from '../ui/Button';
import { IconButton } from '../ui/IconButton';
import { errorBoxCls } from '../ui/cls';
import { IconRefresh, IconTrash } from '../ui/icons';

/** builtin 摘要行（可选）+ 上传文档列表 + 删除 + 错误横幅。
 *  builtin 缺失时不渲染摘要行（契约 §4.5：整个对象可选）。
 *
 *  「刷新」与「删除」改用 ui/ 基元（Button / IconButton）：这里是全站最后两处
 *  自成一体的控件（手写的蓝色下划线链接 + 裸 🗑 emoji），现在与 Header、抽屉关闭键
 *  共用同一套 hover / 焦点环 / 危险色，风格改一次只动 ui/cls.ts。
 */
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
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="text-xs font-semibold tracking-wide text-gray-600">我的文档</h3>
        <Button variant="ghost" size="sm" onClick={onRefresh} title="重新拉取文档列表">
          <IconRefresh size={12} />
          刷新
        </Button>
      </div>

      {builtin && (
        <p className="mb-2 rounded-md bg-gray-50 px-2 py-1 text-[11px] text-gray-600" data-testid="builtin-summary">
          预置文档 {builtin.docs} 篇 / {builtin.chunks} 段（只读）
        </p>
      )}

      {/* 错误一律 role="alert"：抽屉可能刚打开、焦点还在别处，只让文字变红
          等于「失败只对看得见的人可见」。 */}
      {error && <p role="alert" className={`mb-2 ${errorBoxCls}`}>列表加载失败：{error}</p>}
      {deleteError && <p role="alert" className={`mb-2 ${errorBoxCls}`}>删除失败：{deleteError}</p>}

      {loading && documents.length === 0 ? (
        <p className="text-xs text-gray-500">加载中…</p>
      ) : documents.length === 0 ? (
        <p className="text-xs text-gray-500">还没有上传任何文档</p>
      ) : (
        <ul className="space-y-1.5">
          {documents.map(d => (
            <li
              key={d.doc_id}
              className="flex items-center justify-between gap-2 rounded-lg border border-orange-100 bg-orange-50/40 px-2.5 py-1.5"
            >
              <div className="min-w-0">
                <p className="truncate text-xs font-medium text-gray-700" title={d.title ?? d.filename}>
                  {d.title ?? d.filename}
                </p>
                <p className="text-[11px] text-gray-500">
                  {d.chunks} 段
                  {typeof d.size_bytes === 'number' ? ` · ${formatBytes(d.size_bytes)}` : ''}
                  {' · '}
                  {formatDate(d.uploaded_at)}
                </p>
              </div>
              <IconButton
                aria-label={`删除 ${d.filename}`}
                title={`删除 ${d.filename}`}
                tone="danger"
                onClick={() => onDelete(d.doc_id)}
              ><IconTrash size={12} /></IconButton>
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
