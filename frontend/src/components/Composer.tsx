import { useState } from 'react';

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
    <footer className="border-t bg-white p-3">
      {/* 宽度必须与 ChatWindow 保持一致（同为 max-w-5xl），否则输入框与消息列会错位 */}
      <div className="mx-auto flex w-full max-w-5xl gap-2">
        <input
          className="flex-1 rounded-md border px-3 py-2 text-sm"
          placeholder="输入问题…"
          value={value}
          maxLength={4000}
          onChange={e => setValue(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); } }}
        />
        {streaming ? (
          <button onClick={onStop} className="rounded-md bg-red-500 px-4 py-2 text-sm text-white hover:bg-red-600">停止</button>
        ) : (
          <button onClick={submit} className="rounded-md bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700">发送</button>
        )}
      </div>
    </footer>
  );
}
