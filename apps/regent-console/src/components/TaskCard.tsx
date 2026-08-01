import { useEffect, useMemo, useState } from 'react'
import type { HandoffOption } from '../lib/types'

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
  handoff_options?: HandoffOption[]
  primary_failure_code?: string
  budget_summary?: string
  diagnostic_artifact_uri?: string
  recovery_options?: HandoffOption[]
}

/** Extra decision metadata carried alongside the plain approve/deny signal. */
export interface TaskActionOptions {
  /** CD-3.5: "总是允许" — persist this action to session/goal decision policy. */
  always?: boolean
  /** CD-3.2: id of the chosen handoff option (e.g. narrow_scope / keep_trying / stop). */
  optionId?: string
  /** Human-readable reason to attach to the completion response. */
  reason?: string
}

interface TaskCardProps {
  task: Record<string, unknown>
  resolved?: boolean
  /** Compact one-liner for superseded / historical cards — keeps the stream short. */
  compact?: boolean
  onAction: (taskId: string, approved: boolean, opts?: TaskActionOptions) => void
}

function isHandoffOptionArray(value: unknown): value is HandoffOption[] {
  return (
    Array.isArray(value) &&
    value.every(
      item =>
        item &&
        typeof item === 'object' &&
        typeof (item as Record<string, unknown>).id === 'string' &&
        typeof (item as Record<string, unknown>).label === 'string',
    )
  )
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

/** Map machine action / stage → what the human is authorizing. */
function describePermit(task: Record<string, unknown>, confirmation: ConfirmationEnvelope) {
  const action = String(confirmation.action || task.task_type || '').toLowerCase()
  const stage = String(task.stage || '')
  const rules = confirmation.rules_applied || []
  const stageRule = rules.find(r => r.startsWith('stage:')) || ''
  const joined = `${action} ${stage} ${stageRule} ${confirmation.summary || ''}`.toLowerCase()

  if (joined.includes('exhausted') || joined.includes('ladder')) {
    return {
      who: 'Regent（本目标）',
      what: '在补充方向后继续自动修复',
      why: '自动修复轮次已用尽，不是危险操作——需要你给下一步方向',
    }
  }
  if (joined.includes('goal_intent')) {
    return {
      who: 'Regent（本目标）',
      what: '继续自动修复并重新生成交付',
      why: '当前产出可能未命中目标意图',
    }
  }
  if (joined.includes('resume_blocked')) {
    return {
      who: 'Regent（本目标）',
      what: '再次尝试重开交付恢复',
      why: '上次批准后未能自动继续',
    }
  }
  if (action.includes('release') || joined.includes('release')) {
    return {
      who: 'Regent（本目标）',
      what: '发布 / 部署预览',
      why: confirmation.summary || '需要你确认后才能发布',
    }
  }
  if (action.includes('quality') || joined.includes('quality')) {
    return {
      who: 'Regent（本目标）',
      what: '通过质量门并继续后续步骤',
      why: confirmation.summary || '需要你确认质量结果',
    }
  }
  if (action.includes('goal_confirm') || joined.includes('goal_confirm')) {
    return {
      who: 'Regent（本目标）',
      what: '按当前理解开始执行',
      why: confirmation.summary || '确认目标理解无误',
    }
  }
  return {
    who: 'Regent（本目标）',
    what: confirmation.summary || '继续执行被拦截的下一步',
    why: '需要你确认后才能继续',
  }
}

export function TaskCard({
  task,
  resolved = false,
  compact = false,
  onAction,
}: TaskCardProps) {
  const [done, setDone] = useState(resolved)
  const [now, setNow] = useState(() => Date.now())
  const [showDetail, setShowDetail] = useState(false)
  const [showMore, setShowMore] = useState(false)

  const taskId = String(task.id || task.human_task_id || '')
  const confirmation = (task.confirmation || {}) as ConfirmationEnvelope
  const dueAt = typeof task.due_at === 'string' ? task.due_at : null
  const permit = describePermit(task, confirmation)
  const handoffOptions = useMemo(() => {
    const fromConfirmation = confirmation.handoff_options
    const fromRecovery = confirmation.recovery_options
    const fromMetadata = task.handoff_options
    const candidate = isHandoffOptionArray(fromConfirmation)
      ? fromConfirmation
      : isHandoffOptionArray(fromRecovery)
        ? fromRecovery
        : isHandoffOptionArray(fromMetadata)
          ? (fromMetadata as HandoffOption[])
          : []
    return candidate
  }, [confirmation.handoff_options, confirmation.recovery_options, task.handoff_options])

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
    if (done || compact || !dueMs || confirmation.safety_invariant || timeoutSeconds === 0) return
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [done, compact, dueMs, confirmation.safety_invariant, timeoutSeconds])

  const taskStatus = String(task.status || '').toUpperCase()
  const terminalStatus =
    taskStatus === 'COMPLETED' ||
    taskStatus === 'TIMED_OUT' ||
    taskStatus === 'CANCELLED'
  const remainingSec =
    dueMs != null ? Math.max(0, Math.ceil((dueMs - now) / 1000)) : null
  const defaultOnTimeout = confirmation.default_on_timeout || 'deny'
  const timedOutClient =
    remainingSec === 0 &&
    dueMs != null &&
    !confirmation.safety_invariant &&
    timeoutSeconds !== 0 &&
    defaultOnTimeout === 'deny'
  const forceCompact = compact || resolved || terminalStatus || timedOutClient
  const effectivelyDone = done || forceCompact
  const detailText = String(confirmation.detail || task.prompt || '').trim()

  if (forceCompact) {
    const isTimedOut =
      timedOutClient ||
      taskStatus === 'TIMED_OUT' ||
      (remainingSec === 0 && dueMs != null && taskStatus !== 'COMPLETED' && taskStatus !== 'CANCELLED')
    const label = isTimedOut ? '已过期' : '已处理'
    return (
      <div className={`task-card task-card-compact risk-${(confirmation.risk_level || 'medium').toLowerCase()}`}>
        <span className="task-compact-badge">{label}</span>
        <span className="task-compact-text">{permit.what}</span>
        {isTimedOut && taskId ? (
          <button
            type="button"
            className="task-btn approve task-continue-btn"
            onClick={() => {
              setDone(true)
              onAction(taskId, true, { reason: 'continue_after_timeout' })
            }}
          >
            继续此目标
          </button>
        ) : null}
      </div>
    )
  }

  return (
    <div className={`task-card risk-${(confirmation.risk_level || 'medium').toLowerCase()}`}>
      <div className="task-card-head">
        <span className="task-risk-badge">{riskLabel(confirmation.risk_level)}</span>
        {remainingSec != null && !confirmation.safety_invariant && timeoutSeconds !== 0 ? (
          <span className="task-countdown-inline">
            {defaultOnTimeout === 'deny'
              ? `${remainingSec}s 后默认拒绝`
              : defaultOnTimeout === 'allow'
                ? `${remainingSec}s 后默认允许`
                : '等待你的方向（超时不会自动拒绝）'}
          </span>
        ) : null}
      </div>

      <h4 className="task-permit-title">
        {(confirmation.risk_level || '').toLowerCase() === 'high'
          ? '需要你授权'
          : '需要你的方向'}
      </h4>
      <dl className="task-permit">
        <div>
          <dt>授权给</dt>
          <dd>{permit.who}</dd>
        </div>
        <div>
          <dt>允许做</dt>
          <dd>{permit.what}</dd>
        </div>
        <div>
          <dt>原因</dt>
          <dd>{permit.why}</dd>
        </div>
      </dl>

      {(confirmation.primary_failure_code ||
        confirmation.budget_summary ||
        confirmation.diagnostic_artifact_uri) && (
        <div className="task-failure-panel">
          {confirmation.primary_failure_code ? (
            <p className="task-primary-failure">
              失败码：<code>{confirmation.primary_failure_code}</code>
            </p>
          ) : null}
          {confirmation.budget_summary ? (
            <p className="task-budget-summary">预算：{confirmation.budget_summary}</p>
          ) : null}
          {confirmation.diagnostic_artifact_uri ? (
            <p className="task-diagnostic-link">
              诊断产物：<code>{confirmation.diagnostic_artifact_uri}</code>
            </p>
          ) : null}
        </div>
      )}

      {!effectivelyDone && taskId && (
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
              title="本目标内同类请求不再询问"
              onClick={() => {
                setDone(true)
                onAction(taskId, true, { always: true })
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

      {!effectivelyDone && !taskId && (
        <p className="task-done">请在输入框发送「批准」或「拒绝」</p>
      )}

      {effectivelyDone && <p className="task-done">已处理</p>}

      {!effectivelyDone && taskId && handoffOptions.length > 0 && (
        <div className="task-more-fold">
          <button
            type="button"
            className="task-detail-toggle"
            onClick={() => setShowMore(v => !v)}
          >
            {showMore ? '收起其他选项' : '其他选项'}
          </button>
          {showMore && (
            <div className="task-handoff-options">
              {handoffOptions.map(option => (
                <button
                  key={option.id}
                  type="button"
                  className={`task-btn option option-${option.id}`}
                  onClick={() => {
                    setDone(true)
                    onAction(taskId, option.id !== 'stop', {
                      optionId: option.id,
                      reason: option.label,
                    })
                  }}
                >
                  <span className="option-label">{option.label}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {detailText && (
        <div className="task-detail-fold">
          <button
            type="button"
            className="task-detail-toggle"
            onClick={() => setShowDetail(v => !v)}
          >
            {showDetail ? '收起技术细节' : '技术细节'}
          </button>
          {showDetail && <pre className="task-detail-body">{detailText}</pre>}
        </div>
      )}
    </div>
  )
}
