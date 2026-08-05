import { InterventionCard } from './InterventionCard'
import { formatRelativeTime } from '../lib/liveActivity'

interface QuietExecutionCardProps {
  lastProgressAt?: number | null
  now?: number
  onContinue: () => void
  onStop: () => void
}

/** Slim dock when Goal is ACTIVE but nothing is progressing. */
export function QuietExecutionCard({
  lastProgressAt = null,
  now = Date.now(),
  onContinue,
  onStop,
}: QuietExecutionCardProps) {
  const when = formatRelativeTime(lastProgressAt, now)
  return (
    <InterventionCard
      askType="recovery"
      chip="暂无进展"
      title="执行似乎停住了"
      compact
      className="quiet-execution-card"
    >
      <span className="quiet-one-liner">上次 {when}</span>
      <div className="intervention-actions is-inline">
        <button type="button" className="confirm-btn" onClick={onContinue}>
          继续
        </button>
        <button type="button" className="qa-btn" onClick={onStop}>
          停止
        </button>
      </div>
    </InterventionCard>
  )
}
