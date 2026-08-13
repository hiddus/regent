import type { ProjectStatus } from './types'

export interface DashboardMetric {
  name: string
  value: string
  trend?: string
}

export interface ExplorationItem {
  title: string
  status: string
  evidence?: string
}

export interface OperatingDashboardModel {
  charter: {
    goal: string
    status: string
    horizon: string
  }
  metrics: DashboardMetric[]
  learnings: string[]
  explorations: ExplorationItem[]
  budget: {
    used: string
    remaining: string
  }
  risks: string[]
  decisions: string[]
}

const NOT_CONNECTED = '未接入'

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function text(value: unknown): string | undefined {
  if (typeof value === 'string' && value.trim()) return value.trim()
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return undefined
}

function textList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.map(item => text(item) || text(record(item).summary) || text(record(item).title))
    .filter((item): item is string => Boolean(item))
}

function first(...values: unknown[]): string | undefined {
  return values.map(text).find(Boolean)
}

function formatAmount(value: unknown, currency?: unknown): string | undefined {
  if (typeof value !== 'number' || !Number.isFinite(value)) return text(value)
  const unit = text(currency) || ''
  return unit ? `${unit} ${value.toLocaleString('zh-CN')}` : value.toLocaleString('zh-CN')
}

function deriveMetrics(meta: Record<string, unknown>): DashboardMetric[] {
  const source = meta.operating_metrics ?? meta.business_metrics ?? meta.metrics
  const entries = Array.isArray(source)
    ? source
    : Object.entries(record(source)).map(([name, value]) => ({ name, value }))

  return entries.flatMap(item => {
    const metric = record(item)
    const name = first(metric.name, metric.label, metric.key)
    const value = first(metric.display_value, metric.value, metric.current)
    if (!name || !value) return []
    return [{ name, value, trend: first(metric.trend, metric.change) }]
  }).slice(0, 4)
}

function deriveExplorations(meta: Record<string, unknown>): ExplorationItem[] {
  const source = meta.exploration_portfolio ?? meta.explorations ?? meta.hypotheses
  if (!Array.isArray(source)) return []
  return source.flatMap(item => {
    const entry = record(item)
    const title = first(entry.title, entry.name, entry.hypothesis, entry.summary)
    if (!title) return []
    return [{
      title,
      status: first(entry.status, entry.stage) || '状态未接入',
      evidence: first(entry.evidence, entry.latest_result, entry.learning),
    }]
  }).slice(0, 5)
}

export function deriveOperatingDashboard(status: ProjectStatus | null): OperatingDashboardModel {
  const goal = status?.goal
  const meta = record(goal?.metadata)
  const charter = record(meta.goal_charter ?? meta.charter)
  const budget = record(meta.operating_budget ?? meta.budget ?? meta.budget_summary)

  return {
    charter: {
      goal: first(charter.primary_goal, charter.goal, meta.primary_metric, goal?.original_input) || NOT_CONNECTED,
      status: first(charter.status, meta.goal_charter_status) || NOT_CONNECTED,
      horizon: first(charter.horizon, charter.period, meta.operating_horizon) || NOT_CONNECTED,
    },
    metrics: deriveMetrics(meta),
    learnings: textList(meta.cycle_learnings ?? meta.operating_learnings ?? meta.failure_lessons).slice(0, 4),
    explorations: deriveExplorations(meta),
    budget: {
      used: formatAmount(budget.used ?? budget.spent, budget.currency) || NOT_CONNECTED,
      remaining: formatAmount(budget.remaining, budget.currency) || NOT_CONNECTED,
    },
    risks: textList(meta.operating_risks ?? meta.risks ?? meta.open_risks).slice(0, 4),
    decisions: textList(meta.pending_decisions ?? meta.decision_items ?? meta.pending_approvals).slice(0, 4),
  }
}

export { NOT_CONNECTED }
