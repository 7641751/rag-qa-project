import { forwardRef } from 'react';
import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { btnBase, btnSizes, btnVariants } from './cls';
import { IconSpinner } from './icons';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: keyof typeof btnVariants;
  size?: keyof typeof btnSizes;
  /** 请求在飞：禁用按钮并给旋转指示，防双击重复提交 */
  loading?: boolean;
  children: ReactNode;
}

/** 全站唯一按钮实现。风格按语义分档（见 ui/cls.ts 的 btnVariants），
 *  禁用 / loading / 焦点环 / 防双击在这里统一处理，调用处不再各写一套。
 *  forwardRef 是硬需求：ConfirmDialog 要把初始焦点程序化落到「取消」上。 */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'ghost',
    size = 'md',
    loading = false,
    disabled,
    children,
    className = '',
    type = 'button',
    ...rest
  },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || loading}
      className={`${btnBase} ${btnSizes[size]} ${btnVariants[variant]} ${className}`}
      {...rest}
    >
      {loading && <IconSpinner size={12} className="animate-spin" />}
      {children}
    </button>
  );
});
