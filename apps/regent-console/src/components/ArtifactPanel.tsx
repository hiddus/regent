import { useState } from 'react'
import type { Message, Project, ProjectStatus } from '../lib/types'
import { buildProgressNodes, NODE_STATUS_LABEL, type ProgressNode } from '../lib/progressNodes'
import { api } from '../lib/api'

interface ArtifactPanelProps {
  project: Project | null
  status: ProjectStatus | null
  messages: Message[]
  isOpen: boolean
  onToggle: () => void
}

function activeConclusion(nodes: ProgressNode[]): string {
  const running = [...nodes].reverse().find(n => n.status === 'running' || n.status === 'waiting')
  if (running) return `${running.title} — ${running.conclusion}`
  const last = nodes[nodes.length - 1]
  return last ? `${last.title} — ${last.conclusion}` : '暂无进展'
}

function getPreviewEndpoint(status: ProjectStatus | null): string | null {
  if (!status?.preview) return null
  const ep = status.preview.endpoint
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

export function ArtifactPanel({
  project,
  status,
  messages,
  isOpen,
  onToggle,
}: ArtifactPanelProps) {
  const [previewExpanded, setPreviewExpanded] = useState(false)
  const nodes = buildProgressNodes(messages)
  const previewUrl = getPreviewEndpoint(status)
  const hasPreview = !!previewUrl && (
    status?.preview?.status === 'PREVIEW_READY' ||
    status?.preview?.status === 'PREVIEW_DEPLOYMENT_SUCCEEDED' ||
    status?.preview?.status === 'PREVIEW_SUCCEEDED'
  )

  return (
    <>
      {/* Toggle button when panel is closed */}
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
          {/* Preview section */}
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

          {/* Download section */}
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

          {/* Status summary */}
          {status?.goal && (
            <div className="artifact-status-section">
              <div className="artifact-section-title">执行摘要</div>
              <div className="artifact-summary">
                <div className="artifact-summary-conclusion">{activeConclusion(nodes)}</div>
              </div>
            </div>
          )}

          {/* Execution steps rail */}
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

          {/* Empty state */}
          {!hasPreview && nodes.length === 0 && (
            <div className="artifact-empty">
              <p>开始创建 App 后，这里会显示预览和产出物。</p>
            </div>
          )}
        </div>
      </aside>
    </>
  )
}
