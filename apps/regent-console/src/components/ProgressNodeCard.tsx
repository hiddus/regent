import { useEffect, useState } from 'react'
import type { ProgressNode } from '../lib/progressNodes'
import { NODE_STATUS_LABEL } from '../lib/progressNodes'
import { ToolTrace, summarizeTools } from './ToolTrace'

interface ProgressNodeCardProps {
  node: ProgressNode
  /** When true, running/waiting nodes expand detail by default. */
  liveMode?: boolean
  /** Older settled nodes start compressed to keep the dialog scannable. */
  preferCompressed?: boolean
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

type ViewMode = 'detail' | 'overview' | 'compressed'

export function ProgressNodeCard({
  node,
  liveMode = false,
  preferCompressed = false,
}: ProgressNodeCardProps) {
  const isLive = node.status === 'running' || node.status === 'waiting'
  const isSettled = node.status === 'done' || node.status === 'failed'
  const highlights = Object.entries(node.highlights || {})
  const hasExtra = !!(node.detail || highlights.length > 0 || (node.toolTrace && node.toolTrace.length > 0))

  const defaultMode = (): ViewMode => {
    if (liveMode && isLive) return 'detail'
    if (preferCompressed && isSettled) return 'compressed'
    return 'overview'
  }

  const [mode, setMode] = useState<ViewMode>(defaultMode)

  useEffect(() => {
    setMode(defaultMode())
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveMode, preferCompressed, node.status, node.updatedAt, node.key])

  const cycleMode = () => {
    if (isLive) {
      setMode(m => (m === 'detail' ? 'overview' : 'detail'))
      return
    }
    if (mode === 'overview') setMode('compressed')
    else if (mode === 'compressed') setMode(hasExtra ? 'detail' : 'overview')
    else setMode('overview')
  }

  const showBadge = isLive || mode === 'detail'
  const showConclusion = mode !== 'compressed'
  const primaryText = isLive && mode === 'detail' && node.detail
    ? node.detail
    : node.conclusion
  const toolHint =
    isLive && node.toolTrace && node.toolTrace.length > 0
      ? summarizeTools(node.toolTrace)
      : ''

  return (
    <article
      className={[
        'progress-node',
        `status-${node.status}`,
        `view-${mode}`,
        isLive ? 'is-live' : '',
        isSettled ? 'is-settled' : '',
      ].filter(Boolean).join(' ')}
      data-node={node.key}
    >
      <div className="progress-node-rail">
        <div className={`progress-node-dot status-${node.status}`} aria-hidden />
      </div>
      <div className="progress-node-body">
        <header
          className="progress-node-head"
          onClick={cycleMode}
          role="button"
          tabIndex={0}
          onKeyDown={e => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault()
              cycleMode()
            }
          }}
          title={isSettled ? '点击切换详略' : '点击展开/收起'}
        >
          <h3 className="progress-node-title">{node.title}</h3>
          {showBadge && (
            <span className={`progress-node-badge status-${node.status}`}>
              {NODE_STATUS_LABEL[node.status]}
            </span>
          )}
          {toolHint && mode !== 'detail' && (
            <span className="progress-node-tool-hint" title="工具轨迹">
              {toolHint}
            </span>
          )}
          <span
            className={`progress-node-arrow ${mode === 'detail' ? 'expanded' : ''}`}
            aria-hidden
          >
            ›
          </span>
        </header>

        {showConclusion && (
          <p className={`progress-node-conclusion ${isLive && mode === 'detail' ? 'live-detail' : ''}`}>
            {primaryText}
          </p>
        )}

        {mode === 'compressed' && (
          <p className="progress-node-compressed-hint">已折叠 · 点击展开</p>
        )}

        {/* Live overview still shows collapsed tool strip (OpenHands). */}
        {mode === 'overview' && isLive && node.toolTrace && node.toolTrace.length > 0 && (
          <ToolTrace tools={node.toolTrace} defaultOpen={false} />
        )}

        <div className={`progress-node-detail-wrap ${mode === 'detail' ? 'open' : ''}`}>
          {mode === 'detail' && isLive && node.conclusion && node.detail
            && node.detail !== node.conclusion && (
            <p className="progress-node-detail muted">{node.conclusion}</p>
          )}
          {mode === 'detail' && !isLive && node.detail && node.detail !== node.conclusion && (
            <p className="progress-node-detail">{node.detail}</p>
          )}
          {mode === 'detail' && highlights.length > 0 && (
            <ul className="progress-node-highlights">
              {highlights.map(([k, v]) => (
                <li key={k}>
                  <span className="highlight-k">{k}</span>
                  <span className="highlight-v">{v}</span>
                </li>
              ))}
            </ul>
          )}
          {mode === 'detail' && node.toolTrace && node.toolTrace.length > 0 && (
            <ToolTrace tools={node.toolTrace} defaultOpen={isLive} />
          )}
          {mode === 'detail' && node.updatedAt && (
            <span className="progress-node-time">{formatTime(node.updatedAt)}</span>
          )}
        </div>
      </div>
    </article>
  )
}
