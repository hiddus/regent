import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Message } from '../lib/types'
import { buildTimeline } from '../lib/progressNodes'
import { ConfirmationCard } from './ConfirmationCard'
import { TaskCard } from './TaskCard'
import { ProgressNodeCard } from './ProgressNodeCard'

interface MessageListProps {
  messages: Message[]
  currentProjectId?: string | null
  goalStatus?: string | null
  onConfirm: (projectId: string, goalId: string, hash: string) => void
  onTaskAction: (taskId: string, approved: boolean) => void
}

function goalAlreadyMoving(items: Message[], goalId?: string) {
  if (!goalId) return false
  return items.some(m => {
    const mid = m.metadata?.goal_id as string | undefined
    if (mid && mid !== goalId) return false
    const t = m.message_type || ''
    return t === 'GOAL_CONFIRMED' || t === 'GOAL_EXECUTION_QUEUED' ||
      t.startsWith('GOAL_EXECUTION_') || t === 'PREVIEW_READY' ||
      t === 'PREVIEW_DEPLOYMENT_SUCCEEDED'
  })
}

function taskAlreadyResolved(items: Message[], taskId?: string) {
  if (!taskId) return false
  return items.some(m => {
    if (m.message_type !== 'APPROVE_RESULT' && m.message_type !== 'REJECT_RESULT') return false
    return String(m.metadata?.task_id || '') === taskId
  })
}

function approveAlreadyDone(items: Message[], goalId?: string, taskId?: string) {
  if (taskAlreadyResolved(items, taskId)) return true
  if (!goalId && !taskId) {
    return items.some(m => m.message_type === 'APPROVE_RESULT')
  }
  return items.some(m => {
    if (m.message_type !== 'APPROVE_RESULT') return false
    const mid = m.metadata?.goal_id as string | undefined
    const tid = String(m.metadata?.task_id || '')
    if (taskId && tid && tid === taskId) return true
    if (goalId && mid && mid === goalId) return true
    if (!goalId && !tid) return true
    return Boolean(taskId) && tid === taskId
  })
}

function MessageItem({ m, messages, onConfirm, onTaskAction }: {
  m: Message
  messages: Message[]
  onConfirm: (projectId: string, goalId: string, hash: string) => void
  onTaskAction: (taskId: string, approved: boolean) => void
}) {
  const isConfirmation = m.message_type === 'APP_CONFIRMATION_REQUIRED' ||
    m.message_type === 'GOAL_UNDERSTANDING_READY'
  const isAwaiting = m.message_type === 'APP_CONFIRMATION_REQUIRED' &&
    !goalAlreadyMoving(messages, m.metadata?.goal_id as string)
  const isCorrection = m.message_type === 'CORRECTION_APPLIED'
  const roleClass = m.role.toLowerCase()
  const avatarLabel = m.role === 'USER' ? '你' : 'R'
  const metaLabel = m.role === 'USER' ? '你' : 'Regent'

  // Skip preview messages — they are shown in the artifact panel
  if (m.message_type === 'PREVIEW_READY' || m.message_type === 'PREVIEW_DEPLOYMENT_SUCCEEDED') {
    return null
  }

  const taskMeta = { ...(m.metadata || {}) } as Record<string, unknown>
  const taskId = String(taskMeta.id || taskMeta.human_task_id || '')
  const isExhaustedHandoff =
    m.message_type === 'DELIVERY_GAP_EXHAUSTED' ||
    m.message_type === 'BUILD_DELIVERY_GAP_EXHAUSTED' ||
    m.message_type === 'RESEARCH_MORE_ADAPT_EXHAUSTED' ||
    (m.message_type === 'HUMAN_TASK_REQUIRED' &&
      (Boolean(taskMeta.confirmation) ||
        String(taskMeta.stage || '').includes('DELIVERY_GAP') ||
        String(taskMeta.stage || '').includes('NEEDS_HUMAN')))
  // Historical exhausted messages may lack confirmation/task id — still show operable card.
  if (isExhaustedHandoff && !taskMeta.confirmation) {
    taskMeta.confirmation = {
      action: String(taskMeta.task_type || 'DELIVERY_GAP_INTERVENE'),
      summary: '自动修复已用尽，需要你介入',
      rules_applied: ['stage:DELIVERY_GAP_EXHAUSTED'],
      risk_level: 'high',
      rationale: '能力阶梯已穷尽；批准后将重新规划生成，拒绝则保持等待你的下一步指示。',
      on_allow: '重置恢复计数并继续生成',
      on_deny: '停止自动推进，等待你补充方向',
      timeout_seconds: 300,
      default_on_timeout: 'deny',
      detail: m.content?.slice(0, 800) || null,
    }
  }
  if (isExhaustedHandoff && !taskMeta.task_type) {
    taskMeta.task_type = 'DELIVERY_GAP_INTERVENE'
  }
  if (isExhaustedHandoff && !taskMeta.prompt) {
    taskMeta.prompt = m.content
  }
  const showTaskCard =
    m.message_type === 'HUMAN_TASK_REQUIRED' || isExhaustedHandoff

  return (
    <article className={`message ${roleClass}`}>
      <div className="avatar">{avatarLabel}</div>
      <div className="body">
        <div className="meta">{metaLabel}</div>
        <div className="content">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
        </div>

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
            resolved={approveAlreadyDone(
              messages,
              (taskMeta.goal_id as string | undefined) || (m.metadata?.goal_id as string | undefined),
              taskId || undefined,
            )}
            onAction={onTaskAction}
          />
        )}
      </div>
    </article>
  )
}

export function MessageList({ messages, goalStatus, onConfirm, onTaskAction }: MessageListProps) {
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
          return (
            <MessageItem
              key={item.message.id}
              m={item.message}
              messages={messages}
              onConfirm={onConfirm}
              onTaskAction={onTaskAction}
            />
          )
        })}
      </div>
    </section>
  )
}
