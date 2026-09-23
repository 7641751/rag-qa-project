import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { focusRing } from './cls';

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** 强制必填：纯图标按钮对屏幕阅读器是「无内容按钮」，不给 label 就只剩一个「按钮」。 */
  'aria-label': string;
  children: ReactNode;
  /** 点击区域尺寸（视觉图标本身通常 12–14px，点击区不应小于 22px） */
  size?: number;
  /** danger = 删除类操作：红底红字 hover。⚠ 不要靠在外层传 `hover:bg-red-*` 覆盖基类 —
   *  两条同特异性的 Tailwind 类谁赢取决于生成 CSS 里的顺序，与类书写顺序无关，必然踩坑。 */
  tone?: 'default' | 'danger';
}

const TONES = {
  default: 'text-gray-500 hover:bg-gray-200 hover:text-gray-700',
  danger: 'text-gray-500 hover:bg-red-100 hover:text-red-600',
} as const;

/** 纯图标按钮基元：正方形点击区 + 强制 aria-label + 统一 hover/焦点态。 */
export function IconButton({
  size = 22,
  tone = 'default',
  children,
  className = '',
  type = 'button',
  ...rest
}: IconButtonProps) {
  return (
    <button
      type={type}
      className={`inline-flex shrink-0 items-center justify-center rounded-md ` +
        `transition-colors ${TONES[tone]} ${focusRing} ${className}`}
      style={{ width: size, height: size }}
      {...rest}
    >
      {children}
    </button>
  );
}
