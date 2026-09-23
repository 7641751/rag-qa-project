/** 极简 rehype 代码高亮：**只注册本项目真正用得到的语言**。
 *
 *  ── 为什么不用 rehype-highlight（v7，约 40 行代码的差别换来 100+ kB）────────────
 *  rehype-highlight 把 lowlight 的 `common`（37 种语言）**静态**引入，并且写在
 *  `const languages = settings.languages || common` 这个**活表达式**里。打包器无法
 *  摇掉一个「被引用了的」对象，于是传入 `languages` 选项的真实语义是
 *  「在 37 种之上再加十几种」—— 实测主包从 784.65 kB 涨到 835.62 kB（+51 kB），
 *  是负优化。那个 defaults 是 `common` 的注释看上去像「可覆盖」，实则不能。
 *
 *  ── 这里怎么做到真裁剪 ────────────────────────────────────────────────────
 *  直接用 `createLowlight` 注册我们自己的子集：lowlight 的 package.json 声明了
 *  `sideEffects: false`，因此它未被引用的 `lib/all.js`（约 190 种语言）与
 *  `lib/common.js`（37 种）会被摇掉，语言数才真正从 37 降到 14。
 *
 *  ── 与 rehype-highlight 保持一致的三条行为 ────────────────────────────────
 *  ① 只处理 `<pre><code>` 里的 `code` 元素（class 形如 `language-xxx`）；
 *  ② 未标注语言 / 语言未注册 → **原样保留**（不高亮、不猜、不报错、不丢内容）。
 *     刻意不开 detect：猜错语言比不高亮更糟，rehype-highlight 的默认也是 detect:false；
 *  ③ 产出 hast，class 前缀仍是 `hljs-`，所以 `highlight.js/styles/github.css` 可直接用。
 *
 *  另外：lowlight 实例建在**模块级**（只建一次）。rehype-highlight 是在插件工厂里
 *  建实例的，而 rehype 插件每次渲染都会重新调用工厂 —— 等于每个 token 都重新注册
 *  一遍语法，属于白白的开销。
 */
import { createLowlight } from 'lowlight';
import javascript from 'highlight.js/lib/languages/javascript';
import typescript from 'highlight.js/lib/languages/typescript';
import python from 'highlight.js/lib/languages/python';
import bash from 'highlight.js/lib/languages/bash';
import json from 'highlight.js/lib/languages/json';
import markdown from 'highlight.js/lib/languages/markdown';
import yaml from 'highlight.js/lib/languages/yaml';
import xml from 'highlight.js/lib/languages/xml';
import css from 'highlight.js/lib/languages/css';
import sql from 'highlight.js/lib/languages/sql';
import ini from 'highlight.js/lib/languages/ini';
import http from 'highlight.js/lib/languages/http';
import dockerfile from 'highlight.js/lib/languages/dockerfile';
import plaintext from 'highlight.js/lib/languages/plaintext';

const lowlight = createLowlight({
  javascript, typescript, python, bash, json, markdown, yaml, xml, css, sql,
  ini, http, dockerfile, plaintext,
});

// 别名：模型爱写 ```js / ```ts / ```py / ```sh / ```yml / ```html。
// 不给别名的话「```python 有高亮、```py 没有」—— 那是体验倒退，不是优化。
lowlight.registerAlias({
  javascript: ['js', 'jsx'],
  typescript: ['ts', 'tsx'],
  python: ['py'],
  bash: ['sh', 'shell', 'zsh'],
  yaml: ['yml'],
  xml: ['html', 'svg'],
  markdown: ['md'],
  dockerfile: ['docker'],
  plaintext: ['text', 'txt'],
});

/** hast 节点的最小结构。不引 @types/hast：这里只用到 4 个字段，
 *  为此多一个直接依赖不划算（类型定义在打包时本就会被擦除）。 */
interface HastNode {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
}

function textOf(node: HastNode): string {
  if (node.type === 'text') return node.value ?? '';
  return (node.children ?? []).map(textOf).join('');
}

/** 从 className 取语言名：`language-python` / `lang-python` / 直接写 `python` 都认。
 *  先查已注册再返回，避免把 `language-!!!` 之类丢给低层抛异常。 */
function languageOf(node: HastNode): string | null {
  const raw = node.properties?.className;
  const list = Array.isArray(raw) ? raw : typeof raw === 'string' ? raw.split(/\s+/) : [];
  for (const item of list) {
    const name = String(item).replace(/^(language|lang)-/, '').toLowerCase();
    if (name && lowlight.registered(name)) return name;
  }
  return null;
}

/** rehype 插件：把 `<code class="language-x">` 的内容换成高亮后的 hast。 */
export function rehypeHighlightSubset() {
  return (tree: HastNode): void => {
    const walk = (node: HastNode): void => {
      if (node.type === 'element' && node.tagName === 'code') {
        const lang = languageOf(node);
        if (lang) {
          try {
            const result = lowlight.highlight(lang, textOf(node));
            node.children = result.children as HastNode[];
            node.properties = { ...(node.properties ?? {}), className: ['hljs', `language-${lang}`] };
          } catch {
            // 高亮失败不能影响答案本身 —— 保留原文即可（代码块第一职责是内容正确）
          }
        }
      }
      (node.children ?? []).forEach(walk);
    };
    walk(tree);
  };
}
