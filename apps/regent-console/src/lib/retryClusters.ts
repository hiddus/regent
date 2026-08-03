import type { Message } from './types'

const RETRY_SURFACE_TYPES = new Set([
  'GENERATION_ATTEMPT_FAILED',
  'DELIVERY_GAP_CAPABILITY_ESCALATED',
  'PROJECT_AGENT_SESSION_RESUMED',
  'STALE_PROGRESS_NOTE',
])

export function isRetrySurfaceMessage(m: Message): boolean {
  if (m.role !== 'ASSISTANT' && m.role !== 'EVENT') return false
  if (RETRY_SURFACE_TYPES.has(m.message_type)) return true
  return (
    m.role === 'ASSISTANT' &&
    m.message_type.endsWith('_FAILED') &&
    m.message_type !== 'GOAL_FAILED'
  )
}

/** Stable key for "same failure" across retries (ignore attempt counters). */
export function failureFingerprint(m: Message): string {
  const meta = m.metadata || {}
  const reasons = meta.gap_reasons
  if (Array.isArray(reasons) && reasons.length > 0) {
    return reasons.map(String).map(s => s.trim()).filter(Boolean).slice(0, 8).sort().join('|')
  }
  const code = String(meta.error_code || meta.gap_kind || '').trim()
  const body = String(m.content || '')
  // Prefer structured failure tokens so paired ASSISTANT lines still merge.
  const tokens = body.match(
    /(?:goal-first-deliverable|TEST_FAILED|SMOKE_FAILED|BUILD_FAILED|GATE_FAILED)[^；;，,\n)）]*/gi,
  )
  if (tokens && tokens.length > 0) {
    return `${code}::${[...new Set(tokens.map(t => t.trim()))].sort().join('|')}`
  }
  const normalized = body
    .replace(/attempt\s*\d+/gi, '')
    .replace(/lesson=[^\s.]+/gi, '')
    .replace(/session=[^\s,）)]+/gi, '')
    .replace(/epoch=\d+/gi, '')
    .replace(/第\s*\d+\s*次/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 180)
  return `${code}::${normalized}`
}

export interface RetryCluster {
  kind: 'retry_cluster'
  fingerprint: string
  messages: Message[]
  /** Distinct retry rounds (GENERATION_ATTEMPT_FAILED or escalations count). */
  attemptCount: number
  latest: Message
}

export type DialogItem =
  | { kind: 'message'; message: Message }
  | { kind: 'node'; node: import('./progressNodes').ProgressNode }
  | RetryCluster

/**
 * Merge consecutive same-cause retry / escalation bubbles into one cluster.
 * Backend still stores every attempt (audit); UI stops repeating the same wall of text.
 */
export function collapseRetryClusters<T extends { kind: string; message?: Message }>(
  items: T[],
): Array<T | RetryCluster> {
  const out: Array<T | RetryCluster> = []
  let i = 0
  while (i < items.length) {
    const item = items[i]
    if (item.kind !== 'message' || !item.message || !isRetrySurfaceMessage(item.message)) {
      out.push(item)
      i += 1
      continue
    }
    const fp = failureFingerprint(item.message)
    const group: Message[] = [item.message]
    let j = i + 1
    while (j < items.length) {
      const next = items[j]
      if (next.kind !== 'message' || !next.message || !isRetrySurfaceMessage(next.message)) break
      if (failureFingerprint(next.message) !== fp) break
      group.push(next.message)
      j += 1
    }
    if (group.length === 1) {
      out.push(item)
    } else {
      const attemptCount = Math.max(
        1,
        group.filter(m =>
          m.message_type === 'GENERATION_ATTEMPT_FAILED' ||
          m.message_type === 'DELIVERY_GAP_CAPABILITY_ESCALATED' ||
          m.message_type === 'PROJECT_AGENT_SESSION_RESUMED',
        ).length,
        // Pair (failed + escalated) in one round → count rounds by failed or escalated alone
      )
      // Prefer counting "rounds": number of GENERATION_ATTEMPT_FAILED, else group length.
      const failedRounds = group.filter(m => m.message_type === 'GENERATION_ATTEMPT_FAILED').length
      const escalatedRounds = group.filter(m =>
        m.message_type === 'DELIVERY_GAP_CAPABILITY_ESCALATED' ||
        m.message_type === 'PROJECT_AGENT_SESSION_RESUMED',
      ).length
      const rounds = Math.max(failedRounds, escalatedRounds, 1)
      out.push({
        kind: 'retry_cluster',
        fingerprint: fp,
        messages: group,
        attemptCount: rounds,
        latest: group[group.length - 1],
      })
    }
    i = j
  }
  return out
}

export function summarizeGapReasons(m: Message): string {
  const meta = m.metadata || {}
  const reasons = meta.gap_reasons
  if (Array.isArray(reasons) && reasons.length > 0) {
    return reasons.map(String).slice(0, 3).join('；')
  }
  const content = String(m.content || '').replace(/\s+/g, ' ').trim()
  // Prefer the parenthetical / after colon chunk when present.
  const m1 = content.match(/[（(]([^）)]+)[）)]/)
  if (m1) return m1[1].slice(0, 160)
  if (content.includes('：')) return content.split('：').slice(1).join('：').slice(0, 160)
  return content.slice(0, 160)
}
