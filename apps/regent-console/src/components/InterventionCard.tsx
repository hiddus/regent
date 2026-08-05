import type { ReactNode } from 'react'

export type AskType = 'plan_approve' | 'permission' | 'ask_user' | 'recovery'

interface InterventionCardProps {
  askType: AskType
  title: string
  /** Override chip text (e.g. settled plan that no longer needs approval). */
  chip?: string
  badge?: string
  children: ReactNode
  compact?: boolean
  className?: string
}

const ASK_LABEL: Record<AskType, string> = {
  plan_approve: '确认计划',
  permission: '需要授权',
  ask_user: '需要你选',
  recovery: '建议继续',
}

/** Unified chrome for plan / permission / ask / recovery gates. */
export function InterventionCard({
  askType,
  title,
  chip,
  badge,
  children,
  compact = false,
  className = '',
}: InterventionCardProps) {
  return (
    <div
      className={[
        'intervention-card',
        `ask-${askType}`,
        compact ? 'is-compact' : '',
        className,
      ]
        .filter(Boolean)
        .join(' ')}
      data-ask-type={askType}
    >
      <div className="intervention-card-head">
        <span className="intervention-ask-chip">{chip ?? ASK_LABEL[askType]}</span>
        {badge ? <span className="intervention-badge">{badge}</span> : null}
        <h3 className="intervention-title">{title}</h3>
      </div>
      <div className="intervention-body">{children}</div>
    </div>
  )
}
