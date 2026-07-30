import { useState, useCallback, useEffect, useRef, useMemo } from 'react'
import { Sidebar, StageBar } from './components/Sidebar'
import { MessageList } from './components/MessageList'
import { Composer } from './components/Composer'
import { ArtifactPanel } from './components/ArtifactPanel'
import { useWorkspace } from './hooks/useWorkspace'
import { api } from './lib/api'
import { buildProgressNodes } from './lib/progressNodes'

export default function App() {
  const ws = useWorkspace()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [sending, setSending] = useState(false)
  const [artifactPanelOpen, setArtifactPanelOpen] = useState(true)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  // Compute progress nodes for StageBar progress visualization
  const progressNodes = useMemo(() => buildProgressNodes(ws.messages), [ws.messages])

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [ws.messages])

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
        ws.showHint('Core 已基于当前理解开始探索；你可以随时补充或修正目标')
      } else {
        ws.showHint('Core 正在处理你的指令...')
        const result = await api.guidance(ws.currentProject!.id, text)
        await ws.refresh()
        if (result.requires_confirmation && result.resulting_goal_id) {
          await api.startGoal(result.resulting_goal_id)
          ws.showHint('新目标版本已开始执行；你仍可继续补充')
        }
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

  const handleTaskAction = useCallback(async (taskId: string, approved: boolean) => {
    try {
      await api.completeTask(taskId, approved)
      await ws.refresh()
      ws.showHint(approved ? '已批准' : '已拒绝')
    } catch (e) {
      ws.showHint((e as Error).message, true)
    }
  }, [ws])

  const handleQuickAction = useCallback((text: string) => {
    handleSend(text)
  }, [handleSend])

  const handleUpload = useCallback(async (file: File) => {
    try {
      ws.showHint('正在上传文件...')
      await api.uploadFile(file, ws.currentProject?.id)
      ws.showHint(`文件 ${file.name} 已上传`)
    } catch (e) {
      ws.showHint((e as Error).message, true)
    }
  }, [ws])

  const title = ws.currentProject
    ? ws.currentProject.name
    : '新任务'

  return (
    <div className="app">
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
        </header>
        <StageBar
          status={ws.status}
          progressNodes={progressNodes}
          liveActivity={ws.liveActivity}
          onQuickAction={handleQuickAction}
        />
        <MessageList
          messages={ws.messages}
          currentProjectId={ws.currentProject?.id || null}
          goalStatus={ws.status?.goal?.status || ws.currentProject?.status || null}
          onConfirm={handleConfirm}
          onTaskAction={handleTaskAction}
        />
        <div ref={messagesEndRef} />
        <Composer
          onSend={handleSend}
          onUpload={handleUpload}
          disabled={sending}
          hint={ws.hint}
          hintError={ws.hintError}
        />
      </main>
      <ArtifactPanel
        isOpen={artifactPanelOpen}
        onToggle={() => setArtifactPanelOpen(!artifactPanelOpen)}
        project={ws.currentProject}
        status={ws.status}
        messages={ws.messages}
        liveAction={ws.liveActivity.liveAction}
      />
    </div>
  )
}
