import { useState, useEffect, useCallback, useRef } from 'react'
import type { Project, Conversation, Message, ProjectStatus } from '../lib/types'
import { api } from '../lib/api'
import { useSSE } from './useSSE'

export function useWorkspace() {
  const [projects, setProjects] = useState<Project[]>([])
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [currentProject, setCurrentProject] = useState<Project | null>(null)
  const [currentConv, setCurrentConv] = useState<Conversation | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [status, setStatus] = useState<ProjectStatus | null>(null)
  const [hint, setHint] = useState('')
  const [hintError, setHintError] = useState(false)
  const lastOrdinalRef = useRef(0)

  const showHint = useCallback((text: string, isError = false) => {
    setHint(text)
    setHintError(isError)
  }, [])

  const loadMessages = useCallback(async (convId: string) => {
    try {
      const msgs = await api.getMessages(convId)
      setMessages(msgs)
      // Track last ordinal for SSE
      if (msgs.length > 0) {
        lastOrdinalRef.current = Math.max(...msgs.map(m => m.ordinal))
      }
    } catch { /* ignore */ }
  }, [])

  const loadStatus = useCallback(async (projectId: string) => {
    try {
      const s = await api.getProjectStatus(projectId)
      setStatus(s)
      return s
    } catch { return null }
  }, [])

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
    const conv = convs.find(c => c.app_project_id === projectId)
    setCurrentConv(conv || null)
    if (conv) await loadMessages(conv.id)
    await loadStatus(projectId)
  }, [loadWorkspace, loadMessages, loadStatus])

  const refresh = useCallback(async () => {
    if (!currentConv) return
    await loadMessages(currentConv.id)
  }, [currentConv, loadMessages])

  // SSE connection URL
  const sseUrl = currentProject
    ? `/events/stream?project_id=${currentProject.id}&poll_interval=2.0`
    : null

  // Handle SSE events
  useSSE(sseUrl, {
    onEvent: (type, data) => {
      if (type === 'new_message') {
        const msg = data as unknown as Message
        // Avoid duplicates by checking ordinal
        setMessages(prev => {
          if (prev.some(m => m.id === msg.id)) return prev
          return [...prev, msg]
        })
        if (msg.ordinal && msg.ordinal > lastOrdinalRef.current) {
          lastOrdinalRef.current = msg.ordinal
        }
      } else if (type === 'status_change') {
        // Refresh full status from API for consistency
        if (currentProject) {
          loadStatus(currentProject.id)
        }
      }
    },
    onError: () => {
      // SSE error — fallback polling handled by slow interval below
    },
    reconnectDelay: 3000,
  })

  // Slow fallback poll: refresh project list + status every 15s
  useEffect(() => {
    if (!currentProject) return
    const interval = setInterval(async () => {
      await loadStatus(currentProject.id)
      try {
        const projs = await api.listProjects()
        setProjects(projs)
      } catch { /* ignore */ }

      const s = status
      if (s?.goal) {
        const meta = s.goal.metadata || {}
        const stage = (meta.execution_stage as string) || s.goal.execution_stage?.stage || s.goal.status
        if (s.goal.status === 'PAUSED') showHint('已暂停 - 发送"恢复"继续')
        else if (s.goal.status === 'WAITING_HUMAN') showHint('等待你的批准')
        else if (stage === 'RESEARCH_MORE') showHint('正在调研中...')
        else if (s.preview?.status === 'PREVIEW_READY' || meta.last_preview_endpoint) showHint('预览已就绪')
        else if (s.preview?.status === 'FAILED') showHint(s.preview.failure_summary || '生成失败', true)
      }
    }, 15000)
    return () => clearInterval(interval)
  }, [currentProject?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  // Initial load
  useEffect(() => {
    loadWorkspace().then(({ projs }) => {
      if (projs.length > 0) openProject(projs[0].id)
    })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return {
    projects, conversations, currentProject, currentConv, messages, status, hint, hintError,
    setCurrentProject, setCurrentConv, setMessages, setStatus, setHint, setHintError,
    showHint, loadMessages, loadStatus, loadWorkspace, openProject, refresh,
  }
}
