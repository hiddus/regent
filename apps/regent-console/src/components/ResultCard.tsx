interface ResultArtifact {
  uri: string
  label?: string
  kind?: string
}

interface ResultCardProps {
  summary: string
  openItems?: string[]
  artifacts?: ResultArtifact[]
  previewUrl?: string | null
  stopReason?: string | null
  exitKind: 'COMPLETE' | 'STOP' | string
  onOpenPreview?: () => void
  onOpenReview?: () => void
  onOpenArtifacts?: () => void
}

export function ResultCard({
  summary,
  openItems = [],
  artifacts = [],
  previewUrl,
  stopReason,
  exitKind,
  onOpenPreview,
  onOpenReview,
  onOpenArtifacts,
}: ResultCardProps) {
  if (exitKind === 'STOP') {
    return (
      <div className="result-card exit-stop">
        <div className="result-card-head">已停止</div>
        <p className="result-card-summary">
          {stopReason ? `原因：${stopReason}` : '本轮已结束并保留草稿，可继续或调整方向。'}
        </p>
        <p className="result-card-hint">
          Goal 可多次修正：直接说新要求（CORRECT），或大幅改目标（MODIFY）后同会话续跑。
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
      {artifacts.length > 0 && (
        <div className="result-card-artifacts">
          <div className="result-card-section">产物</div>
          <ul>
            {artifacts.map((item, i) => (
              <li key={`${item.uri}-${i}`}>
                <code title={item.uri}>{item.label || item.uri}</code>
                {item.kind ? <span className="result-card-kind">{item.kind}</span> : null}
              </li>
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
        {(artifacts.length > 0 || onOpenArtifacts) && (
          <button type="button" className="result-card-btn" onClick={onOpenArtifacts || onOpenReview}>
            查看产物
          </button>
        )}
        {previewUrl && (
          <a className="result-card-btn" href={previewUrl} target="_blank" rel="noreferrer">
            新窗口
          </a>
        )}
      </div>
      <p className="result-card-hint">
        这不是终点：可继续用对话修正目标（样式、功能、运营动作），系统会在同项目上迭代。
      </p>
    </div>
  )
}
