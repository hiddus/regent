interface ConfirmationCardProps {
  metadata: Record<string, unknown>
  canConfirm: boolean
  onConfirm: () => void
}

function formatConstraints(constraints: Record<string, unknown>): string[] {
  const items: string[] = []
  for (const [key, value] of Object.entries(constraints)) {
    if (typeof value === 'string' && value.trim()) {
      // Convert snake_case/camelCase keys to readable labels
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

export function ConfirmationCard({ metadata, canConfirm, onConfirm }: ConfirmationCardProps) {
  const u = (metadata.understanding as Record<string, unknown>) || {}
  const criteria = Object.entries((u.success_criteria as Record<string, string>) || {})
  const constraintList = formatConstraints((u.explicit_constraints as Record<string, unknown>) || {})

  return (
    <div className="confirm-card">
      <h3>我理解的产品</h3>
      <dl className="facts">
        <dt>App 名称</dt><dd>{(u.app_name as string) || '待定'}</dd>
        <dt>目标用户</dt><dd>{(u.target_users as string) || '待定'}</dd>
        <dt>解决问题</dt><dd>{(u.problem as string) || '待定'}</dd>
        <dt>首轮交付</dt><dd>{(u.first_deliverable as string) || '待定'}</dd>

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
      {canConfirm ? (
        <>
          <button className="confirm-btn" onClick={onConfirm}>按当前理解开始</button>
        </>
      ) : (
        <p className="confirm-note">Core 已按当前理解开始探索；目标会随证据和你的补充持续变清晰。</p>
      )}
    </div>
  )
}
