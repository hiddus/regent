import { useEffect, useMemo, useState } from 'react'
import type { LiveAction } from '../lib/liveActivity'
import type { Message, Project, ProjectStatus } from '../lib/types'
import {
  ACTIVITY_LABEL,
  agentInitials,
  countActiveAgents,
  deriveAgents,
} from '../lib/agents'
import { api } from '../lib/api'

interface ArtifactPanelProps {
  project: Project | null
  status: ProjectStatus | null
  messages: Message[]
  liveAction?: LiveAction | null
  isOpen: boolean
  onToggle: () => void
}

const PREVIEW_READY_STATUSES = new Set([
  'PREVIEW_READY',
  'PREVIEW_DEPLOYMENT_SUCCEEDED',
  'PREVIEW_SUCCEEDED',
  'SUCCEEDED',
])

function normalizePreviewUrl(ep: string | null | undefined): string | null {
  if (!ep) return null
  try {
    const u = new URL(ep, location.origin)
    if (u.hostname === 'regent-api' || u.hostname === 'localhost' || u.hostname === '127.0.0.1') {
      return location.origin + u.pathname + u.search + u.hash
    }
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
  liveAction = null,
  isOpen,
  onToggle,
}: ArtifactPanelProps) {
  const [previewExpanded, setPreviewExpanded] = useState(false)
  const [artifactsOpen, setArtifactsOpen] = useState(false)
  const agents = useMemo(
    () => deriveAgents(status, liveAction, messages),
    [status, liveAction, messages],
  )
  const activeCount = countActiveAgents(agents)
  const deliverable = useMemo(
    () => resolveDeliverable(status, messages),
    [status, messages],
  )
  const previewUrl = deliverable.url
  const hasPreview = deliverable.ready

  useEffect(() => {
    if (hasPreview) setArtifactsOpen(true)
  }, [hasPreview])

  return (
    <>
      {!isOpen && (
        <button className="artifact-panel-toggle" onClick={onToggle} title="打开 Agent 面板">
          <span className="toggle-icon">◁</span>
          <span className="toggle-label">Agent</span>
        </button>
      )}

      <aside className={`artifact-panel ${isOpen ? 'open' : ''}`}>
        <div className="artifact-panel-header">
          <h3>参与 Agent</h3>
          {agents.length > 0 && (
            <span className="agent-panel-count">
              {activeCount > 0 ? `${activeCount} 个活动` : `${agents.length} 个`}
            </span>
          )}
          <button className="close-btn" onClick={onToggle} aria-label="关闭面板">×</button>
        </div>

        <div className="artifact-panel-content">
          <div className="agent-roster-section">
            {agents.length === 0 ? (
              <div className="artifact-empty">
                <p>开始创建 App 后，这里会显示参与的 Agent。</p>
              </div>
            ) : (
              <ul className="agent-roster">
                {agents.map(agent => (
                  <li
                    key={agent.id}
                    className={`agent-roster-item activity-${agent.activity} ${agent.is_main ? 'is-main' : ''}`}
                  >
                    <div className={`agent-avatar activity-${agent.activity}`} aria-hidden>
                      <span>{agentInitials(agent)}</span>
                      <span className={`agent-status-badge activity-${agent.activity}`} />
                    </div>
                    <div className="agent-roster-body">
                      <div className="agent-roster-head">
                        <span className="agent-roster-name">{agent.name}</span>
                        <span className={`agent-activity-chip activity-${agent.activity}`}>
                          {ACTIVITY_LABEL[agent.activity]}
                        </span>
                      </div>
                      <div className="agent-roster-meta">
                        {agent.is_main ? 'Core' : agent.role_label}
                        {agent.kind === 'hive' ? ' · Hive' : ''}
                      </div>
                      {agent.detail && (
                        <p className="agent-roster-detail">{agent.detail}</p>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {(hasPreview || (project && status?.goal)) && (
            <div className="artifact-fold-section">
              <button
                type="button"
                className={`artifact-fold-toggle ${artifactsOpen ? 'open' : ''}`}
                onClick={() => setArtifactsOpen(!artifactsOpen)}
              >
                <span>产物与预览</span>
                <span className="artifact-fold-arrow" aria-hidden>›</span>
              </button>
              {artifactsOpen && (
                <div className="artifact-fold-body">
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
                    <div className="artifact-empty compact">
                      <p>目标已达成，但尚未解析到预览地址。</p>
                    </div>
                  )}

                  {!hasPreview && status?.goal?.status !== 'ACHIEVED' && (
                    <div className="artifact-empty compact">
                      <p>预览就绪后会显示在这里。</p>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </aside>
    </>
  )
}
