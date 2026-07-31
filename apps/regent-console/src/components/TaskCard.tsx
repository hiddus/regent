import { useEffect, useMemo, useState } from 'react'

interface ConfirmationEnvelope {
  action?: string
  summary?: string
  rules_applied?: string[]
  risk_level?: string
  rationale?: string
  on_allow?: string
  on_deny?: string
  timeout_seconds?: number
  default_on_timeout?: string
  safety_invariant?: boolean
  detail?: string | null
}

interface TaskCardProps {
  task: Record<string, unknown>
  resolved?: boolean
  onAction: (taskId: string, approved: boolean) => void
}

function riskLabel(level?: string): string {
  switch ((level || '').toLowerCase()) {
    case 'high':
      return '高风险'
    case 'low':
      return '低风险'
    default:
      return '中风险'
  }
}

function parseDueMs(dueAt?: string | null): number | null {
  if (!dueAt) return null
  const t = Date.parse(dueAt)
  return Number.isFinite(t) ? t : null
}

export function TaskCard({ task, resolved = false, onAction }: TaskCardProps) {
  const [done, setDone] = useState(resolved)
  const [now, setNow] = useState(() => Date.now())
  const [showDetail, setShowDetail] = useState(false)

  const taskId = String(task.id || task.human_task_id || '')
  const title = String(task.task_type || '人工任务')
  const prompt = String(task.prompt || '')
  const confirmation = (task.confirmation || {}) as ConfirmationEnvelope
  const dueAt = typeof task.due_at === 'string' ? task.due_at : null

  const timeoutSeconds = Number(confirmation.timeout_seconds ?? 0)
  const dueMs = useMemo(() => {
    const fromDue = parseDueMs(dueAt)
    if (fromDue != null) return fromDue
    if (timeoutSeconds > 0) return Date.now() + timeoutSeconds * 1000
    return null
  }, [dueAt, timeoutSeconds])

  useEffect(() => {
    if (resolved) setDone(true)
  }, [resolved])

  useEffect(() => {
    if (done || !dueMs || confirmation.safety_invariant || timeoutSeconds === 0) return
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [done, dueMs, confirmation.safety_invariant, timeoutSeconds])

  const remainingSec =
    dueMs != null ? Math.max(0, Math.ceil((dueMs - now) / 1000)) : null
  const summary = confirmation.summary || title
  const rules = confirmation.rules_applied || []
  const defaultOnTimeout = confirmation.default_on_timeout || 'deny'

  return (
    <div className={`task-card risk-${(confirmation.risk_level || 'medium').toLowerCase()}`}>
      <div className="task-risk-badge">{riskLabel(confirmation.risk_level)}</div>
      <h4>需要确认：{summary}</h4>
      <p className="task-meta-line">
        动作：{confirmation.action || title}
        {rules.length > 0 ? ` · 触发规则：${rules.join('、')}` : ''}
      </p>
      {confirmation.rationale ? (
        <p className="task-rationale">为什么：{confirmation.rationale}</p>
      ) : prompt ? (
        <p className="task-rationale">{prompt}</p>
      ) : null}
      <p className="task-consequences">
        允许后：{confirmation.on_allow || '继续执行'} · 拒绝后：
        {confirmation.on_deny || '停止自动推进'}
      </p>
      {remainingSec != null && !confirmation.safety_invariant && timeoutSeconds !== 0 ? (
        <p className="task-countdown">
          倒计时：{remainingSec}s 后默认 {defaultOnTimeout}
        </p>
      ) : confirmation.safety_invariant ? (
        <p className="task-countdown safety">安全不变量：不可超时自动放行</p>
      ) : null}

      {!done && taskId && (
        <div className="task-actions">
          <button
            className="task-btn approve"
            disabled={!!confirmation.safety_invariant}
            onClick={() => {
              setDone(true)
              onAction(taskId, true)
            }}
          >
            允许
          </button>
          {!confirmation.safety_invariant && (
            <button
              className="task-btn approve-always"
              title="本次会话对该类动作总是允许（首版等同允许）"
              onClick={() => {
                setDone(true)
                onAction(taskId, true)
              }}
            >
              总是允许
            </button>
          )}
          <button
            className="task-btn reject"
            onClick={() => {
              setDone(true)
              onAction(taskId, false)
            }}
          >
            拒绝
          </button>
        </div>
      )}
      {done && <p className="task-done">已处理</p>}
      {!done && !taskId && (
        <p className="task-done">
          任务卡缺少 task id — 请用下方快捷「批准」/「拒绝」，或输入「批准」触发恢复
        </p>
      )}

      {(confirmation.detail || prompt) && (
        <div className="task-detail-fold">
          <button
            type="button"
            className="task-detail-toggle"
            onClick={() => setShowDetail(v => !v)}
          >
            {showDetail ? '收起详情' : '展开详情（原始错误 / gap）'}
          </button>
          {showDetail && (
            <pre className="task-detail-body">{String(confirmation.detail || prompt)}</pre>
          )}
        </div>
      )}
    </div>
  )
}
