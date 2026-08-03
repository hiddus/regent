import { useState, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

interface MarkdownBodyProps {
  children: string
  className?: string
  /** Collapse long bodies by default (OpenHands-style process noise control). */
  collapsible?: boolean
  collapseAt?: number
  collapsedLabel?: string
}

/** Shared markdown renderer with denser typography and optional collapse. */
export function MarkdownBody({
  children,
  className = '',
  collapsible = false,
  collapseAt = 320,
  collapsedLabel = '展开完整说明',
}: MarkdownBodyProps) {
  const text = children || ''
  const shouldCollapse = collapsible && text.trim().length > collapseAt
  const [open, setOpen] = useState(!shouldCollapse)

  const preview = shouldCollapse && !open
    ? `${text.trim().slice(0, collapseAt).replace(/\s+\S*$/, '')}…`
    : text

  return (
    <div className={`md-body ${className}`.trim()}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children: c }) => (
            <a href={href} target="_blank" rel="noopener noreferrer">{c}</a>
          ),
        }}
      >
        {preview}
      </ReactMarkdown>
      {shouldCollapse && (
        <button type="button" className="md-collapse-toggle" onClick={() => setOpen(v => !v)}>
          {open ? '收起' : collapsedLabel}
        </button>
      )}
    </div>
  )
}

interface LeadLineProps {
  children: ReactNode
}

/** One-line system lead above a structured card (avoid duplicating card body). */
export function LeadLine({ children }: LeadLineProps) {
  return <p className="message-lead">{children}</p>
}
