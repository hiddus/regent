import { useState } from 'react'
import type { ProgressNode } from '../lib/progressNodes'
import { NODE_STATUS_LABEL } from '../lib/progressNodes'

interface ProgressNodeCardProps {
  node: ProgressNode
}

function formatTime(iso?: string): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  } catch {
    return ''
  }
}

export function ProgressNodeCard({ node }: ProgressNodeCardProps) {
  const [expanded, setExpanded] = useState(false)
  const highlights = Object.entries(node.highlights || {})
  const hasDetail = !!(node.detail || highlights.length > 0 || node.updatedAt)

  return (
    <article
      className={`progress-node status-${node.status} ${expanded ? 'expanded' : ''}`}
      data-node={node.key}
    >
      <div className="progress-node-rail">
        <div className={`progress-node-dot status-${node.status}`} aria-hidden />
      </div>
      <div className="progress-node-body">
        <header
          className="progress-node-head"
          onClick={() => hasDetail && setExpanded(!expanded)}
          role={hasDetail ? 'button' : undefined}
          tabIndex={hasDetail ? 0 : undefined}
          onKeyDown={e => { if (hasDetail && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); setExpanded(!expanded) } }}
        >
          <h3 className="progress-node-title">{node.title}</h3>
          <span className={`progress-node-badge status-${node.status}`}>
            {NODE_STATUS_LABEL[node.status]}
          </span>
          {hasDetail && (
            <span className={`progress-node-arrow ${expanded ? 'expanded' : ''}`} aria-hidden>
              ›
            </span>
          )}
        </header>
        <p className="progress-node-conclusion">{node.conclusion}</p>

        {hasDetail && (
          <div className={`progress-node-detail-wrap ${expanded ? 'open' : ''}`}>
            {node.detail && node.detail !== node.conclusion && (
              <p className="progress-node-detail">{node.detail}</p>
            )}
            {highlights.length > 0 && (
              <ul className="progress-node-highlights">
                {highlights.map(([k, v]) => (
                  <li key={k}>
                    <span className="highlight-k">{k}</span>
                    <span className="highlight-v">{v}</span>
                  </li>
                ))}
              </ul>
            )}
            {node.updatedAt && (
              <span className="progress-node-time">{formatTime(node.updatedAt)}</span>
            )}
          </div>
        )}
      </div>
    </article>
  )
}
