import { useState } from 'react'

interface ToolTraceProps {
  tools: string[]
  events?: Array<{ tool?: string | null; summary?: string }>
  defaultOpen?: boolean
}

const READISH = /^(read|cat|glob|list|ls|search|grep|find|get_)/i
const WRITEISH = /^(write|edit|create|patch|apply|save|mkdir)/i
const RUNISH = /^(bash|shell|run|exec|npm|pip|pytest|test)/i

function classify(name: string): 'read' | 'write' | 'run' | 'other' {
  if (READISH.test(name)) return 'read'
  if (WRITEISH.test(name)) return 'write'
  if (RUNISH.test(name)) return 'run'
  return 'other'
}

/** Human one-liner like OpenHands tool strips — not raw JSON. */
export function summarizeTools(tools: string[]): string {
  const unique = [...new Set(tools.filter(Boolean))]
  if (unique.length === 0) return ''
  const counts = { read: 0, write: 0, run: 0, other: 0 }
  for (const t of unique) counts[classify(t)] += 1
  const parts: string[] = []
  if (counts.read) parts.push(`读了 ${counts.read} 项`)
  if (counts.write) parts.push(`写了 ${counts.write} 项`)
  if (counts.run) parts.push(`执行了 ${counts.run} 项`)
  if (counts.other) parts.push(`调用了 ${counts.other} 个工具`)
  if (parts.length === 0) return `调用了 ${unique.length} 个工具`
  if (unique.length === 1) return `调用了 ${unique[0]}`
  return parts.join(' · ')
}

export function ToolTrace({ tools, events, defaultOpen = false }: ToolTraceProps) {
  const [open, setOpen] = useState(defaultOpen)
  const names = tools.length
    ? tools
    : (events || [])
        .map(e => e.tool)
        .filter((t): t is string => !!t)
  if (names.length === 0 && !(events && events.length)) return null

  const unique = [...new Set(names)]
  const label = summarizeTools(unique)

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
                  {ev.summary && !/^\s*\{/.test(ev.summary) ? <span>{ev.summary}</span> : null}
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
