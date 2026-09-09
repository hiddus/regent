import { useEffect, type ReactNode } from 'react'

/** H5 线性图标（Feather 风格，24x24 描边）。 */
export type IconName =
  | 'shelf' | 'pen' | 'list' | 'user' | 'back' | 'plus' | 'more' | 'close'
  | 'send' | 'coin' | 'share' | 'download' | 'flag' | 'sliders' | 'logout'
  | 'refresh' | 'layers' | 'check' | 'arrowUp' | 'chevronRight' | 'type'

const PATHS: Record<IconName, ReactNode> = {
  shelf: <><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" /><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" /></>,
  pen: <><path d="M12 20h9" /><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z" /></>,
  list: <><path d="M8 6h13M8 12h13M8 18h13" /><path d="M3 6h.01M3 12h.01M3 18h.01" /></>,
  user: <><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" /><circle cx="12" cy="7" r="4" /></>,
  back: <polyline points="15 18 9 12 15 6" />,
  plus: <path d="M12 5v14M5 12h14" />,
  more: <><circle cx="5" cy="12" r="1.4" /><circle cx="12" cy="12" r="1.4" /><circle cx="19" cy="12" r="1.4" /></>,
  close: <path d="M18 6 6 18M6 6l12 12" />,
  send: <><path d="m22 2-11 11" /><path d="M22 2 15 22l-4-9-9-4z" /></>,
  coin: <><path d="M12 1v22" /><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" /></>,
  share: <><circle cx="18" cy="5" r="3" /><circle cx="6" cy="12" r="3" /><circle cx="18" cy="19" r="3" /><path d="m8.6 13.5 6.8 4M15.4 6.5l-6.8 4" /></>,
  download: <><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="7 10 12 15 17 10" /><path d="M12 15V3" /></>,
  flag: <><path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z" /><path d="M4 22v-7" /></>,
  sliders: <><path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3" /><path d="M1 14h6M9 8h6M17 16h6" /></>,
  logout: <><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><polyline points="16 17 21 12 16 7" /><path d="M21 12H9" /></>,
  refresh: <><polyline points="23 4 23 10 17 10" /><polyline points="1 20 1 14 7 14" /><path d="M3.5 9a9 9 0 0 1 14.9-3.4L23 10M1 14l4.6 4.4A9 9 0 0 0 20.5 15" /></>,
  layers: <><polygon points="12 2 2 7 12 12 22 7 12 2" /><polyline points="2 17 12 22 22 17" /><polyline points="2 12 12 17 22 12" /></>,
  check: <polyline points="20 6 9 17 4 12" />,
  arrowUp: <><path d="M12 19V5" /><polyline points="5 12 12 5 19 12" /></>,
  chevronRight: <polyline points="9 18 15 12 9 6" />,
  type: <><polyline points="4 7 4 4 20 4 20 7" /><path d="M9 20h6M12 4v16" /></>,
}

export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  return (
    <svg className="icon" width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {PATHS[name]}
    </svg>
  )
}

/** 底部弹层（H5 bottom sheet）：遮罩 + 上滑面板 + 抓手。 */
export function Sheet({ title, onClose, children, wide }: {
  title: string; onClose: () => void; children: ReactNode; wide?: boolean
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="sheet-layer">
      <div className="scrim" onClick={onClose} />
      <div className={`sheet${wide ? ' wide' : ''}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="sheet-grip" />
        <div className="sheet-head">
          <h2>{title}</h2>
          <button className="iconbtn" onClick={onClose} aria-label="关闭"><Icon name="close" size={18} /></button>
        </div>
        <div className="sheet-body">{children}</div>
      </div>
    </div>
  )
}

/** 分段选择器（弹层内的二级 Tab）。 */
export function Segmented<T extends string>({ value, options, onChange }: {
  value: T; options: { key: T; label: string }[]; onChange: (v: T) => void
}) {
  return (
    <div className="segmented" role="tablist">
      {options.map(o => (
        <button key={o.key} role="tab" aria-selected={value === o.key}
          className={value === o.key ? 'active' : ''} onClick={() => onChange(o.key)}>{o.label}</button>
      ))}
    </div>
  )
}
