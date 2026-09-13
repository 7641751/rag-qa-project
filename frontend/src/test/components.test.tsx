import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { StepTrace } from '../components/StepTrace';
import { SourceChips } from '../components/SourceChips';
import { AnswerMarkdown } from '../components/AnswerMarkdown';
import { MessageBubble } from '../components/MessageBubble';

describe('StepTrace', () => {
  it('无步骤不渲染', () => {
    const { container } = render(<StepTrace steps={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
  it('点击展开显示步骤 label 与 detail', () => {
    render(<StepTrace steps={[{ node: 'retrieve', label: '检索', detail: '召回 8 段' }]} />);
    fireEvent.click(screen.getByText(/推理步骤/));
    expect(screen.getByText('检索')).toBeInTheDocument();
    expect(screen.getByText(/召回 8 段/)).toBeInTheDocument();
  });
});

describe('SourceChips', () => {
  it('渲染来源标题与分数', () => {
    render(<SourceChips sources={[{ title: '01_加载.md', score: 0.82 }]} />);
    expect(screen.getByText('01_加载.md')).toBeInTheDocument();
    expect(screen.getByText('0.82')).toBeInTheDocument();
  });
});

describe('AnswerMarkdown', () => {
  it('渲染 markdown 标题与代码块', () => {
    const { container } = render(<AnswerMarkdown text={'# 标题\n\n```py\nprint(1)\n```'} />);
    expect(container.querySelector('h1')).not.toBeNull();
    expect(container.querySelector('code')).not.toBeNull();
  });
  it('行内 LaTeX $...$ 渲染为 KaTeX，定界符不再裸露', () => {
    const { container } = render(<AnswerMarkdown text={'向量空间 $V$ 与 $\\mathbb{R}$'} />);
    expect(container.querySelectorAll('.katex').length).toBeGreaterThanOrEqual(2);
    expect(container.querySelector('.katex-html')).not.toBeNull();   // 可视层已生成
    expect(container.textContent).not.toContain('$');                // $ 定界符已被消费
    // 注：KaTeX 会把原始 LaTeX 保留在 MathML <annotation> 里（无障碍/复制用），
    // 所以 textContent 里出现 \mathbb 是正常的，不能拿它当“没渲染”的判据。
  });
  it('块级公式：$$ 独占行时渲染为 display 模式', () => {
    const { container } = render(<AnswerMarkdown text={'$$\nE = mc^2\n$$'} />);
    expect(container.querySelector('.katex-display')).not.toBeNull();
  });
  it('块级公式：$$ 与内容同一行时仍渲染（inline 样式，非 display）', () => {
    const { container } = render(<AnswerMarkdown text={'$$E = mc^2$$'} />);
    expect(container.querySelector('.katex')).not.toBeNull();
    expect(container.textContent).not.toContain('$$');
  });
  it('流式途中半截公式不崩组件（降级为文本）', () => {
    const { container } = render(<AnswerMarkdown text={'加法 $+ : V \\times V \\t'} />);
    expect(container).not.toBeEmptyDOMElement();
    expect(container.textContent).toContain('加法');
  });
  it('LaTeX 与 GFM 表格、代码块共存', () => {
    const { container } = render(
      <AnswerMarkdown text={'| a | b |\n|---|---|\n| $x$ | 1 |\n\n```py\nprint(1)\n```'} />,
    );
    expect(container.querySelector('table')).not.toBeNull();
    expect(container.querySelector('.katex')).not.toBeNull();
    expect(container.querySelector('code')).not.toBeNull();
  });
});

describe('MessageBubble', () => {
  it('user 气泡显示内容', () => {
    render(<MessageBubble msg={{ role: 'user', content: '你好' }} />);
    expect(screen.getByText('你好')).toBeInTheDocument();
  });
  it('assistant streaming 空答案显示思考中', () => {
    render(<MessageBubble msg={{ role: 'assistant', steps: [], answer: '', sources: [], status: 'streaming' }} />);
    expect(screen.getByText('思考中…')).toBeInTheDocument();
  });
  it('grounded=false 渲染未验证横幅与「无知识库来源」', () => {
    render(
      <MessageBubble
        msg={{ role: 'assistant', steps: [], answer: '⚠ 基于通用知识…', sources: [], status: 'done', grounded: false }}
      />,
    );
    expect(screen.getByTestId('ungrounded-banner')).toBeInTheDocument();
    expect(screen.getByTestId('ungrounded-banner')).toHaveTextContent('未命中知识库');
    expect(screen.getByTestId('no-source-hint')).toBeInTheDocument();
  });
  it('grounded=true 不渲染横幅，正常显示来源芯片', () => {
    render(
      <MessageBubble
        msg={{ role: 'assistant', steps: [], answer: '答案', sources: [{ title: 'a.md' }], status: 'done', grounded: true }}
      />,
    );
    expect(screen.queryByTestId('ungrounded-banner')).not.toBeInTheDocument();
    expect(screen.getByText('a.md')).toBeInTheDocument();
  });
  it('grounded 未定（还在流式中）不渲染横幅', () => {
    render(
      <MessageBubble msg={{ role: 'assistant', steps: [], answer: '正在答…', sources: [], status: 'streaming' }} />,
    );
    expect(screen.queryByTestId('ungrounded-banner')).not.toBeInTheDocument();
  });
});
