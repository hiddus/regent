import type { ProjectStatus } from '../lib/types'

export function GoalReadiness({ status, onAction }: { status: ProjectStatus | null; onAction: (text: string) => void }) {
  const goal = status?.goal
  if (!goal || goal.status !== 'DRAFT') return null
  const meta = goal.metadata || {}
  const rounds = Number(meta.clarification_rounds || 0)
  const unknowns = Array.isArray(meta.unknowns) ? meta.unknowns.map(String) : []
  const verdict = String(meta.feasibility_verdict || 'REVISION_REQUIRED').toUpperCase()
  const reasons = Array.isArray(meta.feasibility_reasons) ? meta.feasibility_reasons.map(String) : []
  const locked = meta.execution_boundary_locked === true
  const clarified = rounds >= 2 && unknowns.length === 0
  const feasible = verdict === 'FEASIBLE'
  const active = !clarified ? 0 : !feasible ? 1 : !locked ? 2 : 3
  const steps = [
    ['明确项目边界', `${rounds}/2 轮确认${unknowns.length ? ` · ${unknowns.length} 项待定` : ''}`, clarified],
    ['验证可行性', feasible ? '分析通过' : verdict === 'NOT_FEASIBLE' ? '当前不可行' : '需要补充或调整', feasible],
    ['锁定目标', locked ? '目标版本已锁定' : '等待你的最终确认', locked],
    ['正式执行', '锁定后才会使用执行预算', false],
  ] as const
  const prompt = !clarified
    ? (unknowns[0] ? `请继续向我确认这个边界：${unknowns[0]}` : '请继续下一轮边界确认，并列出仍需我决定的问题')
    : !feasible ? '请基于当前边界完成可行性分析，并说明价值、风险与落地条件'
    : '请给出最终目标摘要，供我确认后锁定执行'
  return <section className="readiness" aria-label="项目启动检查">
    <div className="readiness-head"><div><strong>启动前检查</strong><span>先确认值得做、能够做，再正式执行</span></div><b className={`verdict verdict-${verdict.toLowerCase()}`}>{feasible ? '可行' : verdict === 'NOT_FEASIBLE' ? '不可行' : '分析中'}</b></div>
    <ol className="readiness-steps">{steps.map((step, index) => <li key={step[0]} className={`${step[2] ? 'done' : ''} ${index === active ? 'current' : ''}`}><i>{step[2] ? '✓' : index + 1}</i><div><strong>{step[0]}</strong><small>{step[1]}</small></div></li>)}</ol>
    {(unknowns.length > 0 || reasons.length > 0) && <div className="readiness-detail"><strong>{unknowns.length ? '待确认边界' : '可行性依据'}</strong><span>{(unknowns.length ? unknowns : reasons).slice(0, 3).join('；')}</span></div>}
    {!locked && <button type="button" className="readiness-action" onClick={() => onAction(prompt)}>继续当前步骤 →</button>}
  </section>
}
