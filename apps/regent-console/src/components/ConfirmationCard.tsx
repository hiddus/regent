import { useState } from 'react'
import { InterventionCard } from './InterventionCard'
import { getStartReadiness, type FeasibilityVerdict } from '../lib/confirmationReadiness'

interface ForkOption {
  id: string
  label: string
  description?: string
  cost_hint?: string
}

interface ConfirmationCardProps {
  metadata: Record<string, unknown>
  canConfirm: boolean
  needsUserFork?: boolean
  /** Sticky dock: start collapsed so the card does not dominate the viewport. */
  docked?: boolean
  onConfirm: () => void
  onSelectOption?: (optionId: string, label: string) => void
}

function asStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value
    .map(v => {
      if (typeof v === 'string') return v.trim()
      if (v && typeof v === 'object' && 'question' in v) {
        return String((v as { question?: unknown }).question || '').trim()
      }
      return String(v || '').trim()
    })
    .filter(Boolean)
}

function formatConstraints(constraints: Record<string, unknown>): string[] {
  return Object.entries(constraints).flatMap(([key, value]) => {
    const label = key.replace(/_/g, ' ').replace(/([A-Z])/g, ' $1').trim()
    if (typeof value === 'string' && value.trim()) return [`${label}: ${value}`]
    if (typeof value === 'boolean' && value) return [label]
    return []
  })
}

function asForkOptions(value: unknown): ForkOption[] {
  if (!Array.isArray(value)) return []
  return value.flatMap(item => {
    if (!item || typeof item !== 'object') return []
    const row = item as Record<string, unknown>
    const id = String(row.id || '').trim()
    const label = String(row.label || '').trim()
    if (!id || !label) return []
    return [{
      id,
      label,
      description: String(row.description || '').trim() || undefined,
      cost_hint: String(row.cost_hint || '').trim() || undefined,
    }]
  })
}

const VERDICT_COPY: Record<FeasibilityVerdict, { label: string; lead: string }> = {
  FEASIBLE: { label: '可行', lead: '可行性分析已通过' },
  REVISION_REQUIRED: { label: '待修订', lead: '还需继续明确边界，暂不能开始执行' },
  NOT_FEASIBLE: { label: '不可行', lead: '当前方案不可行，不会启动执行' },
}

export function ConfirmationCard({
  metadata,
  canConfirm,
  needsUserFork = false,
  docked = false,
  onConfirm,
  onSelectOption,
}: ConfirmationCardProps) {
  const u = (metadata.understanding as Record<string, unknown>) || {}
  const plan = (metadata.plan as Record<string, unknown>) || {}
  const readiness = getStartReadiness(metadata)
  const criteria = Object.entries(
    ((plan.success_criteria as Record<string, string>) ||
      (u.success_criteria as Record<string, string>) || {}),
  )
  const constraints = formatConstraints((u.explicit_constraints as Record<string, unknown>) || {})
  const steps = asStringList(plan.proposed_steps || u.proposed_steps)
  const forkOptions = asForkOptions(plan.fork_options || metadata.pending_fork_options || u.fork_options)
  const showFork = needsUserFork && forkOptions.length >= 2
  const mayStart = canConfirm && readiness.ready && !showFork
  const settled = !showFork && !canConfirm
  const appName = (plan.app_name as string) || (u.app_name as string) || '待确定'
  const firstDeliverable = (plan.first_deliverable as string) || (u.first_deliverable as string) || ''
  const hasDetails = Boolean(
    plan.problem || u.problem || plan.target_users || u.target_users || steps.length ||
    criteria.length || readiness.unknowns.length || constraints.length,
  )
  const [expanded, setExpanded] = useState(!docked && canConfirm)

  return (
    <InterventionCard
      askType={showFork ? 'ask_user' : 'plan_approve'}
      chip={settled ? '执行中方案' : `可行性：${VERDICT_COPY[readiness.verdict].label}`}
      title={showFork ? '请选择方案方向' : mayStart ? '确认并锁定目标' : settled ? '已锁定的目标' : '目标尚未达到启动条件'}
      badge={settled ? '边跑边修' : undefined}
      compact={docked && !expanded && !showFork}
      className={['confirm-card', settled ? 'is-settled' : '', docked ? 'is-docked' : '', expanded ? 'is-expanded' : 'is-collapsed'].filter(Boolean).join(' ')}
    >
      <p className="confirm-summary">{[appName, firstDeliverable].filter(Boolean).join(' · ')}</p>
      {!settled && <p className="confirm-note">{VERDICT_COPY[readiness.verdict].lead}。已完成 {readiness.rounds}/2 轮边界确认。</p>}

      {hasDetails && (
        <button type="button" className="confirm-expand-btn" onClick={() => setExpanded(v => !v)}>
          {expanded ? '收起详情' : '展开目标与可行性详情'}
        </button>
      )}

      {expanded && (
        <dl className="facts">
          <dt>产品名称</dt><dd>{appName}</dd>
          <dt>目标用户</dt><dd>{String(plan.target_users || u.target_users || '待确定')}</dd>
          <dt>解决问题</dt><dd>{String(plan.problem || u.problem || '待确定')}</dd>
          <dt>首轮交付</dt><dd>{firstDeliverable || '待确定'}</dd>
          {steps.length > 0 && <><dt>拟议步骤</dt><dd><ol className="plan-steps">{steps.map((step, i) => <li key={i}>{step}</li>)}</ol></dd></>}
          {criteria.length > 0 && <><dt>验收标准</dt><dd><ul className="criteria-list">{criteria.map(([k, v]) => <li key={k}><span className="criteria-check">✓</span><span>{v || k}</span></li>)}</ul></dd></>}
          {readiness.reasons.length > 0 && <><dt>可行性依据</dt><dd><ul className="plan-unknowns">{readiness.reasons.map((reason, i) => <li key={i}>{reason}</li>)}</ul></dd></>}
          {readiness.unknowns.length > 0 && <><dt>待确认边界</dt><dd><ul className="plan-unknowns">{readiness.unknowns.map((q, i) => <li key={i}>{q}</li>)}</ul></dd></>}
          {constraints.length > 0 && <><dt>明确约束</dt><dd><ul className="constraint-tags">{constraints.map((c, i) => <li key={i} className="constraint-tag">{c}</li>)}</ul></dd></>}
        </dl>
      )}

      {showFork ? (
        <div className="fork-options intervention-actions">
          <p className="fork-lead">选择方向只用于继续分析，不会立即开工：</p>
          {forkOptions.map(opt => (
            <button key={opt.id} type="button" className="fork-option-btn" onClick={() => onSelectOption?.(opt.id, opt.label)}>
              <span className="fork-option-label">{opt.label}</span>
              {expanded && opt.description && <span className="fork-option-desc">{opt.description}</span>}
              {expanded && opt.cost_hint && <span className="fork-option-cost">成本提示：{opt.cost_hint}</span>}
            </button>
          ))}
        </div>
      ) : mayStart ? (
        <div className="intervention-actions">
          <button className="confirm-btn" onClick={onConfirm}>确认边界、锁定目标并开始</button>
          <p className="confirm-note">锁定后按当前边界执行；过程中可继续修正，重大边界变化将重新确认。</p>
        </div>
      ) : settled ? (
        docked ? null : <p className="confirm-note">目标已锁定。执行中可继续补充和修正。</p>
      ) : (
        <p className="confirm-note">请继续回复待确认边界；只有可行性通过且关键未知项清零后，才会出现启动按钮。</p>
      )}
    </InterventionCard>
  )
}
