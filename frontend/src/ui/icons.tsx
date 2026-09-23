/** 内联 SVG 图标：替换散落各处的 emoji（✎ 🗑 « » 🔍 ⚠ ✕ 等）。
 *
 *  为什么不引图标库：全库引入会让 bundle 多几十 KB，而这里只需要约 10 个线性图标；
 *  emoji 的渲染因平台字体而异（Windows 与 macOS 长得不一样），且对屏幕阅读器是噪音。
 *  内联 SVG 与文字同色系（currentColor）、可精确控制尺寸。
 *
 *  约定：24×24 viewBox、stroke=currentColor、默认 14px、一律 aria-hidden
 *  （语义由外层按钮的 aria-label 提供，图标本身不该被朗读）。
 *
 *  例外：`📚 知识库` 仍保留 emoji —— 测试用 getByText('📚 知识库') 精确匹配它，
 *  换图标会红。这是有意的「测试契约优先」。
 */

type IconProps = { size?: number; className?: string };

function attrs({ size = 14, className = '' }: IconProps) {
  return {
    width: size,
    height: size,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.8,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    className,
    'aria-hidden': true,
  };
}

export function IconPencil(props: IconProps) {
  return (
    <svg {...attrs(props)}>
      <path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
      <path d="m15 5 4 4" />
    </svg>
  );
}

export function IconTrash(props: IconProps) {
  return (
    <svg {...attrs(props)}>
      <path d="M3 6h18" />
      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
      <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
      <path d="M10 11v6M14 11v6" />
    </svg>
  );
}

export function IconChevronLeft(props: IconProps) {
  return (
    <svg {...attrs(props)}><path d="m15 18-6-6 6-6" /></svg>
  );
}

export function IconChevronRight(props: IconProps) {
  return (
    <svg {...attrs(props)}><path d="m9 18 6-6-6-6" /></svg>
  );
}

export function IconSearch(props: IconProps) {
  return (
    <svg {...attrs(props)}>
      <circle cx="11" cy="11" r="7" />
      <path d="m21 21-4.3-4.3" />
    </svg>
  );
}

export function IconAlert(props: IconProps) {
  return (
    <svg {...attrs(props)}>
      <path d="m12 3 10 18H2Z" />
      <path d="M12 10v4" />
      <path d="M12 18h.01" />
    </svg>
  );
}

export function IconX(props: IconProps) {
  return (
    <svg {...attrs(props)}>
      <path d="M18 6 6 18" />
      <path d="m6 6 12 12" />
    </svg>
  );
}

export function IconRobot(props: IconProps) {
  return (
    <svg {...attrs(props)}>
      <rect x="5" y="8" width="14" height="11" rx="2" />
      <path d="M12 8V4" />
      <path d="M9 12h.01M15 12h.01" />
      <path d="M8 19v2M16 19v2" />
    </svg>
  );
}

export function IconSend(props: IconProps) {
  return (
    <svg {...attrs(props)}>
      <path d="m22 2-7 20-4-9-9-4Z" />
      <path d="M22 2 11 13" />
    </svg>
  );
}

export function IconStop(props: IconProps) {
  return (
    <svg {...attrs(props)}>
      <rect x="6" y="6" width="12" height="12" rx="1.5" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function IconLogout(props: IconProps) {
  return (
    <svg {...attrs(props)}>
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <path d="m16 17 5-5-5-5" />
      <path d="M21 12H9" />
    </svg>
  );
}

export function IconPlus(props: IconProps) {
  return (
    <svg {...attrs(props)}>
      <path d="M12 5v14" />
      <path d="M5 12h14" />
    </svg>
  );
}

export function IconRefresh(props: IconProps) {
  return (
    <svg {...attrs(props)}>
      <path d="M21 12a9 9 0 1 1-2.64-6.36" />
      <path d="M21 3v6h-6" />
    </svg>
  );
}

export function IconUpload(props: IconProps) {
  return (
    <svg {...attrs(props)}>
      <path d="M12 16V4" />
      <path d="m6 10 6-6 6 6" />
      <path d="M4 20h16" />
    </svg>
  );
}

/** 加载态旋转指示：配 animate-spin 用。 */
export function IconSpinner(props: IconProps) {
  return (
    <svg {...attrs(props)}>
      <path d="M12 2a10 10 0 1 0 10 10" />
    </svg>
  );
}
