import { useState, useEffect, useCallback, useRef } from 'react'
import type { Project, Conversation, Message, ProjectStatus } from '../lib/types'
import { api } from '../lib/api'
import { useSSE } from './useSSE'
import {
  latestMessageTimestamp,
  parseLiveAction,
  type LiveAction,
  type LiveActivity,
  type LiveConnectionState,
} from '../lib/liveActivity'

export function useWorkspace() {
  const [projects, setProjects] = useState<Project[]>([])
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [currentProject, setCurrentProject] = useState<Project | null>(null)
  const [currentConv, setCurrentConv] = useState<Conversation | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [status, setStatus] = useState<ProjectStatus | null>(null)
  const [hint, setHint] = useState('')
  const [hintError, setHintError] = useState(false)
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
  currentProjectRef.current = currentProject
  currentConvRef.current = currentConv
  statusRef.current = status

  const showHint = useCallback((text: string, isError = false) => {
    setHint(text)
    setHintError(isError)
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

  const loadMessages = useCallback(async (convId: string) => {
    try {
      const msgs = await api.getMessages(convId)
      setMessages(msgs)
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
      const action = parseLiveAction(s?.goal?.metadata?.live_action)
      if (action) setLiveAction(action)
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
  }, [setLiveAction])

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
    const conv = convs.find(c => c.app_project_id === projectId)
    setCurrentConv(conv || null)
    if (conv) await loadMessages(conv.id)
    await loadStatus(projectId)
  }, [loadWorkspace, loadMessages, loadStatus])

  const refresh = useCallback(async () => {
    await syncProjectView()
  }, [syncProjectView])

  // SSE connection URL
  const sseUrl = currentProject
    ? `/events/stream?project_id=${currentProject.id}&poll_interval=1.0`
    : null

  useSSE(sseUrl, {
    onEvent: (type, data) => {
      if (type === 'connected' || type === 'heartbeat') {
        markHeartbeat()
        const action = parseLiveAction(data.live_action)
        if (action) setLiveAction(action)
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
      } else if (type === 'status_change') {
        const nextStatus = typeof data.status === 'string' ? data.status : null
        const nextMeta = (data.metadata && typeof data.metadata === 'object')
          ? data.metadata as Record<string, unknown>
          : null
        const action = parseLiveAction(data.live_action ?? nextMeta?.live_action)
        if (action) setLiveAction(action)
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
        void syncProjectView()
      }
    },
    onError: () => {
      // Fallback polling below covers missed frames while reconnecting
    },
    onConnectionChange: setConnection,
    reconnectDelay: 2000,
  })

  // ACTIVE: sync every 1s so Core actions never look frozen on the console.
  useEffect(() => {
    if (!currentProject) return
    const tick = async () => {
      const s = await loadStatus(currentProject.id)
      const conv = currentConvRef.current
      if (conv) await loadMessages(conv.id)
      try {
        const projs = await api.listProjects()
        setProjects(projs)
      } catch { /* ignore */ }

      const goal = s?.goal ?? statusRef.current?.goal
      if (!goal) return
      const meta = goal.metadata || {}
      const live = parseLiveAction(meta.live_action)
      if (live) {
        showHint(`Core：${live.summary}`)
        return
      }
      const stage = (meta.execution_stage as string) || goal.execution_stage?.stage || goal.status
      if (goal.status === 'PAUSED') showHint('已暂停 - 发送"恢复"继续')
      else if (goal.status === 'WAITING_HUMAN') showHint('等待你的确认或补充后继续')
      else if (goal.status === 'EXHAUSTED' || goal.status === 'BLOCKED') {
        showHint('自动路径已用尽，需要你介入后继续 — 不是已完成')
      }
      else if (String(stage).includes('NEEDS_HUMAN') || stage === 'DELIVERY_GAP_EXHAUSTED') {
        showHint('需要你介入后继续')
      }
      else if (stage === 'GENERATING') showHint('正在生成应用（可能需要几分钟）...')
      else if (String(stage).startsWith('DEPLOY_') && !String(stage).includes('NEEDS_HUMAN')) {
        showHint('部署未成功，正在自动重试...')
      }
      else if (stage === 'RESEARCH_MORE' || stage === 'DISCOVERY_NO_SELECT') {
        showHint('正在继续调研 / 取证...')
      }
      else if (s?.preview?.status === 'PREVIEW_READY' || meta.last_preview_endpoint) showHint('预览已就绪')
      else if (s?.preview?.status === 'FAILED') showHint(s.preview.failure_summary || '生成失败', true)
    }

    void tick()
    const onVisible = () => {
      if (document.visibilityState === 'visible') void tick()
    }
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('focus', onVisible)

    const active = ['ACTIVE', 'WAITING_HUMAN', 'PAUSED', 'READY'].includes(
      statusRef.current?.goal?.status || currentProject.status,
    )
    const interval = setInterval(tick, active ? 1000 : 10000)
    return () => {
      clearInterval(interval)
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('focus', onVisible)
    }
  }, [currentProject?.id, status?.goal?.status, loadStatus, loadMessages, showHint])

  useEffect(() => {
    loadWorkspace().then(({ projs }) => {
      if (projs.length > 0) openProject(projs[0].id)
    })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return {
    projects, conversations, currentProject, currentConv, messages, status, hint, hintError,
    liveActivity,
    setCurrentProject, setCurrentConv, setMessages, setStatus, setHint, setHintError,
    showHint, loadMessages, loadStatus, loadWorkspace, openProject, refresh,
  }
}
