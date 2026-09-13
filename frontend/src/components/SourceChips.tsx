import type { Source } from '../api/types';

export function SourceChips({ sources }: { sources: Source[] }) {
  if (sources.length === 0) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {sources.map((s, i) => (
        <span
          key={i}
          title={s.snippet ?? s.title}
          className="inline-flex items-center gap-1 rounded-full border border-orange-200 bg-orange-50 px-2 py-0.5 text-xs text-orange-700"
        >
          <span>{i + 1}</span>
          <span className="max-w-[16rem] truncate">{s.title}</span>
          {typeof s.score === 'number' ? <span className="text-orange-400">{s.score.toFixed(2)}</span> : null}
        </span>
      ))}
    </div>
  );
}
