import { useEffect, useRef } from 'react';
import type { Msg } from '../hooks/useChat';
import { MessageBubble } from './MessageBubble';

export function ChatWindow({ messages }: { messages: Msg[] }) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);
  return (
    <main className="flex-1 overflow-y-auto p-4">
      {/* 宽度必须与 Composer 保持一致（同为 max-w-5xl），否则消息列与输入框会错位 */}
      <div className="mx-auto flex w-full max-w-5xl flex-col gap-3">
        {messages.length === 0 ? (
          <p className="mt-10 text-center text-sm text-gray-400">开始提问吧</p>
        ) : (
          messages.map((m, i) => <MessageBubble key={i} msg={m} />)
        )}
        <div ref={endRef} />
      </div>
    </main>
  );
}
