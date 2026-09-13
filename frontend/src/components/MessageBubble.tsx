import type { Msg } from '../hooks/useChat';
import { StepTrace } from './StepTrace';
import { AnswerMarkdown } from './AnswerMarkdown';
import { SourceChips } from './SourceChips';

/** 未命中知识库时的警示横幅。grounded === false 才渲染（undefined = 还在流式中，不渲染）。 */
function UngroundedBanner() {
  return (
    <p
      data-testid="ungrounded-banner"
      className="mb-2 flex items-start gap-1.5 rounded-md border border-amber-300 bg-amber-50 px-2 py-1.5 text-xs leading-relaxed text-amber-800"
    >
      <span aria-hidden>⚠</span>
      <span>本回答<strong>未命中知识库</strong>，基于模型通用知识生成，可能过时或有误，请自行核实。</span>
    </p>
  );
}

export function MessageBubble({ msg }: { msg: Msg }) {
  if (msg.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-lg bg-green-100 px-3 py-2 text-sm text-gray-800">{msg.content}</div>
      </div>
    );
  }
  const ungrounded = msg.grounded === false;
  return (
    <div className="flex justify-start">
      <div className={`max-w-[85%] rounded-lg border bg-white px-3 py-2 ${ungrounded ? 'border-amber-300' : ''}`}>
        <StepTrace steps={msg.steps} />
        {ungrounded && <UngroundedBanner />}
        {msg.status === 'streaming' && msg.answer === '' ? (
          <p className="text-sm text-gray-400">思考中…</p>
        ) : (
          <AnswerMarkdown text={msg.answer} />
        )}
        {msg.status === 'error' ? (
          <p className="mt-1 text-xs text-red-600">⚠ {msg.error ?? '出错了'}</p>
        ) : null}
        {ungrounded && msg.sources.length === 0 ? (
          <p className="mt-2 text-xs text-gray-400" data-testid="no-source-hint">无知识库来源</p>
        ) : (
          <SourceChips sources={msg.sources} />
        )}
      </div>
    </div>
  );
}
