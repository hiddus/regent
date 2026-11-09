import type { Message } from './types'

export type EventPresentation =
  | 'conversation'
  | 'decision'
  | 'stage'
  | 'current_action'
  | 'artifact'
  | 'retry'
  | 'silent'

const DECISIONS = new Set([
  'APP_CONFIRMATION_REQUIRED', 'GOAL_PLAN_PROPOSED', 'HUMAN_TASK_REQUIRED',
  'APPROVE_RESULT', 'REJECT_RESULT', 'FORK_SELECTED', 'CORRECTION_APPLIED',
  'DELIVERY_GAP_EXHAUSTED', 'BUILD_DELIVERY_GAP_EXHAUSTED',
  'RESEARCH_MORE_ADAPT_EXHAUSTED', 'DIAGNOSTIC_DELIVERY_READY',
  'PLAN_REVIEW',
])

const ARTIFACTS = new Set([
  'PREVIEW_READY', 'PREVIEW_DEPLOYMENT_SUCCEEDED', 'WORKSPACE_SNAPSHOT_READY',
  'GOAL_ACHIEVED',
])

const RETRIES = new Set([
  'GENERATION_ATTEMPT_FAILED', 'DELIVERY_GAP_CAPABILITY_ESCALATED',
  'PROJECT_AGENT_SESSION_RESUMED', 'STALE_PROGRESS_NOTE',
])

const CURRENT_ACTIONS = new Set([
  'DELIVERY_BATCH_STARTED', 'GENERATION_RUN_REQUESTED', 'APP_BUILD_REQUESTED',
  'DISCOVERY_ROUND_REQUESTED', 'REQUIREMENT_REQUESTED',
])

const SILENT = new Set([
  'TRANSCRIPT_PERSIST_FAILED', 'OUTBOX_DISPATCHED', 'HEARTBEAT', 'PING',
])

/**
 * Product contract for the real-time surface. The conversation contains user
 * intent, decisions and durable conclusions—not an append-only infrastructure log.
 */
export function classifyEventForPresentation(message: Message): EventPresentation {
  const type = String(message.message_type || '').toUpperCase()
  if (message.role === 'USER') return 'conversation'
  if (DECISIONS.has(type)) return 'decision'
  if (ARTIFACTS.has(type)) return 'artifact'
  if (RETRIES.has(type) || (type.endsWith('_FAILED') && type !== 'GOAL_FAILED')) return 'retry'
  if (CURRENT_ACTIONS.has(type)) return 'current_action'
  if (SILENT.has(type)) return 'silent'
  if (message.role === 'EVENT') return 'stage'
  if (message.role === 'ASSISTANT') return 'conversation'
  return 'silent'
}

export interface PresentationPolicy {
  surface: 'bubble' | 'decision_card' | 'stage_node' | 'live_slot' | 'artifact_panel' | 'retry_cluster' | 'hidden'
  durable: boolean
  replacePrevious: boolean
}

export function presentationPolicy(kind: EventPresentation): PresentationPolicy {
  switch (kind) {
    case 'conversation': return { surface: 'bubble', durable: true, replacePrevious: false }
    case 'decision': return { surface: 'decision_card', durable: true, replacePrevious: false }
    case 'stage': return { surface: 'stage_node', durable: true, replacePrevious: true }
    case 'current_action': return { surface: 'live_slot', durable: false, replacePrevious: true }
    case 'artifact': return { surface: 'artifact_panel', durable: true, replacePrevious: false }
    case 'retry': return { surface: 'retry_cluster', durable: true, replacePrevious: true }
    case 'silent': return { surface: 'hidden', durable: false, replacePrevious: true }
  }
}

export type ConnectionTruth = 'live' | 'reconnecting' | 'unknown'

export function connectionTruth(input: {
  connection: 'connecting' | 'connected' | 'reconnecting' | 'idle'
  lastHeartbeatAt: number | null
  now?: number
}): ConnectionTruth {
  if (input.connection === 'reconnecting' || input.connection === 'connecting') return 'reconnecting'
  const age = input.lastHeartbeatAt == null ? Number.POSITIVE_INFINITY : (input.now ?? Date.now()) - input.lastHeartbeatAt
  if (input.connection === 'connected' && age < 15_000) return 'live'
  return 'unknown'
}

export const CONNECTION_COPY: Record<ConnectionTruth, string> = {
  live: '实时连接正常',
  reconnecting: '实时连接已中断，正在重连；执行是否继续暂未确认',
  unknown: '实时状态未知；显示的是最后一次已确认进展',
}
