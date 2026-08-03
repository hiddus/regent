import { InterventionCard } from './InterventionCard'

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
  onConfirm: () => void
  onSelectOption?: (optionId: string, label: string) => void
}

function formatConstraints(constraints: Record<string, unknown>): string[] {
  const items: string[] = []
  for (const [key, value] of Object.entries(constraints)) {
    if (typeof value === 'string' && value.trim()) {
      const label = key
        .replace(/_/g, ' ')
        .replace(/([A-Z])/g, ' $1')
        .replace(/^./, s => s.toUpperCase())
        .trim()
      items.push(`${label}: ${value}`)
    } else if (typeof value === 'boolean' && value) {
      const label = key
        .replace(/_/g, ' ')
        .replace(/([A-Z])/g, ' $1')
        .replace(/^./, s => s.toUpperCase())
        .trim()
      items.push(label)
    }
  }
  return items
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

function asForkOptions(value: unknown): ForkOption[] {
  if (!Array.isArray(value)) return []
  const out: ForkOption[] = []
  for (const item of value) {
    if (!item || typeof item !== 'object') continue
    const row = item as Record<string, unknown>
    const id = String(row.id || '').trim()
    const label = String(row.label || '').trim()
    if (!id || !label) continue
    out.push({
      id,
      label,
      description: String(row.description || '').trim() || undefined,
      cost_hint: String(row.cost_hint || '').trim() || undefined,
    })
  }
  return out
}

export function ConfirmationCard({
  metadata,
  canConfirm,
  needsUserFork = false,
  onConfirm,
  onSelectOption,
}: ConfirmationCardProps) {
  const u = (metadata.understanding as Record<string, unknown>) || {}
  const plan = (metadata.plan as Record<string, unknown>) || {}
  const criteria = Object.entries(
    ((plan.success_criteria as Record<string, string>) ||
      (u.success_criteria as Record<string, string>) ||
      {}),
  )
  const constraintList = formatConstraints(
    (u.explicit_constraints as Record<string, unknown>) || {},
  )
  const steps = asStringList(plan.proposed_steps || u.proposed_steps)
  const unknowns = asStringList(plan.unknowns || u.unknowns)
  const forkOptions = asForkOptions(
    plan.fork_options || metadata.pending_fork_options || u.fork_options,
  )
  const showFork = needsUserFork && forkOptions.length >= 2

  return (
    <InterventionCard
      askType={showFork ? 'ask_user' : 'plan_approve'}
      title={showFork ? '需要你辅助决断的方向' : '请确认本轮计划'}
      className="confirm-card"
    >
      <dl className="facts">
        <dt>App 名称</dt>
        <dd>{(plan.app_name as string) || (u.app_name as string) || '待定'}</dd>
        <dt>目标用户</dt>
        <dd>{(plan.target_users as string) || (u.target_users as string) || '待定'}</dd>
        <dt>解决问题</dt>
        <dd>{(plan.problem as string) || (u.problem as string) || '待定'}</dd>
        <dt>首轮交付</dt>
        <dd>
          {(plan.first_deliverable as string) || (u.first_deliverable as string) || '待定'}
        </dd>

        {steps.length > 0 && (
          <>
            <dt>拟议步骤</dt>
            <dd>
              <ol className="plan-steps">
                {steps.map((step, i) => (
                  <li key={i}>{step}</li>
                ))}
              </ol>
            </dd>
          </>
        )}

        {criteria.length > 0 && (
          <>
            <dt>成功标准</dt>
            <dd>
              <ul className="criteria-list">
                {criteria.map(([k, v]) => (
                  <li key={k}>
                    <span className="criteria-check">✓</span>
                    <span>{v || k}</span>
                  </li>
                ))}
              </ul>
            </dd>
          </>
        )}

        {unknowns.length > 0 && (
          <>
            <dt>未知项</dt>
            <dd>
              <ul className="plan-unknowns">
                {unknowns.map((q, i) => (
                  <li key={i}>{q}</li>
                ))}
              </ul>
            </dd>
          </>
        )}

        {constraintList.length > 0 && (
          <>
            <dt>明确约束</dt>
            <dd>
              <ul className="constraint-tags">
                {constraintList.map((c, i) => (
                  <li key={i} className="constraint-tag">{c}</li>
                ))}
              </ul>
            </dd>
          </>
        )}
      </dl>

      {showFork ? (
        <div className="fork-options intervention-actions">
          <p className="fork-lead">请选择一个方向继续（2–4 项）：</p>
          {forkOptions.map(opt => (
            <button
              key={opt.id}
              type="button"
              className="fork-option-btn"
              onClick={() => onSelectOption?.(opt.id, opt.label)}
            >
              <span className="fork-option-label">{opt.label}</span>
              {opt.description ? (
                <span className="fork-option-desc">{opt.description}</span>
              ) : null}
              {opt.cost_hint ? (
                <span className="fork-option-cost">代价：{opt.cost_hint}</span>
              ) : null}
            </button>
          ))}
        </div>
      ) : canConfirm ? (
        <div className="intervention-actions">
          <button className="confirm-btn" onClick={onConfirm}>
            批准并继续
          </button>
        </div>
      ) : (
        <p className="confirm-note">
          Core 已按当前方案开始探索；目标会随证据和你的补充持续变清晰。
        </p>
      )}
    </InterventionCard>
  )
}
