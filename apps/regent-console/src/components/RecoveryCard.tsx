import type { DiagnosticDelivery, DiagnosticRecommendation } from '../lib/types'

interface RecoveryCardProps {
  delivery: DiagnosticDelivery
  summary?: string
  projectId?: string
  onAction?: (action: string, label: string) => void
  onInspect?: () => void
}

export function RecoveryCard({
  delivery,
  summary,
  onAction,
  onInspect,
}: RecoveryCardProps) {
  const findings = (delivery.findings || []).slice(0, 3)
  const recs = (delivery.recommendations || []).slice(0, 3)
  const budget = delivery.budget || {}
  const preview = delivery.preview || { state: 'UNAVAILABLE', reason: '' }
  const fileCount =
    delivery.artifacts?.find(a => a.kind === 'source_snapshot')?.file_count ?? null

  const handle = (rec: DiagnosticRecommendation) => {
    const action = String(rec.action || rec.id || '')
    if (action === 'INSPECT_CURRENT_RESULT') {
      onInspect?.()
      return
    }
    onAction?.(action, rec.label)
  }

  return (
    <div className="recovery-card">
      <div className="recovery-card-badge">未验证草稿 · 非正式交付</div>
      <p className="recovery-card-summary">
        {summary || delivery.summary || '本轮已暂停，当前成果已保存。'}
      </p>

      <ul className="recovery-card-facts">
        {delivery.terminal_reason && (
          <li>原因：{delivery.terminal_reason}</li>
        )}
        {fileCount != null && <li>已保存源码文件：{fileCount} 个</li>}
        {(budget.turns_used != null || budget.tokens_used != null) && (
          <li>
            预算：
            {budget.turns_used != null
              ? `${budget.turns_used}${budget.turns_limit != null ? `/${budget.turns_limit}` : ''} 轮`
              : ''}
            {budget.turns_used != null && budget.tokens_used != null ? ' · ' : ''}
            {budget.tokens_used != null
              ? `${Number(budget.tokens_used).toLocaleString()} tokens`
              : ''}
          </li>
        )}
        <li>
          预览：
          {preview.state === 'VERIFIED'
            ? '上一验证版可用'
            : preview.state === 'DRAFT'
              ? '未验证草稿可尝试启动'
              : preview.reason || '本轮未生成可运行 Preview'}
        </li>
      </ul>

      {findings.length > 0 && (
        <div className="recovery-card-findings">
          <div className="recovery-card-section-title">阻断问题</div>
          <ol>
            {findings.map((f, i) => (
              <li key={`${f.code}-${i}`}>
                <strong>{f.code}</strong>
                {f.title ? ` — ${f.title}` : ''}
              </li>
            ))}
          </ol>
        </div>
      )}

      <div className="recovery-card-actions">
        {recs.map(rec => (
          <button
            key={rec.id || rec.action}
            type="button"
            className="recovery-card-btn"
            onClick={() => handle(rec)}
          >
            {rec.label}
          </button>
        ))}
      </div>
      <p className="recovery-card-hint">无需点「允许」或「总是允许」— 直接选下一步即可。</p>
    </div>
  )
}
