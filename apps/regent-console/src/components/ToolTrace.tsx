import { useState } from 'react'

interface ToolTraceProps {
  tools: string[]
  /** Optional richer rows: tool + short summary */
  events?: Array<{ tool?: string | null; summary?: string }>
  defaultOpen?: boolean
}

/** Collapsed tool trajectory: one-line summary, expand for list. */
export function ToolTrace({ tools, events, defaultOpen = false }: ToolTraceProps) {
  const [open, setOpen] = useState(defaultOpen)
  const names = tools.length
    ? tools
    : (events || [])
        .map(e => e.tool)
        .filter((t): t is string => !!t)
  if (names.length === 0 && !(events && events.length)) return null

  const unique = [...new Set(names)]
  const label =
    unique.length === 1
      ? `调用了 ${unique[0]}`
      : unique.length <= 3
        ? `调用了 ${unique.join('、')}`
        : `调用了 ${unique.length} 个工具`

  return (
    <div className={`tool-trace ${open ? 'open' : ''}`}>
      <button type="button" className="tool-trace-toggle" onClick={() => setOpen(v => !v)}>
        <span className="tool-trace-label">{label}</span>
        <span className="tool-trace-arrow" aria-hidden>
          ›
        </span>
      </button>
      {open && (
        <ul className="tool-trace-list">
          {events && events.length > 0
            ? events.map((ev, i) => (
                <li key={i}>
                  <code>{ev.tool || 'tool'}</code>
                  {ev.summary ? <span>{ev.summary}</span> : null}
                </li>
              ))
            : unique.map((t, i) => (
                <li key={`${t}-${i}`}>
                  <code>{t}</code>
                </li>
              ))}
        </ul>
      )}
    </div>
  )
}
