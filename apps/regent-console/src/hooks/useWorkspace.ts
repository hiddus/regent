import { useState, useEffect, useCallback, useRef } from 'react'
import type {
  ActivityEvent,
  PlanItem,
  Project,
  Conversation,
  Message,
  ProjectStatus,
  RuntimeAgent,
} from '../lib/types'
import { api } from '../lib/api'
import { useSSE } from './useSSE'
import {
  latestMessageTimestamp,
  parseLiveAction,
  type LiveAction,
  type LiveActivity,
  type LiveConnectionState,
} from '../lib/liveActivity'

function messagesEqual(a: Message[], b: Message[]): boolean {
  if (a === b) return true
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i += 1) {
    if (a[i].id !== b[i].id || a[i].ordinal !== b[i].ordinal) return false
    if (a[i].content !== b[i].content || a[i].message_type !== b[i].message_type) return false
  }
  return true
}

function mergeMessagesByOrdinal(prev: Message[], next: Message[]): Message[] {
  if (messagesEqual(prev, next)) return prev
  if (prev.length === 0) return next
  const byId = new Map(prev.map(m => [m.id, m]))
  let changed = prev.length !== next.length
  const merged = next.map(m => {
    const old = byId.get(m.id)
    if (
      old
      && old.content === m.content
      && old.message_type === m.message_type
      && old.ordinal === m.ordinal
      && JSON.stringify(old.metadata || {}) === JSON.stringify(m.metadata || {})
    ) {
      return old
    }
    changed = true
    return m
  })
  return changed ? merged : prev
}

export function useWorkspace() {
  const [projects, setProjects] = useState<Project[]>([])
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [currentProject, setCurrentProject] = useState<Project | null>(null)
  const [currentConv, setCurrentConv] = useState<Conversation | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [status, setStatus] = useState<ProjectStatus | null>(null)
  /** User-action / command feedback — not overwritten by Core live status. */
  const [userHint, setUserHint] = useState('')
  const [userHintError, setUserHintError] = useState(false)
  /** Core live status (stage / live_action). */
  const [coreHint, setCoreHint] = useState('')
  const [coreHintError, setCoreHintError] = useState(false)
  const [toolEvents, setToolEvents] = useState<Record<string, unknown>[]>([])
  const [planItems, setPlanItems] = useState<PlanItem[]>([])
  const [planTimeline, setPlanTimeline] = useState<{
    nodes: Array<Record<string, unknown>>
    edges: Array<{ from: string; to: string }>
    lanes: Array<{ owner: string; items: Record<string, unknown>[] }>
  } | null>(null)
  const [activity, setActivity] = useState<ActivityEvent[]>([])
  const [runtimeAgents, setRuntimeAgents] = useState<RuntimeAgent[]>([])
  const [liveActivity, setLiveActivity] = useState<LiveActivity>({
    connection: 'idle',
    lastProgressAt: null,
    lastHeartbeatAt: null,
    liveAction: null,
  })
  const lastOrdinalRef = useRef(0)
  const currentProjectRef = useRef<Project | null>(null)
  const currentConvRef = useRef<Conversation | null>(null)
  const statusRef = useRef<ProjectStatus | null>(null)
  const sseConnectedRef = useRef(false)
  currentProjectRef.current = currentProject
  currentConvRef.current = currentConv
  statusRef.current = status

  const loadGoalExtras = useCallback(async (goalId: string | null | undefined) => {
    if (!goalId) return
    try {
      const [plan, act, agents, timeline] = await Promise.all([
        api.getPlanItems(goalId).catch(() => [] as PlanItem[]),
        api.getGoalActivity(goalId).catch(() => ({ events: [] as ActivityEvent[], tool_events: [] as Record<string, unknown>[] })),
        api.getGoalAgents(goalId).catch(() => [] as RuntimeAgent[]),
        api.getPlanTimeline(goalId).catch(() => null),
      ])
      setPlanItems(plan)
      if (timeline && Array.isArray(timeline.nodes)) {
        setPlanTimeline({
          nodes: timeline.nodes as Array<Record<string, unknown>>,
          edges: timeline.edges || [],
          lanes: timeline.lanes || [],
        })
      }
      if (Array.isArray(act.events)) setActivity(act.events)
      if (Array.isArray(act.tool_events) && act.tool_events.length > 0) {
        setToolEvents(act.tool_events.filter(e => e && typeof e === 'object') as Record<string, unknown>[])
      }
      setRuntimeAgents(agents)
    } catch { /* ignore */ }
  }, [])

  const showHint = useCallback((text: string, isError = false) => {
    setUserHint(text)
    setUserHintError(isError)
  }, [])

  const showCoreHint = useCallback((text: string, isError = false) => {
    setCoreHint(text)
    setCoreHintError(isError)
  }, [])

  const markHeartbeat = useCallback(() => {
    setLiveActivity(prev => ({ ...prev, lastHeartbeatAt: Date.now() }))
  }, [])

  const markProgress = useCallback((at?: number | null) => {
    const ts = at && !Number.isNaN(at) ? at : Date.now()
    setLiveActivity(prev => ({
      ...prev,
      lastProgressAt: prev.lastProgressAt != null ? Math.max(prev.lastProgressAt, ts) : ts,
      lastHeartbeatAt: Date.now(),
    }))
  }, [])

  const setConnection = useCallback((connection: LiveConnectionState) => {
    sseConnectedRef.current = connection === 'connected'
    setLiveActivity(prev => ({ ...prev, connection }))
  }, [])

  const setLiveAction = useCallback((action: LiveAction | null) => {
    setLiveActivity(prev => ({
      ...prev,
      liveAction: action,
      lastHeartbeatAt: Date.now(),
      lastProgressAt: action
        ? (prev.lastProgressAt != null ? Math.max(prev.lastProgressAt, Date.now()) : Date.now())
        : prev.lastProgressAt,
    }))
  }, [])

  const applyGoalMetaHints = useCallback((goal: NonNullable<ProjectStatus['goal']>, s?: ProjectStatus | null) => {
    const meta = goal.metadata || {}
    const events = meta.tool_events
    if (Array.isArray(events)) {
      setToolEvents(events.filter(e => e && typeof e === 'object') as Record<string, unknown>[])
    }
    const live = parseLiveAction(meta.live_action)
    if (live) {
      setLiveAction(live)
      const toolBit = live.tool ? ` · ${live.tool}` : ''
      showCoreHint(`Core：${live.summary}${toolBit}`)
      return
    }
    const stage = (meta.execution_stage as string) || goal.execution_stage?.stage || goal.status
    if (goal.status === 'PAUSED') showCoreHint('已暂停 - 发送"恢复"继续')
    else if (goal.status === 'WAITING_HUMAN') showCoreHint('等待你的确认或补充后继续')
    else if (goal.status === 'EXHAUSTED' || goal.status === 'BLOCKED') {
      showCoreHint('自动路径已用尽，需要你介入后继续 — 不是已完成')
    }
    else if (String(stage).includes('NEEDS_HUMAN') || stage === 'DELIVERY_GAP_EXHAUSTED') {
      showCoreHint('需要你介入后继续')
    }
    else if (stage === 'GENERATING') showCoreHint('正在生成应用（可能需要几分钟）...')
    else if (String(stage).startsWith('DEPLOY_') && !String(stage).includes('NEEDS_HUMAN')) {
      showCoreHint('部署未成功，正在自动重试...')
    }
    else if (stage === 'RESEARCH_MORE' || stage === 'DISCOVERY_NO_SELECT') {
      showCoreHint('正在继续调研 / 取证...')
    }
    else if (s?.preview?.status === 'PREVIEW_READY' || meta.last_preview_endpoint) {
      showCoreHint('预览已就绪')
    }
    else if (s?.preview?.status === 'FAILED') {
      showCoreHint(s.preview.failure_summary || '生成失败', true)
    }
  }, [setLiveAction, showCoreHint])

  const loadMessages = useCallback(async (convId: string) => {
    try {
      const msgs = await api.getMessages(convId)
      setMessages(prev => mergeMessagesByOrdinal(prev, msgs))
      if (msgs.length > 0) {
        lastOrdinalRef.current = Math.max(...msgs.map(m => m.ordinal))
        const latest = latestMessageTimestamp(msgs)
        if (latest != null) {
          setLiveActivity(prev => ({
            ...prev,
            lastProgressAt: prev.lastProgressAt != null ? Math.max(prev.lastProgressAt, latest) : latest,
          }))
        }
      }
      return msgs
    } catch {
      return null
    }
  }, [])

  const loadStatus = useCallback(async (projectId: string) => {
    try {
      const s = await api.getProjectStatus(projectId)
      setStatus(s)
      if (s?.goal) {
        applyGoalMetaHints(s.goal, s)
        void loadGoalExtras(s.goal.id)
      }
      if (s?.goal?.status) {
        setCurrentProject(prev => (
          prev && prev.id === projectId && prev.status !== s.goal!.status
            ? { ...prev, status: s.goal!.status }
            : prev
        ))
        setProjects(prev => prev.map(p => (
          p.id === projectId && s.goal && p.status !== s.goal.status
            ? { ...p, status: s.goal.status }
            : p
        )))
      }
      return s
    } catch { return null }
  }, [applyGoalMetaHints, loadGoalExtras])

  const syncProjectView = useCallback(async () => {
    const project = currentProjectRef.current
    const conv = currentConvRef.current
    if (!project) return
    await Promise.all([
      loadStatus(project.id),
      conv ? loadMessages(conv.id) : Promise.resolve(null),
    ])
  }, [loadStatus, loadMessages])

  const loadWorkspace = useCallback(async () => {
    try {
      const [projs, convs] = await Promise.all([
        api.listProjects(),
        api.listConversations(),
      ])
      setProjects(projs)
      setConversations(convs)
      return { projs, convs }
    } catch {
      return { projs: [], convs: [] }
    }
  }, [])

  const openProject = useCallback(async (projectId: string) => {
    const { projs, convs } = await loadWorkspace()
    const proj = projs.find(p => p.id === projectId)
    if (!proj) return
    setCurrentProject(proj)
    setLiveActivity({
      connection: 'connecting',
      lastProgressAt: null,
      lastHeartbeatAt: null,
      liveAction: null,
    })
    setToolEvents([])
    setPlanItems([])
    setPlanTimeline(null)
    setActivity([])
    setRuntimeAgents([])
    setCoreHint('')
    const conv = convs.find(c => c.app_project_id === projectId)
    setCurrentConv(conv || null)
    if (conv) await loadMessages(conv.id)
    await loadStatus(projectId)
  }, [loadWorkspace, loadMessages, loadStatus])

  const refresh = useCallback(async () => {
    await syncProjectView()
  }, [syncProjectView])

  const sseUrl = currentProject
    ? `/events/stream?project_id=${currentProject.id}&poll_interval=1.0`
    : null

  useSSE(sseUrl, {
    onEvent: (type, data) => {
      if (type === 'connected' || type === 'heartbeat') {
        markHeartbeat()
        const action = parseLiveAction(data.live_action)
        if (action) {
          setLiveAction(action)
          const toolBit = action.tool ? ` · ${action.tool}` : ''
          showCoreHint(`Core：${action.summary}${toolBit}`)
        }
        if (type === 'heartbeat' && data.has_changes === true) {
          const conv = currentConvRef.current
          if (conv) void loadMessages(conv.id)
        }
        return
      }
      if (type === 'new_message') {
        const msg = data as unknown as Message
        setMessages(prev => {
          if (prev.some(m => m.id === msg.id)) return prev
          return [...prev, msg]
        })
        if (msg.ordinal && msg.ordinal > lastOrdinalRef.current) {
          lastOrdinalRef.current = msg.ordinal
        }
        const created = msg.created_at ? Date.parse(msg.created_at) : NaN
        markProgress(Number.isNaN(created) ? Date.now() : created)
        const project = currentProjectRef.current
        if (project) void loadStatus(project.id)
      } else if (type === 'agent_event') {
        // H1.2: structured RegentEvent → activity feed (no Chinese reverse parse).
        const summary = String(data.summary || data.type || 'agent_event')
        const evType = String(data.type || 'status')
        setActivity(prev => {
          const next = [
            ...prev,
            {
              type: evType,
              turn: typeof data.turn === 'number' ? data.turn : null,
              tool: typeof data.tool === 'string' ? data.tool : null,
              summary,
              args_preview: null,
              result_preview: null,
              updated_at: typeof data.at === 'string' ? data.at : null,
            } as ActivityEvent,
          ]
          return next.slice(-80)
        })
        if (evType === 'plan_updated' || evType === 'tool_call') {
          const goalId = statusRef.current?.goal?.id
          if (goalId) void loadGoalExtras(goalId)
        }
        markProgress(Date.now())
        const toolBit = data.tool ? ` · ${String(data.tool)}` : ''
        showCoreHint(`Core：${summary}${toolBit}`)
      } else if (type === 'status_change') {
        const nextStatus = typeof data.status === 'string' ? data.status : null
        const nextMeta = (data.metadata && typeof data.metadata === 'object')
          ? data.metadata as Record<string, unknown>
          : null
        const action = parseLiveAction(data.live_action ?? nextMeta?.live_action)
        if (action) {
          setLiveAction(action)
          const toolBit = action.tool ? ` · ${action.tool}` : ''
          showCoreHint(`Core：${action.summary}${toolBit}`)
        }
        if (nextMeta && Array.isArray(nextMeta.tool_events)) {
          setToolEvents(
            nextMeta.tool_events.filter(e => e && typeof e === 'object') as Record<string, unknown>[],
          )
        }
        setStatus(prev => {
          if (!prev?.goal) return prev
          return {
            ...prev,
            goal: {
              ...prev.goal,
              status: nextStatus || prev.goal.status,
              metadata: nextMeta || prev.goal.metadata,
              execution_stage: nextMeta?.execution_stage
                ? { stage: String(nextMeta.execution_stage) }
                : prev.goal.execution_stage,
            },
          }
        })
        if (nextStatus) {
          const project = currentProjectRef.current
          if (project) {
            setCurrentProject(prev => (prev ? { ...prev, status: nextStatus } : prev))
            setProjects(prev => prev.map(p => (p.id === project.id ? { ...p, status: nextStatus } : p)))
          }
        }
        const updated = typeof data.updated_at === 'string' ? Date.parse(data.updated_at) : NaN
        markProgress(Number.isNaN(updated) ? Date.now() : updated)
        // Light refresh: status already applied; only reload messages (not full sync storm).
        const conv = currentConvRef.current
        if (conv) void loadMessages(conv.id)
      }
    },
    onError: () => {
      sseConnectedRef.current = false
    },
    onConnectionChange: setConnection,
    reconnectDelay: 2000,
  })

  // Fallback poll only when SSE is down (SSE is primary). Interval ≥10s.
  useEffect(() => {
    if (!currentProject) return
    const tick = async () => {
      if (sseConnectedRef.current) return
      const s = await loadStatus(currentProject.id)
      const conv = currentConvRef.current
      if (conv) await loadMessages(conv.id)
      try {
        const projs = await api.listProjects()
        setProjects(projs)
      } catch { /* ignore */ }
      const goal = s?.goal ?? statusRef.current?.goal
      if (goal) applyGoalMetaHints(goal, s)
    }

    void tick()
    const onVisible = () => {
      if (document.visibilityState === 'visible') void tick()
    }
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('focus', onVisible)
    // Semantic: degraded poll when SSE disconnected (was historically 3000ms).
    const FALLBACK_POLL_MS = 10_000
    const interval = setInterval(tick, FALLBACK_POLL_MS)
    return () => {
      clearInterval(interval)
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('focus', onVisible)
    }
  }, [currentProject?.id, loadStatus, loadMessages, applyGoalMetaHints])

  useEffect(() => {
    loadWorkspace().then(({ projs }) => {
      if (projs.length > 0) openProject(projs[0].id)
    })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Combined hint: user action takes priority over core status for the composer strip.
  const hint = userHint || coreHint
  const hintError = userHint ? userHintError : coreHintError

  return {
    projects, conversations, currentProject, currentConv, messages, status,
    hint, hintError, userHint, userHintError, coreHint, coreHintError, toolEvents,
    planItems, planTimeline, activity, runtimeAgents,
    liveActivity,
    setCurrentProject, setCurrentConv, setMessages, setStatus,
    setHint: setUserHint, setHintError: setUserHintError,
    showHint, showCoreHint, loadMessages, loadStatus, loadWorkspace, openProject, refresh,
  }
}
