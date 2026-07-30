import { useEffect, useMemo, useState } from 'react'
import type { Message, Project, ProjectStatus } from '../lib/types'
import { buildProgressNodes, NODE_STATUS_LABEL, type ProgressNode } from '../lib/progressNodes'
import { formatRelativeTime, latestMessageTimestamp } from '../lib/liveActivity'
import { api } from '../lib/api'

interface ArtifactPanelProps {
  project: Project | null
  status: ProjectStatus | null
  messages: Message[]
  isOpen: boolean
  onToggle: () => void
}

const PREVIEW_READY_STATUSES = new Set([
  'PREVIEW_READY',
  'PREVIEW_DEPLOYMENT_SUCCEEDED',
  'PREVIEW_SUCCEEDED',
  'SUCCEEDED',
])

function activeConclusion(nodes: ProgressNode[]): string {
  const running = [...nodes].reverse().find(n => n.status === 'running' || n.status === 'waiting')
  if (running) return `${running.title} — ${running.conclusion}`
  const last = nodes[nodes.length - 1]
  return last ? `${last.title} — ${last.conclusion}` : '暂无进展'
}

function normalizePreviewUrl(ep: string | null | undefined): string | null {
  if (!ep) return null
  try {
    const u = new URL(ep, location.origin)
    if (u.hostname === 'regent-api' || u.hostname === 'localhost' || u.hostname === '127.0.0.1') {
      return location.origin + u.pathname + u.search + u.hash
    }
    // Same host as API but console may be on :3000 — keep absolute API preview URL.
    return u.toString()
  } catch {
    return String(ep).replace('http://regent-api:8000', location.origin)
  }
}

function previewUrlFromMessages(messages: Message[]): string | null {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const m = messages[i]
    const metaEp = m.metadata?.endpoint || m.metadata?.preview_endpoint || m.metadata?.url
    if (typeof metaEp === 'string' && metaEp.includes('/preview/')) {
      return normalizePreviewUrl(metaEp)
    }
    const match = m.content?.match(/https?:\/\/\S*\/preview\/[^\s)\]\"']+/i)
      || m.content?.match(/\/preview\/[0-9a-f-]+\/[0-9a-f-]+\/?/i)
    if (match) return normalizePreviewUrl(match[0])
  }
  return null
}

function resolveDeliverable(status: ProjectStatus | null, messages: Message[]): {
  url: string | null
  ready: boolean
} {
  const preview = status?.preview
  const fromPreview = normalizePreviewUrl(preview?.endpoint)
  const fromMeta = normalizePreviewUrl(
    typeof status?.goal?.metadata?.last_preview_endpoint === 'string'
      ? status.goal.metadata.last_preview_endpoint
      : null,
  )
  const fromMessages = previewUrlFromMessages(messages)
  const url = fromPreview || fromMeta || fromMessages
  const statusReady = !!preview?.status && PREVIEW_READY_STATUSES.has(preview.status)
  const goalDone = status?.goal?.status === 'ACHIEVED'
  const stage = String(
    status?.goal?.metadata?.execution_stage
      || status?.goal?.execution_stage?.stage
      || '',
  )
  const stageReady = stage.includes('PREVIEW') || stage === 'ACHIEVED' || stage === 'GATE_PASSED'
  return {
    url,
    ready: !!url && (statusReady || goalDone || stageReady || !!fromMeta || !!fromMessages),
  }
}

export function ArtifactPanel({
  project,
  status,
  messages,
  isOpen,
  onToggle,
}: ArtifactPanelProps) {
  const [previewExpanded, setPreviewExpanded] = useState(false)
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])
  const nodes = buildProgressNodes(messages)
  const lastProgressAt = latestMessageTimestamp(messages)
  const deliverable = useMemo(
    () => resolveDeliverable(status, messages),
    [status, messages],
  )
  const previewUrl = deliverable.url
  const hasPreview = deliverable.ready

  return (
    <>
      {!isOpen && (
        <button className="artifact-panel-toggle" onClick={onToggle} title="打开产物面板">
          <span className="toggle-icon">◁</span>
          <span className="toggle-label">产物</span>
        </button>
      )}

      <aside className={`artifact-panel ${isOpen ? 'open' : ''}`}>
        <div className="artifact-panel-header">
          <h3>产物面板</h3>
          <button className="close-btn" onClick={onToggle} aria-label="关闭面板">×</button>
        </div>

        <div className="artifact-panel-content">
          {hasPreview && previewUrl && (
            <div className="artifact-preview-section">
              <div className="artifact-section-title">
                <span>应用预览</span>
                <span className="artifact-preview-badge">就绪</span>
              </div>
              <div className={`artifact-preview-frame ${previewExpanded ? 'expanded' : ''}`}>
                <iframe
                  src={previewUrl}
                  title="App Preview"
                  className="artifact-preview-iframe"
                  sandbox="allow-scripts allow-same-origin"
                />
              </div>
              <div className="artifact-preview-actions">
                <button
                  className="artifact-btn"
                  onClick={() => setPreviewExpanded(!previewExpanded)}
                >
                  {previewExpanded ? '收起预览' : '展开预览'}
                </button>
                <a
                  className="artifact-btn"
                  href={previewUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  新窗口打开
                </a>
              </div>
            </div>
          )}

          {project && hasPreview && (
            <div className="artifact-download-section">
              <button
                className="artifact-btn primary"
                onClick={() => api.downloadArtifact(project.id)}
              >
                下载应用产出物
              </button>
            </div>
          )}

          {!hasPreview && status?.goal?.status === 'ACHIEVED' && (
            <div className="artifact-empty">
              <p>目标已达成，但尚未解析到预览地址。可在对话「预览准备」步骤中打开链接。</p>
            </div>
          )}

          {status?.goal && (
            <div className="artifact-status-section">
              <div className="artifact-section-title">执行摘要</div>
              <div className="artifact-summary">
                <div className="artifact-summary-conclusion">{activeConclusion(nodes)}</div>
                <div className="artifact-summary-freshness">
                  上次进展：{formatRelativeTime(lastProgressAt, now)}
                  {status.goal.status === 'ACTIVE' && lastProgressAt != null && now - lastProgressAt > 3 * 60 * 1000
                    ? ' · 长时间无新消息，可能较慢或停滞'
                    : status.goal.status === 'ACTIVE'
                      ? ' · 系统仍在处理中'
                      : ''}
                </div>
              </div>
            </div>
          )}

          {nodes.length > 0 && (
            <div className="artifact-rail-section">
              <div className="artifact-section-title">执行步骤</div>
              <ol className="artifact-rail">
                {nodes.map(node => (
                  <li key={node.key} className={`artifact-rail-item status-${node.status}`}>
                    <div className={`artifact-rail-dot status-${node.status}`} />
                    <div className="artifact-rail-body">
                      <div className="artifact-rail-head">
                        <span className="artifact-rail-title">{node.title}</span>
                        <span className={`progress-node-badge status-${node.status}`}>
                          {NODE_STATUS_LABEL[node.status]}
                        </span>
                      </div>
                      <p className="artifact-rail-conclusion">{node.conclusion}</p>
                    </div>
                  </li>
                ))}
              </ol>
            </div>
          )}

          {!hasPreview && nodes.length === 0 && status?.goal?.status !== 'ACHIEVED' && (
            <div className="artifact-empty">
              <p>开始创建 App 后，这里会显示预览和产出物。</p>
            </div>
          )}
        </div>
      </aside>
    </>
  )
}
