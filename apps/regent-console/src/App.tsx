import { useState, useCallback, useEffect, useRef, useMemo } from 'react'
import { Sidebar, StageBar } from './components/Sidebar'
import { MessageList } from './components/MessageList'
import { Composer } from './components/Composer'
import { ArtifactPanel, type WorkspaceTab } from './components/ArtifactPanel'
import { useWorkspace } from './hooks/useWorkspace'
import { api } from './lib/api'
import { buildProgressNodes } from './lib/progressNodes'
import type { DiagnosticDelivery } from './lib/types'

export default function App() {
  const ws = useWorkspace()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [sending, setSending] = useState(false)
  const [artifactPanelOpen, setArtifactPanelOpen] = useState(true)
  const [workspaceTab, setWorkspaceTab] = useState<WorkspaceTab>('plan')
  const [highlightItemKey, setHighlightItemKey] = useState<string | null>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const stickToBottomRef = useRef(true)
  const lastMessageId = ws.messages.length > 0 ? ws.messages[ws.messages.length - 1].id : null

  const progressNodes = useMemo(
    () => buildProgressNodes(ws.messages, {
      toolEvents: ws.toolEvents,
      liveTool: ws.liveActivity.liveAction?.tool,
    }),
    [ws.messages, ws.toolEvents, ws.liveActivity.liveAction?.tool],
  )

  useEffect(() => {
    const el = document.querySelector('.messages') as HTMLElement | null
    if (!el) return
    const onScroll = () => {
      const gap = el.scrollHeight - el.scrollTop - el.clientHeight
      stickToBottomRef.current = gap < 80
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [ws.currentProject?.id])

  useEffect(() => {
    if (!stickToBottomRef.current) return
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [ws.messages.length, lastMessageId])

  useEffect(() => {
    setWorkspaceTab('plan')
    setHighlightItemKey(null)
  }, [ws.currentProject?.id])

  // Highlight blocked plan item when waiting human with blocked_item_key in metadata.
  useEffect(() => {
    const meta = (ws.status?.goal?.metadata || {}) as Record<string, unknown>
    const blocked = String(meta.blocked_item_key || meta.ask_blocked_item_key || '')
    if (blocked && ws.status?.goal?.status === 'WAITING_HUMAN') {
      setHighlightItemKey(blocked)
      setWorkspaceTab('plan')
      setArtifactPanelOpen(true)
    }
  }, [ws.status?.goal?.metadata, ws.status?.goal?.status])

  const handleNew = useCallback(() => {
    ws.setCurrentProject(null)
    ws.setCurrentConv(null)
    ws.setMessages([])
    ws.setStatus(null)
    ws.setHint('')
  }, [ws])

  const handleSend = useCallback(async (text: string) => {
    setSending(true)
    try {
      if (!ws.currentConv) {
        ws.showHint('Core 正在形成产品理解草案...')
        const draft = await api.createDraft(text)
        await ws.loadWorkspace()
        ws.setCurrentProject(draft.project)
        const convs = await api.listConversations()
        const conv = convs.find(c => c.id === draft.conversation_id)
        if (conv) {
          ws.setCurrentConv(conv)
          await ws.loadMessages(conv.id)
        }
        if (draft.needs_user_fork) {
          ws.showHint('方案已就绪，请先选择一个方向后再继续')
        } else if (draft.auto_started) {
          ws.showHint('Core 已基于当前方案开始探索；你可以随时补充或修正')
        } else {
          ws.showHint('方案已形成；你可以补充后继续，或确认开始')
        }
      } else {
        ws.showHint('Core 正在处理你的指令...')
        const result = await api.guidance(ws.currentProject!.id, text)
        await ws.refresh()
        if (result.requires_confirmation && result.resulting_goal_id) {
          await api.startGoal(result.resulting_goal_id)
          ws.showHint('新目标版本已开始执行；你仍可继续补充')
        }
        else if (result.command_type === 'SELECT_OPTION') ws.showHint('已记录你的选择，正在按该方向推进。')
        else if (result.command_type === 'PAUSE') ws.showHint('已暂停。可发送修正或恢复指令。')
        else if (result.command_type === 'RESUME') ws.showHint('已恢复执行。')
        else if (result.command_type === 'CORRECT') ws.showHint('修正已记录，将在下一步执行中生效。')
        else if (result.command_type === 'APPROVE') ws.showHint('已批准，目标继续执行。')
        else if (result.command_type === 'REJECT') ws.showHint('已拒绝，Core 将重新规划。')
        else ws.showHint('')
        if (ws.currentProject) await ws.loadStatus(ws.currentProject.id)
      }
    } catch (e) {
      ws.showHint((e as Error).message, true)
    } finally {
      setSending(false)
    }
  }, [ws])

  const handleSelectOption = useCallback(async (
    projectId: string,
    optionId: string,
    label: string,
  ) => {
    setSending(true)
    try {
      ws.showHint('正在按你选择的方向继续...')
      await api.guidance(projectId, `option:${optionId} ${label}`)
      await ws.refresh()
      ws.showHint('已记录你的选择，正在按该方向推进。')
      if (ws.currentProject) await ws.loadStatus(ws.currentProject.id)
    } catch (e) {
      ws.showHint((e as Error).message, true)
    } finally {
      setSending(false)
    }
  }, [ws])

  const handleConfirm = useCallback(async (projectId: string, goalId: string, hash: string) => {
    try {
      if (!hash) {
        const state = await api.getProjectStatus(projectId)
        hash = ((state.goal?.metadata as Record<string, unknown>)?.goal_spec_hash as string) || ''
        if (!hash) {
          const specHash = (state.goal?.metadata as Record<string, unknown>)?.spec_hash as string
          if (specHash) hash = specHash
        }
      }
      if (!hash) {
        ws.showHint('缺少目标版本哈希，请刷新后重试', true)
        return
      }
      await api.confirmProject(projectId, hash)
      try {
        await api.startGoal(goalId)
      } catch (startErr) {
        const msg = String((startErr as Error).message || startErr)
        if (/startable or retryable/i.test(msg)) {
          ws.showHint('该目标已在执行中')
          await ws.openProject(projectId)
          return
        }
        throw startErr
      }
      ws.showHint('Core 已开始执行，你无需继续操作')
      await ws.openProject(projectId)
    } catch (e) {
      ws.showHint((e as Error).message, true)
    }
  }, [ws])

  const handleTaskAction = useCallback(async (
    taskId: string,
    approved: boolean,
    opts?: { always?: boolean; optionId?: string; reason?: string },
  ) => {
    try {
      await api.completeTask(taskId, approved, opts?.reason, {
        always_allow: opts?.always,
        option_id: opts?.optionId,
      })
      await ws.refresh()
      ws.showHint(approved ? '已批准' : '已拒绝')
    } catch (e) {
      ws.showHint((e as Error).message, true)
    }
  }, [ws])

  const handleQuickAction = useCallback(async (text: string) => {
    if (text === '停止执行') {
      const goalId = ws.status?.goal?.id
      if (!goalId) {
        ws.showHint('当前没有可停止的目标', true)
        return
      }
      try {
        await api.abortGoal(goalId)
        ws.showHint('已请求停止：本轮将结束并保留草稿')
        await ws.refresh()
        if (ws.currentProject) await ws.loadStatus(ws.currentProject.id)
      } catch (e) {
        ws.showHint((e as Error).message, true)
      }
      return
    }
    handleSend(text)
  }, [handleSend, ws])

  const handleUpload = useCallback(async (file: File) => {
    try {
      ws.showHint('正在上传文件...')
      await api.uploadFile(file, ws.currentProject?.id)
      ws.showHint(`文件 ${file.name} 已上传`)
    } catch (e) {
      ws.showHint((e as Error).message, true)
    }
  }, [ws])

  const openWorkspace = useCallback((tab?: WorkspaceTab) => {
    setArtifactPanelOpen(true)
    if (tab) setWorkspaceTab(tab)
  }, [])

  const title = ws.currentProject
    ? ws.currentProject.name
    : '新任务'

  return (
    <div className="app">
      {sidebarOpen && (
        <button
          type="button"
          className="sidebar-backdrop"
          aria-label="关闭侧栏"
          onClick={() => setSidebarOpen(false)}
        />
      )}
      <Sidebar
        projects={ws.projects}
        currentProject={ws.currentProject}
        onSelect={async (id) => { await ws.openProject(id); setSidebarOpen(false) }}
        onNew={handleNew}
        isOpen={sidebarOpen}
      />
      <main className="main">
        <header className="top">
          <button className="mobile-menu" onClick={() => setSidebarOpen(!sidebarOpen)}>☰</button>
          <div className="title">{title}</div>
          <button
            type="button"
            className={`top-workspace-btn ${artifactPanelOpen ? 'active' : ''}`}
            onClick={() => {
              if (artifactPanelOpen) setArtifactPanelOpen(false)
              else openWorkspace(workspaceTab || 'plan')
            }}
          >
            工作区
          </button>
        </header>
        <StageBar
          status={ws.status}
          planItems={ws.planItems}
          liveActivity={ws.liveActivity}
          onQuickAction={handleQuickAction}
        />
        <MessageList
          messages={ws.messages}
          currentProjectId={ws.currentProject?.id || null}
          goalStatus={ws.status?.goal?.status || ws.currentProject?.status || null}
          executionStage={
            String(
              (ws.status?.goal?.metadata as Record<string, unknown> | undefined)
                ?.execution_stage || '',
            ) || null
          }
          goalDiagnostic={
            ((ws.status?.goal?.metadata as Record<string, unknown> | undefined)
              ?.diagnostic_delivery as DiagnosticDelivery | undefined)
            || null
          }
          agentLoopExit={
            ((ws.status?.goal?.metadata as Record<string, unknown> | undefined)
              ?.agent_loop_exit as Record<string, unknown> | undefined)
            || null
          }
          toolEvents={ws.toolEvents}
          liveTool={ws.liveActivity.liveAction?.tool || null}
          onConfirm={handleConfirm}
          onSelectOption={handleSelectOption}
          onTaskAction={handleTaskAction}
          onInspectSource={() => openWorkspace('changes')}
          onOpenPreview={() => openWorkspace('preview')}
          onOpenReview={() => openWorkspace('review')}
          onExampleSend={(text) => { void handleSend(text) }}
        />
        <div ref={messagesEndRef} />
        <Composer
          onSend={handleSend}
          onUpload={handleUpload}
          disabled={sending}
          userHint={ws.userHint}
          userHintError={ws.userHintError}
          coreHint={ws.coreHint}
          coreHintError={ws.coreHintError}
          goalStatus={ws.status?.goal?.status || ws.currentProject?.status || null}
          goalId={ws.status?.goal?.id || null}
        />
      </main>
      <ArtifactPanel
        isOpen={artifactPanelOpen}
        onToggle={() => setArtifactPanelOpen(!artifactPanelOpen)}
        project={ws.currentProject}
        status={ws.status}
        messages={ws.messages}
        liveAction={ws.liveActivity.liveAction}
        toolEvents={ws.toolEvents}
        planItems={ws.planItems}
        planTimeline={ws.planTimeline}
        activity={ws.activity}
        runtimeAgents={ws.runtimeAgents}
        activeTab={workspaceTab}
        onTabChange={setWorkspaceTab}
        highlightItemKey={highlightItemKey}
        onSelectPlanItem={(key) => {
          setHighlightItemKey(key)
          setWorkspaceTab('plan')
          const el = document.querySelector(`[data-item-key="${CSS.escape(key)}"]`)
          el?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
        }}
        onModeChanged={() => {
          if (ws.currentProject) void ws.loadStatus(ws.currentProject.id)
        }}
      />
    </div>
  )
}
