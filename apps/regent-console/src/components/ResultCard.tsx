interface ResultCardProps {
  summary: string
  openItems?: string[]
  previewUrl?: string | null
  stopReason?: string | null
  exitKind: 'COMPLETE' | 'STOP' | string
  onOpenPreview?: () => void
  onOpenReview?: () => void
}

export function ResultCard({
  summary,
  openItems = [],
  previewUrl,
  stopReason,
  exitKind,
  onOpenPreview,
  onOpenReview,
}: ResultCardProps) {
  if (exitKind === 'STOP') {
    return (
      <div className="result-card exit-stop">
        <div className="result-card-head">已停止</div>
        <p className="result-card-summary">
          {stopReason ? `原因：${stopReason}` : '本轮已结束并保留草稿，可继续或调整方向。'}
        </p>
      </div>
    )
  }

  return (
    <div className="result-card exit-complete">
      <div className="result-card-head">本轮已完成</div>
      <p className="result-card-summary">{summary || '交付已就绪。'}</p>
      {openItems.length > 0 && (
        <div className="result-card-open">
          <div className="result-card-section">未决项</div>
          <ul>
            {openItems.map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="result-card-actions">
        {(previewUrl || onOpenPreview) && (
          <button type="button" className="result-card-btn primary" onClick={onOpenPreview}>
            打开预览
          </button>
        )}
        {onOpenReview && (
          <button type="button" className="result-card-btn" onClick={onOpenReview}>
            查看审阅
          </button>
        )}
        {previewUrl && (
          <a className="result-card-btn" href={previewUrl} target="_blank" rel="noreferrer">
            新窗口
          </a>
        )}
      </div>
    </div>
  )
}
