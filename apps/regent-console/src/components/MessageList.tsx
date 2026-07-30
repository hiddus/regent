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

  const taskMeta = m.metadata || {}
  const taskId = String(taskMeta.id || taskMeta.human_task_id || '')

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

        {m.message_type === 'HUMAN_TASK_REQUIRED' && (
          <TaskCard
            task={taskMeta}
            resolved={taskAlreadyResolved(messages, taskId)}
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
