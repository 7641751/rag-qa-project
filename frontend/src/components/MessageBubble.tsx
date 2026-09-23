import { memo } from 'react';
import type { Msg } from '../hooks/useChat';
import { StepTrace } from './StepTrace';
import { AnswerMarkdown } from './AnswerMarkdown';
import { SourceChips } from './SourceChips';
import { IconAlert } from '../ui/icons';

/** 未命中知识库时的警示横幅。grounded === false 才渲染（undefined = 还在流式中，不渲染）。 */
function UngroundedBanner() {
  return (
    <p
      data-testid="ungrounded-banner"
      role="alert"
      className="mb-2 flex items-start gap-1.5 rounded-md border border-amber-300 bg-amber-50 px-2 py-1.5 text-xs leading-relaxed text-amber-800"
    >
      <IconAlert size={13} className="mt-0.5 shrink-0" />
      <span>本回答<strong>未命中知识库</strong>，基于模型通用知识生成，可能过时或有误，请自行核实。</span>
    </p>
  );
}

/** memo 的收益点：流式时每个 token 都让整个列表重渲染，而 useChat 的
 *  updateLastAssistant 只替换**最后一条** assistant 消息（其余保持引用），
 *  所以老气泡全部走「props 没变 → 跳过渲染」。 */
export const MessageBubble = memo(function MessageBubble({ msg }: { msg: Msg }) {
  if (msg.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-2xl rounded-br-md bg-emerald-100 px-3.5 py-2 text-sm text-gray-800 shadow-sm">{msg.content}</div>
      </div>
    );
  }
  const ungrounded = msg.grounded === false;
  return (
    <div className="flex justify-start">
      <div className={`max-w-[85%] rounded-2xl rounded-bl-md border bg-white px-3.5 py-2.5 shadow-sm ${ungrounded ? 'border-amber-300' : 'border-gray-100'}`}>
        <StepTrace steps={msg.steps} />
        {ungrounded && <UngroundedBanner />}
        {msg.status === 'streaming' && msg.answer === '' ? (
          <p className="text-sm text-gray-500">思考中…</p>
        ) : (
          <AnswerMarkdown text={msg.answer} />
        )}
        {msg.status === 'error' ? (
          <p className="mt-1 flex items-center gap-1 text-xs text-red-600">
            <IconAlert size={12} />
            {msg.error ?? '出错了'}
          </p>
        ) : null}
        {ungrounded && msg.sources.length === 0 ? (
          <p className="mt-2 text-xs text-gray-500" data-testid="no-source-hint">无知识库来源</p>
        ) : (
          <SourceChips sources={msg.sources} />
        )}
      </div>
    </div>
  );
});
