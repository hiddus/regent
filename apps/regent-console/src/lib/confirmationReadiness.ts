export type FeasibilityVerdict = 'FEASIBLE' | 'REVISION_REQUIRED' | 'NOT_FEASIBLE'

export interface StartReadiness {
  verdict: FeasibilityVerdict
  rounds: number
  unknowns: string[]
  reasons: string[]
  ready: boolean
}

function asList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.map(item => typeof item === 'string' ? item.trim() : String((item as { question?: unknown })?.question || '').trim()).filter(Boolean)
}

export function getStartReadiness(metadata: Record<string, unknown>): StartReadiness {
  const understanding = (metadata.understanding as Record<string, unknown>) || {}
  const plan = (metadata.plan as Record<string, unknown>) || {}
  const value = (key: string) => metadata[key] ?? plan[key] ?? understanding[key]
  const raw = String(value('feasibility_verdict') || 'REVISION_REQUIRED').toUpperCase()
  const verdict: FeasibilityVerdict = raw === 'FEASIBLE' || raw === 'NOT_FEASIBLE' ? raw : 'REVISION_REQUIRED'
  const rounds = Number(value('clarification_rounds') || 0)
  const unknowns = asList(value('unknowns'))
  const reasons = asList(value('feasibility_reasons'))
  return { verdict, rounds, unknowns, reasons, ready: verdict === 'FEASIBLE' && rounds >= 2 && unknowns.length === 0 }
}
