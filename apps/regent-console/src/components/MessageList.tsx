import { useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Message } from '../lib/types'
import { buildTimeline } from '../lib/progressNodes'
import { ConfirmationCard } from './ConfirmationCard'
import { TaskCard, type TaskActionOptions } from './TaskCard'
import { ProgressNodeCard } from './ProgressNodeCard'

interface MessageListProps {
  messages: Message[]
  currentProjectId?: string | null
  goalStatus?: string | null
  onConfirm: (projectId: string, goalId: string, hash: string) => void
  onTaskAction: (taskId: string, approved: boolean, opts?: TaskActionOptions) => void
}

function buildMovingGoals(items: Message[]): Set<string> {
  const set = new Set<string>()
  for (const m of items) {
    const t = m.message_type || ''
    if (
      t === 'GOAL_CONFIRMED' || t === 'GOAL_EXECUTION_QUEUED' ||
      t.startsWith('GOAL_EXECUTION_') || t === 'PREVIEW_READY' ||
      t === 'PREVIEW_DEPLOYMENT_SUCCEEDED'
    ) {
      const mid = m.metadata?.goal_id as string | undefined
      if (mid) set.add(mid)
      else set.add('*')
    }
  }
  return set
}

function buildResolvedTasks(items: Message[]): {
  byTaskId: Set<string>
  byGoalId: Set<string>
  anyApprove: boolean
} {
  const byTaskId = new Set<string>()
  const byGoalId = new Set<string>()
  let anyApprove = false
  for (const m of items) {
    if (m.message_type !== 'APPROVE_RESULT' && m.message_type !== 'REJECT_RESULT') continue
    const tid = String(m.metadata?.task_id || '')
    const gid = m.metadata?.goal_id as string | undefined
    if (tid) byTaskId.add(tid)
    if (gid) byGoalId.add(gid)
    if (m.message_type === 'APPROVE_RESULT') anyApprove = true
  }
  return { byTaskId, byGoalId, anyApprove }
}

function taskMetaResolved(taskMeta?: Record<string, unknown>) {
  if (!taskMeta) return false
  const status = String(taskMeta.status || '').toUpperCase()
  if (status === 'COMPLETED' || status === 'TIMED_OUT' || status === 'CANCELLED') return true
  const dueAt = typeof taskMeta.due_at === 'string' ? Date.parse(taskMeta.due_at) : NaN
  return Number.isFinite(dueAt) && dueAt <= Date.now()
}

function MessageItem({
  m,
  movingGoals,
  resolved,
  onConfirm,
  onTaskAction,
}: {
  m: Message
  movingGoals: Set<string>
  resolved: boolean
  onConfirm: (projectId: string, goalId: string, hash: string) => void
  onTaskAction: (taskId: string, approved: boolean, opts?: TaskActionOptions) => void
}) {
  const isConfirmation = m.message_type === 'APP_CONFIRMATION_REQUIRED' ||
    m.message_type === 'GOAL_UNDERSTANDING_READY'
  const goalId = m.metadata?.goal_id as string | undefined
  const isAwaiting = m.message_type === 'APP_CONFIRMATION_REQUIRED' &&
    !(goalId ? movingGoals.has(goalId) : movingGoals.has('*'))
  const isCorrection = m.message_type === 'CORRECTION_APPLIED'
  const roleClass = m.role.toLowerCase()
  const avatarLabel = m.role === 'USER' ? '你' : 'R'
  const metaLabel = m.role === 'USER' ? '你' : 'Regent'

  if (m.message_type === 'PREVIEW_READY' || m.message_type === 'PREVIEW_DEPLOYMENT_SUCCEEDED') {
    return null
  }

  const taskMeta = { ...(m.metadata || {}) } as Record<string, unknown>
  const taskId = String(taskMeta.id || taskMeta.human_task_id || '')
  const taskTypeUpper = String(taskMeta.task_type || '').toUpperCase()
  const confirmationAction = String(
    ((taskMeta.confirmation as Record<string, unknown> | undefined)?.action as string) || '',
  ).toLowerCase()
  const isDeliveryGapIntervene =
    taskTypeUpper === 'DELIVERY_GAP_INTERVENE' ||
    confirmationAction === 'delivery_gap_intervene' ||
    m.message_type === 'DELIVERY_SOFT_PAUSE' ||
    m.message_type === 'STALE_PROGRESS_NOTE'

  const isExhaustedHandoff =
    !isDeliveryGapIntervene &&
    (m.message_type === 'DELIVERY_GAP_EXHAUSTED' ||
      m.message_type === 'BUILD_DELIVERY_GAP_EXHAUSTED' ||
      m.message_type === 'RESEARCH_MORE_ADAPT_EXHAUSTED' ||
      (m.message_type === 'HUMAN_TASK_REQUIRED' &&
        (Boolean(taskMeta.confirmation) ||
          String(taskMeta.stage || '').includes('DELIVERY_GAP') ||
          String(taskMeta.stage || '').includes('NEEDS_HUMAN'))))

  if (isExhaustedHandoff && !taskMeta.confirmation) {
    taskMeta.confirmation = {
      action: String(taskMeta.task_type || 'DELIVERY_GAP_INTERVENE'),
      summary: '自动修复已用尽，需要你介入',
      rules_applied: ['stage:DELIVERY_GAP_EXHAUSTED'],
      risk_level: 'high',
      timeout_seconds: 300,
      default_on_timeout: 'deny',
      detail: m.content?.slice(0, 400) || null,
    }
  }
  if (isExhaustedHandoff && !taskMeta.task_type) {
    taskMeta.task_type = 'DELIVERY_GAP_INTERVENE'
  }
  if (isExhaustedHandoff && !taskMeta.prompt) {
    taskMeta.prompt = m.content
  }

  const showTaskCard =
    !isDeliveryGapIntervene &&
    (m.message_type === 'HUMAN_TASK_REQUIRED' || isExhaustedHandoff)

  return (
    <article className={`message ${roleClass}${showTaskCard ? ' message-task' : ''}`}>
      <div className="avatar">{avatarLabel}</div>
      <div className="body">
        <div className="meta">{metaLabel}</div>
        {!showTaskCard && (
          <div className="content">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
          </div>
        )}

        {isConfirmation && (
          <ConfirmationCard
            metadata={m.metadata}
            canConfirm={isAwaiting}
            onConfirm={() => {
              const pid = m.metadata?.app_project_id as string
              const gid = m.metadata?.goal_id as string
              const hash = (m.metadata?.goal_spec_hash as string) || ''
              onConfirm(pid, gid, hash)
            }}
          />
        )}

        {isCorrection && (
          <div className="correction-card">
            <span className="ct-label">
              修正 [{(m.metadata?.correction_target as string) || '其他'}]
            </span>
            <span className="ct-detail">{(m.metadata?.correction_detail as string) || ''}</span>
          </div>
        )}

        {showTaskCard && (
          <TaskCard
            task={taskMeta}
            resolved={resolved}
            compact={resolved}
            onAction={onTaskAction}
          />
        )}
      </div>
    </article>
  )
}

export function MessageList({ messages, goalStatus, onConfirm, onTaskAction }: MessageListProps) {
  const movingGoals = useMemo(() => buildMovingGoals(messages), [messages])
  const resolvedIndex = useMemo(() => buildResolvedTasks(messages), [messages])

  if (messages.length === 0) {
    return (
      <section className="messages">
        <div className="stream">
          <div className="empty">
            <h1>创建你的第一个 App</h1>
            <p>先描述产品想法。即使目标还很模糊，Core 也会从当前理解开始探索，并持续与你一起澄清。</p>
          </div>
        </div>
      </section>
    )
  }

  const timeline = buildTimeline(messages)
  const liveMode = !goalStatus || ['ACTIVE', 'WAITING_HUMAN', 'PAUSED', 'READY', 'BLOCKED', 'EXHAUSTED'].includes(goalStatus)

  return (
    <section className="messages">
      <div className="stream">
        {timeline.map((item, idx) => {
          if (item.kind === 'node') {
            return (
              <ProgressNodeCard
                key={`node-${item.node.key}-${idx}`}
                node={item.node}
                liveMode={liveMode}
              />
            )
          }
          const m = item.message
          const taskMeta = (m.metadata || {}) as Record<string, unknown>
          const taskId = String(taskMeta.id || taskMeta.human_task_id || '')
          const goalId = (taskMeta.goal_id as string | undefined) || (m.metadata?.goal_id as string | undefined)
          const resolved =
            taskMetaResolved(taskMeta) ||
            (taskId ? resolvedIndex.byTaskId.has(taskId) : false) ||
            (goalId ? resolvedIndex.byGoalId.has(goalId) : false) ||
            (!goalId && !taskId && resolvedIndex.anyApprove)

          return (
            <MessageItem
              key={item.message.id}
              m={item.message}
              movingGoals={movingGoals}
              resolved={resolved}
              onConfirm={onConfirm}
              onTaskAction={onTaskAction}
            />
          )
        })}
      </div>
    </section>
  )
}
