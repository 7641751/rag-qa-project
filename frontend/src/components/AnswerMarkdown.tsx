import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import rehypeHighlight from 'rehype-highlight';
import 'katex/dist/katex.min.css';

/** 答案 Markdown 渲染：GFM 表格 + LaTeX 数学公式（KaTeX）+ 代码高亮。
 *
 * 为什么需要 remark-math + rehype-katex：模型回答数学类问题时会输出 `$V$`、
 * `$\mathbb{R}$`、`$+ : V \times V \to V$` 这类 LaTeX 记号，react-markdown 默认
 * 只处理 CommonMark，不认识 `$...$`，会把它当普通文本原样吐出来（看着像乱码）。
 *
 * ⚠ 流式副作用（已实测）：答案逐 token 到达时公式可能是半截的（如 `$+ : V \times V \t`）。
 *   未闭合的 `$` 不会被 remark-math 识别为 math 节点，而是**原样当普通文本显示**
 *   （不闪红、不报错），等闭合的 `$` 到达后自动渲染成公式。只有“已闭合但语法非法”
 *   的公式才会触发 KaTeX 降级，此时 errorColor 把它渲染成灰色而非刺眼的红。
 *
 * 另：`$$...$$` 只有当 `$$` **独占行**时才走 display（居中放大）；与内容同一行时按 inline 渲染。
 */
export function AnswerMarkdown({ text }: { text: string }) {
  return (
    <div className="prose prose-sm max-w-none break-words">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[
          // strict:false 让不支持的 LaTeX 命令降级而不是报错；errorColor 把“已闭合但非法”
          // 的公式渲染成灰色而非刺眼的红（未闭合的公式压根不会进 KaTeX，见上方注释）
          [rehypeKatex, { strict: false, throwOnError: false, errorColor: '#6b7280' }],
          rehypeHighlight,
        ]}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
