import { memo, useState } from 'react';
import type { Step } from '../api/types';
import { IconChevronRight, IconSearch } from '../ui/icons';

export const StepTrace = memo(function StepTrace({ steps }: { steps: Step[] }) {
  const [open, setOpen] = useState(false);
  if (steps.length === 0) return null;
  return (
    <div className="mb-2 text-xs">
      <button
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        className="flex items-center gap-1 rounded bg-blue-50 px-2 py-1 text-blue-700 transition-colors hover:bg-blue-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60"
      >
        <IconChevronRight size={11} className={`transition-transform ${open ? 'rotate-90' : ''}`} />
        <IconSearch size={11} />
        <span>推理步骤（{steps.length}）</span>
      </button>
      {open && (
        <ol className="mt-1 space-y-0.5 border-l-2 border-blue-100 pl-3">
          {steps.map((s, i) => (
            <li key={i} className="text-gray-600">
              <span className="font-medium">{s.label}</span>
              {s.detail ? <span className="text-gray-500"> · {s.detail}</span> : null}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
});
