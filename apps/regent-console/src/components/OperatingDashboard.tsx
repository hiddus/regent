import { useMemo, useState } from 'react'
import type { ProjectStatus } from '../lib/types'
import { deriveOperatingDashboard, NOT_CONNECTED } from '../lib/operatingDashboard'

interface OperatingDashboardProps {
  status: ProjectStatus | null
}

function Empty({ label = '数据未接入' }: { label?: string }) {
  return <span className="operating-empty">{label}</span>
}

export function OperatingDashboard({ status }: OperatingDashboardProps) {
  const [expanded, setExpanded] = useState(true)
  const model = useMemo(() => deriveOperatingDashboard(status), [status])

  if (!status?.goal) return null

  return (
    <section className="operating-dashboard" aria-label="经营驾驶舱">
      <button
        type="button"
        className="operating-dashboard-heading"
        aria-expanded={expanded}
        onClick={() => setExpanded(value => !value)}
      >
        <span>
          <strong>经营驾驶舱</strong>
          <small>目标、学习、探索与风险</small>
        </span>
        <span aria-hidden>{expanded ? '收起' : '展开'}</span>
      </button>
      {expanded && (
        <div className="operating-dashboard-body">
          <article className="operating-card operating-charter">
            <div className="operating-card-title">Goal Charter</div>
            <strong>{model.charter.goal}</strong>
            <dl>
              <div><dt>状态</dt><dd>{model.charter.status}</dd></div>
              <div><dt>周期</dt><dd>{model.charter.horizon}</dd></div>
            </dl>
          </article>

          <article className="operating-card">
            <div className="operating-card-title">经营指标</div>
            {model.metrics.length === 0 ? <Empty /> : (
              <ul className="operating-metrics">
                {model.metrics.map(metric => (
                  <li key={metric.name}>
                    <span>{metric.name}</span>
                    <strong>{metric.value}</strong>
                    {metric.trend && <small>{metric.trend}</small>}
                  </li>
                ))}
              </ul>
            )}
          </article>

          <article className="operating-card">
            <div className="operating-card-title">本周期学习</div>
            {model.learnings.length === 0 ? <Empty /> : (
              <ul>{model.learnings.map(item => <li key={item}>{item}</li>)}</ul>
            )}
          </article>

          <article className="operating-card operating-explorations">
            <div className="operating-card-title">探索组合</div>
            {model.explorations.length === 0 ? <Empty /> : (
              <ul>
                {model.explorations.map(item => (
                  <li key={`${item.title}-${item.status}`}>
                    <div><strong>{item.title}</strong><span>{item.status}</span></div>
                    {item.evidence && <small>{item.evidence}</small>}
                  </li>
                ))}
              </ul>
            )}
          </article>

          <article className="operating-card">
            <div className="operating-card-title">预算</div>
            <dl>
              <div><dt>已使用</dt><dd>{model.budget.used}</dd></div>
              <div><dt>剩余</dt><dd>{model.budget.remaining}</dd></div>
            </dl>
            {model.budget.used === NOT_CONNECTED && <Empty label="预算数据未接入" />}
          </article>

          <article className="operating-card">
            <div className="operating-card-title">风险与待决策</div>
            <div className="operating-alert-group">
              <strong>风险</strong>
              {model.risks.length === 0 ? <Empty /> : <ul>{model.risks.map(item => <li key={item}>{item}</li>)}</ul>}
            </div>
            <div className="operating-alert-group">
              <strong>待决策</strong>
              {model.decisions.length === 0 ? <Empty /> : <ul>{model.decisions.map(item => <li key={item}>{item}</li>)}</ul>}
            </div>
          </article>
        </div>
      )}
    </section>
  )
}
