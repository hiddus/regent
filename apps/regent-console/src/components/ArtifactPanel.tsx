import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { LiveAction } from '../lib/liveActivity'
import type {
  ActivityEvent,
  DeliveryReview,
  Message,
  PlanItem,
  Project,
  ProjectStatus,
  RuntimeAgent,
  WorkspaceTreeNode,
} from '../lib/types'
import {
  ACTIVITY_LABEL,
  agentInitials,
  countActiveAgents,
  deriveAgents,
} from '../lib/agents'
import { api } from '../lib/api'
import { isQuietActive, latestMessageTimestamp } from '../lib/liveActivity'
import { ResultCard } from './ResultCard'

export type WorkspaceTab = 'plan' | 'run' | 'changes' | 'preview' | 'review'

interface ArtifactPanelProps {
  project: Project | null
  status: ProjectStatus | null
  messages: Message[]
  liveAction?: LiveAction | null
  toolEvents?: Record<string, unknown>[]
  planItems?: PlanItem[]
  planTimeline?: {
    nodes: Array<Record<string, unknown>>
    edges: Array<{ from: string; to: string }>
    lanes: Array<{ owner: string; items: Record<string, unknown>[] }>
  } | null
  activity?: ActivityEvent[]
  runtimeAgents?: RuntimeAgent[]
  isOpen: boolean
  onToggle: () => void
  /** Controlled tab (optional); parent can force-open preview/review. */
  activeTab?: WorkspaceTab
  onTabChange?: (tab: WorkspaceTab) => void
  highlightItemKey?: string | null
  onSelectPlanItem?: (itemKey: string) => void
  onModeChanged?: () => void
}

const PLAN_STATUS_MARK: Record<string, string> = {
  pending: '○',
  in_progress: '●',
  completed: '✓',
  cancelled: '–',
  failed: '!',
}

const TABS: { id: WorkspaceTab; label: string }[] = [
  { id: 'plan', label: '计划' },
  { id: 'changes', label: '中间产物' },
  { id: 'preview', label: '交付物' },
  { id: 'review', label: '验证' },
]

function planStatusLabel(status: string): string {
  const s = (status || 'pending').toLowerCase()
  if (s === 'in_progress') return '进行中'
  if (s === 'completed') return '完成'
  if (s === 'cancelled') return '取消'
  if (s === 'failed') return '失败'
  return '待办'
}

function fileExtLabel(path: string): string {
  const base = path.split('/').pop() || path
  const i = base.lastIndexOf('.')
  if (i <= 0) return '文件'
  const ext = base.slice(i + 1).toLowerCase()
  const map: Record<string, string> = {
    ts: 'TypeScript',
    tsx: 'TSX',
    js: 'JavaScript',
    jsx: 'JSX',
    py: 'Python',
    css: 'CSS',
    html: 'HTML',
    json: 'JSON',
    md: 'Markdown',
    yml: 'YAML',
    yaml: 'YAML',
    toml: 'TOML',
    sql: 'SQL',
  }
  return map[ext] || ext.toUpperCase()
}

const PREVIEW_READY_STATUSES = new Set([
  'PREVIEW_READY',
  'PREVIEW_DEPLOYMENT_SUCCEEDED',
  'PREVIEW_SUCCEEDED',
  'SUCCEEDED',
])

function ensurePreviewTrailingSlash(url: string): string {
  // Runtime preview root must end with `/` so relative hrefs resolve under the
  // deployment prefix (and so CSP-backed <base href> matches the document path).
  try {
    const u = new URL(url, location.origin)
    if (/\/preview\/runtime\/[^/]+$/i.test(u.pathname)) {
      u.pathname = `${u.pathname}/`
      return u.toString()
    }
  } catch {
    /* keep original */
  }
  return url
}

function normalizePreviewUrl(ep: string | null | undefined): string | null {
  if (!ep) return null
  try {
    const u = new URL(ep, location.origin)
    // Worker-local preview ports are not reachable from the browser. Only
    // path-prefixed /preview/... URLs (or same-origin API hosts) are usable.
    if (u.hostname === 'regent-api' || u.hostname === 'localhost' || u.hostname === '127.0.0.1') {
      if (u.pathname.startsWith('/preview/')) {
        return ensurePreviewTrailingSlash(location.origin + u.pathname + u.search + u.hash)
      }
      return null
    }
    return ensurePreviewTrailingSlash(u.toString())
  } catch {
    const s = String(ep).replace('http://regent-api:8000', location.origin)
    if (/^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?\/?$/i.test(s)) return null
    return ensurePreviewTrailingSlash(s)
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

function TrustPostureInline({ goalId }: { goalId: string }) {
  const [label, setLabel] = useState('姿态…')
  useEffect(() => {
    let cancelled = false
    api.getTrustPosture(goalId).then((r) => {
      if (cancelled || !r.posture) return
      const level = String(r.posture.level || 'standard')
      const mode = String(r.posture.execution_mode || 'ask')
      const map: Record<string, string> = {
        restricted: '受限',
        standard: '标准',
        elevated: '高信任',
      }
      setLabel(`姿态 · ${map[level] || level} · ${mode}`)
    }).catch(() => {
      if (!cancelled) setLabel('姿态 · 未知')
    })
    return () => {
      cancelled = true
    }
  }, [goalId])
  return <span title="Trust posture">{label}</span>
}

export function ArtifactPanel({
  project,
  status,
  messages,
  liveAction = null,
  toolEvents = [],
  planItems = [],
  planTimeline = null,
  activity = [],
  runtimeAgents = [],
  isOpen,
  onToggle,
  activeTab: controlledTab,
  onTabChange,
  highlightItemKey = null,
  onSelectPlanItem,
  onModeChanged,
}: ArtifactPanelProps) {
  const [internalTab, setInternalTab] = useState<WorkspaceTab>('plan')
  const tab = controlledTab ?? internalTab
  const setTab = useCallback((next: WorkspaceTab) => {
    if (onTabChange) onTabChange(next)
    else setInternalTab(next)
  }, [onTabChange])

  const [previewExpanded, setPreviewExpanded] = useState(false)
  const [showTimeline, setShowTimeline] = useState(false)
  const [showAdvancedJson, setShowAdvancedJson] = useState(false)
  const [review, setReview] = useState<DeliveryReview | null>(null)
  const [reviewLoading, setReviewLoading] = useState(false)
  const [reviewError, setReviewError] = useState<string | null>(null)
  const [tree, setTree] = useState<WorkspaceTreeNode[]>([])
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [fileContent, setFileContent] = useState<string>('')
  const [fileError, setFileError] = useState<string | null>(null)
  const autoPreviewDone = useRef<string | null>(null)
  const autoCompleteDone = useRef<string | null>(null)

  const agents = useMemo(() => {
    const base = deriveAgents(status, liveAction, messages)
    if (!runtimeAgents.length) return base
    const byId = new Map(base.map(a => [a.id, a]))
    for (const ra of runtimeAgents) {
      byId.set(ra.id, {
        id: ra.id,
        name: ra.name,
        role: ra.role || 'executor',
        role_label: ra.role_label || ra.name,
        kind: (ra.kind as 'core' | 'hive' | 'spec' | 'derived') || 'derived',
        activity: ra.activity,
        detail: ra.detail || (ra.tool ? `工具 ${ra.tool}` : null),
        is_main: !!ra.is_main,
      })
    }
    return [...byId.values()]
  }, [status, liveAction, messages, runtimeAgents])
  const activeCount = countActiveAgents(agents)
  const deliverable = useMemo(
    () => resolveDeliverable(status, messages),
    [status, messages],
  )
  const previewUrl = deliverable.url
  const hasPreview = deliverable.ready

  const exit = (status?.goal?.metadata?.agent_loop_exit || null) as Record<string, unknown> | null
  const bundle = (exit?.result_bundle || null) as Record<string, unknown> | null
  const exitKind = String(exit?.exit_kind || '')
  const goalId = status?.goal?.id || ''

  // Default to plan when items appear; auto-switch preview once per project.
  useEffect(() => {
    if (!project?.id) return
    if (planItems.length > 0 && tab === 'run' && !controlledTab) {
      // keep user choice if they left plan
    }
  }, [project?.id, planItems.length, tab, controlledTab])

  useEffect(() => {
    if (!project?.id || !hasPreview) return
    if (autoPreviewDone.current === project.id) return
    autoPreviewDone.current = project.id
    setTab('preview')
  }, [project?.id, hasPreview, setTab])

  useEffect(() => {
    if (!project?.id || exitKind !== 'COMPLETE') return
    if (autoCompleteDone.current === project.id) return
    autoCompleteDone.current = project.id
    if (hasPreview) setTab('preview')
    else setTab('review')
  }, [project?.id, exitKind, hasPreview, setTab])

  useEffect(() => {
    setReview(null)
    setReviewError(null)
    setShowAdvancedJson(false)
    setTree([])
    setSelectedFile(null)
    setFileContent('')
    autoPreviewDone.current = null
    autoCompleteDone.current = null
    if (!controlledTab) setInternalTab('plan')
  }, [project?.id, controlledTab])

  const loadReview = useCallback(async () => {
    if (!project) return
    setReviewLoading(true)
    setReviewError(null)
    try {
      const data = await api.getDeliveryReview(project.id)
      setReview(data)
    } catch (e) {
      setReviewError((e as Error).message || '暂无审阅数据')
    } finally {
      setReviewLoading(false)
    }
  }, [project])

  useEffect(() => {
    if (tab === 'review' && project && !review && !reviewLoading) {
      void loadReview()
    }
  }, [tab, project, review, reviewLoading, loadReview])

  const loadTree = useCallback(async () => {
    if (!project) return
    try {
      const data = await api.getWorkspaceTree(project.id)
      setTree((data.entries || []).filter(e => e.kind === 'file').slice(0, 200))
      setFileError(null)
    } catch (e) {
      setFileError((e as Error).message || '暂无源码树')
      setTree([])
    }
  }, [project])

  useEffect(() => {
    if (tab === 'changes' && project && tree.length === 0) {
      void loadTree()
    }
  }, [tab, project, tree.length, loadTree])

  const openFile = useCallback(async (path: string) => {
    if (!project) return
    setSelectedFile(path)
    try {
      const data = await api.getWorkspaceFile(project.id, path)
      setFileContent(data.content)
      setFileError(null)
    } catch (e) {
      setFileContent('')
      setFileError((e as Error).message || '无法读取文件')
    }
  }, [project])

  const budget = review?.budget
  const activityFeed = activity.length > 0
    ? activity.slice(-12).reverse()
    : toolEvents.slice(-12).reverse().map(e => ({
        type: String(e.type || 'tool_call'),
        tool: typeof e.tool === 'string' ? e.tool : null,
        summary: typeof e.summary === 'string' ? e.summary : undefined,
        turn: typeof e.turn === 'number' ? e.turn : null,
        input_tokens: typeof e.input_tokens === 'number' ? e.input_tokens : null,
        output_tokens: typeof e.output_tokens === 'number' ? e.output_tokens : null,
        cached_tokens: typeof e.cached_tokens === 'number' ? e.cached_tokens : null,
      }))
  const budgetTurnsText = budget?.turns != null
    ? `${budget.turns}${budget.max_turns != null ? ` / ${budget.max_turns}` : ''} 轮`
    : null
  const budgetTokens = (budget?.input_tokens || 0) + (budget?.output_tokens || 0)
  const budgetTokensText = budget && (budget.input_tokens != null || budget.output_tokens != null)
    ? `${budgetTokens.toLocaleString()}${budget.max_tokens != null ? ` / ${budget.max_tokens.toLocaleString()}` : ''} tokens`
    : null

  const hasDeps = !!(planTimeline && planTimeline.edges.length > 0)

  return (
    <>
      {isOpen && (
        <button
          type="button"
          className="workspace-backdrop"
          aria-label="关闭工作区"
          onClick={onToggle}
        />
      )}
      {!isOpen && (
        <button className="artifact-panel-toggle" onClick={onToggle} title="打开工作区">
          <span className="toggle-icon">◁</span>
          <span className="toggle-label">工作区</span>
        </button>
      )}

      <aside className={`artifact-panel workspace-panel ${isOpen ? 'open' : ''}`}>
        <div className="workspace-sheet-handle" aria-hidden />
        <div className="artifact-panel-header">
          <div><h3>项目查看</h3><small>只读 · 所有操作请在对话中完成</small></div>
          <button className="close-btn" onClick={onToggle} aria-label="关闭面板">×</button>
        </div>

        <nav className="workspace-tabs" aria-label="工作区视图">
          {TABS.map(t => (
            <button
              key={t.id}
              type="button"
              className={`workspace-tab ${tab === t.id ? 'active' : ''}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
              {t.id === 'plan' && planItems.length > 0 ? (
                <span className="tab-count">{planItems.length}</span>
              ) : null}
              {t.id === 'run' && activeCount > 0 ? (
                <span className="tab-count live">{activeCount}</span>
              ) : null}
              {t.id === 'preview' && hasPreview ? (
                <span className="tab-dot" aria-hidden />
              ) : null}
            </button>
          ))}
        </nav>

        <div className="artifact-panel-content workspace-tab-body">
          {tab === 'plan' && (
            <div className="workspace-pane plan-pane">
              <p className="readonly-note">此处同步展示已形成的计划，不会从侧栏启动或修改执行。</p>

              {exitKind === 'COMPLETE' && bundle && (
                <ResultCard
                  exitKind="COMPLETE"
                  summary={String(bundle.summary || '本轮已完成')}
                  openItems={Array.isArray(bundle.open_items) ? (bundle.open_items as string[]) : []}
                  artifacts={
                    Array.isArray(bundle.artifacts)
                      ? (bundle.artifacts as Array<{ uri: string; label?: string; kind?: string }>)
                      : bundle.artifact_uri
                        ? [
                            {
                              uri: String(bundle.artifact_uri),
                              label: '主产物',
                              kind: 'primary',
                            },
                          ]
                        : []
                  }
                  previewUrl={bundle.preview_url ? String(bundle.preview_url) : previewUrl}
                  onOpenPreview={() => setTab('preview')}
                  onOpenReview={() => setTab('review')}
                  onOpenArtifacts={() => setTab('changes')}
                />
              )}
              {exitKind === 'STOP' && (
                <ResultCard
                  exitKind="STOP"
                  summary=""
                  stopReason={String(exit?.stop_reason || '')}
                />
              )}

              {planItems.length === 0 ? (
                <div className="artifact-empty">
                  <p>
                    {isQuietActive({
                      goalStatus: status?.goal?.status,
                      generationProgress: String(
                        status?.generation_progress ||
                          (status?.goal?.metadata as Record<string, unknown> | undefined)
                            ?.generation_progress ||
                          '',
                      ),
                      liveAction,
                      lastProgressAt: latestMessageTimestamp(messages),
                    })
                      ? '尚未生成工作清单 — 执行已开跑但暂无进展（可在对话里点「继续此目标」或补充指令）'
                      : '尚未生成工作清单 — Agent 规划中…'}
                  </p>
                </div>
              ) : (
                <ul className="plan-checklist">
                  {planItems.map(item => {
                    const st = String(item.status || 'pending').toLowerCase()
                    const mark = PLAN_STATUS_MARK[st] || '○'
                    const owner = item.owner_agent_id
                      ? String(item.owner_agent_id).startsWith('subagent')
                        ? '子 Agent'
                        : String(item.owner_agent_id)
                      : null
                    const key = item.item_key || item.id
                    const highlighted = highlightItemKey && highlightItemKey === item.item_key
                    return (
                      <li
                        key={item.id || item.item_key}
                        className={`plan-item status-${st}${highlighted ? ' is-highlight' : ''}`}
                        data-item-key={item.item_key}
                      >
                        <button
                          type="button"
                          className="plan-item-btn"
                          onClick={() => onSelectPlanItem?.(item.item_key)}
                        >
                          <span className="plan-mark" aria-hidden>{mark}</span>
                          <span className="plan-status">{planStatusLabel(st)}</span>
                          <span className="plan-content">{item.content || item.item_key}</span>
                          {owner && <span className="plan-owner">{owner}</span>}
                        </button>
                      </li>
                    )
                  })}
                </ul>
              )}

              {hasDeps && (
                <div className="plan-timeline-fold">
                  <button
                    type="button"
                    className="task-detail-toggle"
                    onClick={() => setShowTimeline(v => !v)}
                  >
                    {showTimeline ? '收起依赖视图' : '依赖视图（只读）'}
                  </button>
                  {showTimeline && planTimeline && (
                    <div className="plan-timeline">
                      {planTimeline.lanes.map(lane => (
                        <div key={lane.owner} className="timeline-lane">
                          <div className="timeline-lane-title">
                            {String(lane.owner).startsWith('subagent')
                              ? '子 Agent'
                              : lane.owner === 'primary'
                                ? 'Primary'
                                : lane.owner}
                          </div>
                          <ul className="timeline-bars">
                            {lane.items.map((item) => {
                              const st = String(item.status || 'pending').toLowerCase()
                              const deps = Array.isArray(item.dependencies)
                                ? item.dependencies as string[]
                                : []
                              return (
                                <li key={String(item.id || item.item_key)} className={`timeline-bar status-${st}`}>
                                  <span className="plan-mark" aria-hidden>{PLAN_STATUS_MARK[st] || '○'}</span>
                                  <span className="plan-content">{String(item.content || item.item_key || '')}</span>
                                  {deps.length > 0 && (
                                    <span className="plan-owner">← {deps.join(', ')}</span>
                                  )}
                                </li>
                              )
                            })}
                          </ul>
                        </div>
                      ))}
                      <p className="hint">依赖边 {planTimeline.edges.length} 条（只读）</p>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Legacy run controls intentionally stay out of the read-only viewer. */}
          {false && tab === 'run' && goalId && (
            <div className="workspace-pane run-pane">
              {goalId && (
                <div className="trust-posture-row hint" style={{ marginBottom: 8 }}>
                  <TrustPostureInline goalId={goalId} />
                  {' · '}
                  <button
                    type="button"
                    className="linkish"
                    onClick={() => {
                      api.getEvidenceBundle(goalId).then((r) => {
                        const dig = String(r.bundle?.digest || '').slice(0, 16)
                        window.alert(
                          r.verify?.ok
                            ? `证据包校验通过 · digest ${dig}…`
                            : `证据包校验失败 · ${JSON.stringify(r.verify || {})}`,
                        )
                      })
                    }}
                  >
                    证据包
                  </button>
                  {' · '}
                  <button
                    type="button"
                    className="linkish"
                    onClick={() => {
                      api.undoTurn(goalId, true).then((r) => {
                        if (!r.ok) {
                          window.alert(r.preview || '无法撤回')
                          return
                        }
                        if (window.confirm(`${r.preview || '确认撤回上一回合？'}\n\n应用撤回？`)) {
                          api.undoTurn(goalId, false)
                        }
                      })
                    }}
                  >
                    撤回上一回合
                  </button>
                </div>
              )}

              <div className="artifact-section-title">
                <span>参与 Agent</span>
                {agents.length > 0 && (
                  <span className="agent-panel-count">
                    {activeCount > 0 ? `${activeCount} 个活动` : `${agents.length} 个`}
                  </span>
                )}
              </div>
              {agents.length === 0 ? (
                <div className="artifact-empty compact">
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

              <div className="artifact-section-title" style={{ marginTop: 16 }}>
                <span>活动流</span>
              </div>
              {activityFeed.length === 0 ? (
                <div className="artifact-empty compact"><p>尚无活动事件。</p></div>
              ) : (
                <ul className="activity-feed">
                  {activityFeed.map((ev, idx) => {
                    const tokens = [
                      ev.turn != null ? `第 ${Number(ev.turn) + 1} 轮` : null,
                      ev.tool ? `调用 ${ev.tool}` : null,
                      (ev.input_tokens != null || ev.output_tokens != null)
                        ? `累计 ${(Number(ev.input_tokens || 0) + Number(ev.output_tokens || 0)).toLocaleString()} tokens`
                        : null,
                    ].filter(Boolean)
                    return (
                      <li key={idx}>
                        <div className="activity-summary">{ev.summary || ev.type || 'event'}</div>
                        {tokens.length > 0 && (
                          <div className="activity-meta">{tokens.join(' · ')}</div>
                        )}
                      </li>
                    )
                  })}
                </ul>
              )}
            </div>
          )}

          {tab === 'changes' && (
            <div className="workspace-pane changes-pane source-browser">
              {!project && (
                <div className="artifact-empty"><p>选择 App 后可浏览源码改动。</p></div>
              )}
              {project && fileError && tree.length === 0 && (
                <div className="artifact-empty compact"><p>{fileError}</p></div>
              )}
              {project && !fileError && tree.length === 0 && (
                <div className="artifact-empty compact">
                  <p>本轮未产生可浏览的源码文件（或快照尚未就绪）。</p>
                </div>
              )}
              {tree.length > 0 && (
                <>
                  <div className="changes-meta">
                    <span>{tree.length} 个文件</span>
                    {selectedFile ? (
                      <span className="changes-ext">{fileExtLabel(selectedFile)}</span>
                    ) : (
                      <span className="hint">点击左侧查看内容</span>
                    )}
                    <button type="button" className="linkish" onClick={() => void loadTree()}>
                      刷新
                    </button>
                  </div>
                  <div className="source-layout">
                    <ul className="source-tree">
                      {tree
                        .filter(node => !!(node.path || node.name))
                        .map(node => {
                          const path = String(node.path || node.name || '')
                          return (
                            <li key={path}>
                              <button
                                type="button"
                                className={selectedFile === path ? 'active' : ''}
                                onClick={() => void openFile(path)}
                                title={path}
                              >
                                <span className="source-file-name">{path.split('/').pop() || path}</span>
                                <span className="source-file-path">{path}</span>
                              </button>
                            </li>
                          )
                        })}
                    </ul>
                    <pre className="source-code">{fileContent || (selectedFile ? '加载中…' : '选择文件查看内容')}</pre>
                  </div>
                </>
              )}
            </div>
          )}

          {tab === 'preview' && (
            <div className="workspace-pane preview-pane">
              {hasPreview && previewUrl ? (
                <>
                  <div className="artifact-section-title">
                    <span>应用预览</span>
                    <span className="artifact-preview-badge">就绪</span>
                  </div>
                  <div className={`artifact-preview-frame ${previewExpanded ? 'expanded' : ''}`}>
                    <iframe
                      src={previewUrl}
                      title="App Preview"
                      className="artifact-preview-iframe"
                      sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-downloads allow-modals"
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
                    {project && (
                      <button
                        className="artifact-btn primary"
                        onClick={() => api.downloadArtifact(project.id)}
                      >
                        下载产出物
                      </button>
                    )}
                  </div>
                </>
              ) : (
                <div className="artifact-empty">
                  <p>
                    {(() => {
                      const diag = status?.goal?.metadata?.diagnostic_delivery as
                        | { preview?: { state?: string; reason?: string } }
                        | undefined
                      const stage = String(status?.goal?.metadata?.execution_stage || '')
                      if (diag?.preview?.reason) return diag.preview.reason
                      if (stage === 'DELIVERY_SOFT_PAUSE') {
                        return '本轮未生成可运行 Preview；可到「改动」查看草稿源码。'
                      }
                      if (status?.preview?.failure_summary) {
                        return `预览不可用：${status.preview.failure_summary}`
                      }
                      if (status?.goal?.status === 'ACHIEVED') {
                        return '目标已达成，但尚未解析到预览地址。'
                      }
                      return '尚无预览。产物就绪后会自动切换到此 Tab。'
                    })()}
                  </p>
                  {project && (
                    <button
                      type="button"
                      className="artifact-btn"
                      onClick={() => api.downloadArtifact(project.id)}
                    >
                      尝试下载当前产出
                    </button>
                  )}
                </div>
              )}
            </div>
          )}

          {tab === 'review' && (
            <div className="workspace-pane review-pane">
              {!project && (
                <div className="artifact-empty"><p>选择 App 后可审阅交付。</p></div>
              )}
              {project && reviewLoading && (
                <div className="artifact-empty compact"><p>正在加载审阅数据...</p></div>
              )}
              {project && !reviewLoading && reviewError && (
                <div className="artifact-empty compact"><p>暂无审阅数据（{reviewError}）</p></div>
              )}
              {project && !reviewLoading && !reviewError && review && (
                <div className="review-section">
                  {(budgetTurnsText || budgetTokensText) && (
                    <div className="review-block">
                      <div className="artifact-section-title"><span>预算摘要</span></div>
                      <p className="review-budget">
                        {[budgetTurnsText, budgetTokensText].filter(Boolean).join(' · ')}
                      </p>
                    </div>
                  )}
                  {review.plan != null && (
                    <div className="review-block">
                      <div className="artifact-section-title"><span>执行计划</span></div>
                      {Array.isArray((review.plan as { items?: unknown }).items) ? (
                        <ul className="plan-checklist">
                          {((review.plan as { items: Record<string, unknown>[] }).items).map((item, idx) => (
                            <li key={idx} className={`plan-item status-${String(item.status || 'pending')}`}>
                              <span className="plan-status">{String(item.status || 'pending')}</span>
                              <span className="plan-content">{String(item.content || item.title || item.item_key || '')}</span>
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p className="hint">计划已记录（见高级 JSON）</p>
                      )}
                    </div>
                  )}
                  {review.verification != null && (
                    <div className="review-block">
                      <div className="artifact-section-title"><span>验证结论</span></div>
                      <p className="review-budget">
                        {String(
                          (review.verification as Record<string, unknown>).verdict
                          || (review.verification as Record<string, unknown>).summary
                          || '见详情',
                        )}
                      </p>
                    </div>
                  )}
                  {Array.isArray(review.transcript) && review.transcript.length > 0 && (
                    <div className="review-block">
                      <div className="artifact-section-title"><span>执行摘要</span></div>
                      <ul className="review-transcript">
                        {review.transcript.slice(0, 20).map((item, idx) => (
                          <li key={idx}>{typeof item === 'string' ? item : JSON.stringify(item)}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  <button
                    type="button"
                    className="task-detail-toggle"
                    onClick={() => setShowAdvancedJson(v => !v)}
                  >
                    {showAdvancedJson ? '收起高级 JSON' : '高级 JSON'}
                  </button>
                  {showAdvancedJson && (
                    <pre className="review-pre">{JSON.stringify(review, null, 2)}</pre>
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
