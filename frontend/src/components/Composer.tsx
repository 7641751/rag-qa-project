import { useState } from 'react';
import { Button } from '../ui/Button';
import { inputCls } from '../ui/cls';
import { IconSend, IconStop } from '../ui/icons';

export function Composer({ streaming, onSend, onStop }: {
  streaming: boolean; onSend: (q: string) => void; onStop: () => void;
}) {
  const [value, setValue] = useState('');
  const submit = () => {
    const q = value.trim();
    if (!q || streaming) return;
    onSend(q);
    setValue('');
  };
  return (
    <footer className="border-t bg-white/95 p-3 shadow-[0_-1px_3px_rgba(0,0,0,0.04)]">
      {/* 宽度必须与 ChatWindow 保持一致（同为 max-w-5xl），否则输入框与消息列会错位 */}
      <div className="mx-auto flex w-full max-w-5xl gap-2">
        <input
          className={`flex-1 ${inputCls}`}
          placeholder="输入问题…"
          aria-label="输入问题"
          value={value}
          maxLength={4000}
          onChange={e => setValue(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); } }}
        />
        {streaming ? (
          <Button variant="danger" onClick={onStop}>
            <IconStop size={12} />
            停止
          </Button>
        ) : (
          <Button variant="primary" onClick={submit} disabled={!value.trim()}>
            <IconSend size={13} />
            发送
          </Button>
        )}
      </div>
    </footer>
  );
}
