import { useEffect, useRef } from 'react';
import type { Msg } from '../hooks/useChat';
import { MessageBubble } from './MessageBubble';

/** 距底多少像素内算「在底部」：用户在此范围内时新 token 才自动跟随滚动。 */
const NEAR_BOTTOM_PX = 120;

/** 自动滚动的最小间隔（ms）。流式时 token 以每秒几十次的频率到达，
 *  每次到达都调一次 `scrollIntoView({behavior:'smooth'})` 会让平滑动画在同一帧里
 *  反复排队、互相打断（表现为滚动卡顿、长答案掉帧）。 */
const SCROLL_THROTTLE_MS = 90;

/** 系统「减少动态效果」偏好的用户对平滑滚动会不适（前庭敏感）。
 *  jsdom 的 matchMedia 是 stub（可能返回 undefined 甚至抛），所以必须 try/catch。 */
function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true;
  } catch {
    return false;
  }
}

export function ChatWindow({ messages }: { messages: Msg[] }) {
  const endRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLElement>(null);
  /** 用户是否正看着底部。流式时每个 token 都触发本组件重渲染，
   *  若无条件 scrollIntoView，用户往上翻历史时会被不停拽回底部（已实测的坏体验）；
   *  且「smooth 滚动 + 每 token 触发」会产生持续的滚动动画与布局开销。 */
  const nearBottomRef = useRef(true);
  /** 已排程的滚动定时器。非 null = 这一轮已有一次滚动在路上，后续 token 直接合并掉。 */
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    if (!nearBottomRef.current) return;
    // 节流 + 尾沿触发：突发期间最多每 SCROLL_THROTTLE_MS 滚一次；
    // 且**不**在 cleanup 里清定时器 —— 否则流式期间 messages 每 30ms 变一次、
    // 定时器被反复清掉，一次都不会执行（等于彻底不滚动）。尾沿这一下保证
    // 最后一个 token 之后仍会补滚一次，答案末尾不会停在视口外。
    if (timerRef.current !== null) return;
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      endRef.current?.scrollIntoView({
        behavior: prefersReducedMotion() ? 'auto' : 'smooth',
        block: 'end',
      });
    }, SCROLL_THROTTLE_MS);
  }, [messages]);

  // 卸载时清掉在飞的定时器：组件都没了还在调 scrollIntoView 是无意义的引用。
  useEffect(() => () => {
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
  }, []);

  const handleScroll = () => {
    const el = containerRef.current;
    if (!el) return;
    nearBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX;
  };

  return (
    <main
      ref={containerRef}
      onScroll={handleScroll}
      className="flex-1 overflow-y-auto px-4 py-4"
    >
      {/* 宽度必须与 Composer 保持一致（同为 max-w-5xl），否则消息列与输入框会错位。
          role=log + aria-live=polite：流式答案到达时屏幕阅读器会按序朗读，
          但不过度打断（polite 会等当前话语结束）。 */}
      <div
        role="log"
        aria-live="polite"
        aria-label="对话消息"
        className="mx-auto flex w-full max-w-5xl flex-col gap-3"
      >
        {messages.length === 0 ? (
          <p className="mt-10 text-center text-sm text-gray-500">开始提问吧</p>
        ) : (
          messages.map((m, i) => <MessageBubble key={i} msg={m} />)
        )}
        <div ref={endRef} />
      </div>
    </main>
  );
}
