/** 共享设计 token：按钮 / 输入框 / 错误框 / 焦点环。
 *
 *  为什么集中在这里而不是散落在各组件：同一类元素曾分别在 6 个组件里各写一套类名
 *  （「删除对话」的红、`ConfirmDialog` 的红、`Composer` 停止键的红是三个不同写法），
 *  改一次风格要改 6 处、必然漂移。集中后，风格调整只动这里，调用处只管语义。
 *
 *  命名刻意按「语义」而不是按「颜色」：dark / primary / ghost / danger。
 */

/** 所有可聚焦控件共用的焦点环。
 *  键盘用户看得见焦点在哪 —— 浏览器默认 outline 在灰底上对比度不足且形状不规则。 */
export const focusRing =
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-1 focus-visible:ring-blue-500/70';

export const btnBase =
  `inline-flex items-center justify-center gap-1.5 rounded-md border font-medium ` +
  `transition-colors select-none ${focusRing} disabled:cursor-not-allowed`;

export const btnSizes = {
  sm: 'px-3 py-1 text-xs',
  md: 'px-3.5 py-2 text-sm',
} as const;

export const btnVariants = {
  /** 主要动作（发送 / 提交） */
  primary:
    'border-blue-600 bg-blue-600 text-white hover:bg-blue-700 ' +
    'disabled:border-gray-300 disabled:bg-gray-300',
  /** 次要动作（新对话 / 刷新 / 退出） */
  ghost:
    'border-gray-300 bg-white text-gray-600 hover:bg-gray-100 ' +
    'disabled:text-gray-300 disabled:hover:bg-transparent',
  /** 不可逆的危险动作（删除确认） */
  danger:
    'border-red-600 bg-red-600 text-white hover:bg-red-700 ' +
    'disabled:border-gray-300 disabled:bg-gray-300',
  /** 危险但非主要按钮（删除对话 / 停止）：红字红框，不抢主按钮的视觉权重 */
  dangerGhost:
    'border-red-200 bg-white text-red-600 hover:bg-red-50 ' +
    'disabled:border-gray-200 disabled:text-gray-300 disabled:hover:bg-transparent',
  /** 登录/注册提交：比 primary 更沉一档，和登录页的克制感一致 */
  dark:
    'border-gray-900 bg-gray-900 text-white hover:bg-gray-800 ' +
    'disabled:border-gray-300 disabled:bg-gray-300',
} as const;

export const inputCls =
  `rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-800 ` +
  `placeholder:text-gray-400 hover:border-gray-400 focus:border-gray-500 ${focusRing} ` +
  `aria-[invalid=true]:border-red-400 aria-[invalid=true]:ring-red-400/40`;

/** 错误提示框。auth-error / confirm-error / sidebar-error / conv-error 原先是同一套
 *  写法手抄了 4 遍 —— 那份「视觉一致性」本来是靠运气维持的。 */
export const errorBoxCls =
  'rounded-md border border-red-200 bg-red-50 px-2 py-1.5 text-xs leading-relaxed text-red-700';
