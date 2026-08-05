/** Live activity helpers for distinguishing "running" vs "stuck". */

export type LiveConnectionState = 'connecting' | 'connected' | 'reconnecting' | 'idle'

export interface LiveAction {
  summary: string
  stage?: string | null
  detail?: string | null
  turn?: number | null
  tool?: string | null
  event_type?: string | null
  updated_at?: string | null
}

export interface LiveActivity {
  connection: LiveConnectionState
  /** Last genuine progress (new message / real status change). */
  lastProgressAt: number | null
  /** Last SSE heartbeat / any SSE event. */
  lastHeartbeatAt: number | null
  /** What Core is doing right now (from goal.metadata.live_action). */
  liveAction: LiveAction | null
}

const STALE_MS = 3 * 60 * 1000

export function formatRelativeTime(at: number | null, now = Date.now()): string {
  if (at == null) return '尚无进展'
  const sec = Math.max(0, Math.floor((now - at) / 1000))
  if (sec < 5) return '刚刚'
  if (sec < 60) return `${sec} 秒前`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min} 分钟前`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr} 小时前`
  return `${Math.floor(hr / 24)} 天前`
}

export function formatElapsed(at: number | null, now = Date.now()): string {
  if (at == null) return ''
  const sec = Math.max(0, Math.floor((now - at) / 1000))
  if (sec < 60) return `${sec}s`
  const min = Math.floor(sec / 60)
  const rem = sec % 60
  return `${min}m${rem.toString().padStart(2, '0')}s`
}

export function parseLiveAction(raw: unknown): LiveAction | null {
  if (!raw || typeof raw !== 'object') return null
  const obj = raw as Record<string, unknown>
  const summary = typeof obj.summary === 'string' ? obj.summary.trim() : ''
  if (!summary) return null
  return {
    summary,
    stage: typeof obj.stage === 'string' ? obj.stage : null,
    detail: typeof obj.detail === 'string' ? obj.detail : null,
    turn: typeof obj.turn === 'number' ? obj.turn : null,
    tool: typeof obj.tool === 'string' ? obj.tool : null,
    event_type: typeof obj.event_type === 'string' ? obj.event_type : null,
    updated_at: typeof obj.updated_at === 'string' ? obj.updated_at : null,
  }
}

export function deriveLiveLabel(
  activity: LiveActivity,
  goalStatus: string | null | undefined,
  now = Date.now(),
): { tone: 'live' | 'quiet' | 'stale' | 'offline' | 'idle'; text: string } {
  if (!goalStatus) {
    return { tone: 'idle', text: '等待开始' }
  }

  const terminal = ['ACHIEVED', 'CANCELLED', 'FAILED'].includes(goalStatus)
  if (terminal) {
    return { tone: 'idle', text: `上次进展 ${formatRelativeTime(activity.lastProgressAt, now)}` }
  }

  if (activity.connection === 'reconnecting' || activity.connection === 'connecting' || activity.connection === 'idle') {
    return {
      tone: 'offline',
      text: activity.connection === 'reconnecting'
        ? '实时连接中断，正在重连…'
        : '正在连接实时通道…',
    }
  }

  const waiting = ['WAITING_HUMAN', 'PAUSED', 'EXHAUSTED', 'BLOCKED'].includes(goalStatus)
  if (waiting) {
    return {
      tone: 'quiet',
      text: `等待你处理后继续 · 上次进展 ${formatRelativeTime(activity.lastProgressAt, now)}`,
    }
  }

  const actionAt = activity.liveAction?.updated_at
    ? Date.parse(activity.liveAction.updated_at)
    : NaN
  const actionFresh = !Number.isNaN(actionAt) && now - actionAt < 90_000
  if (activity.liveAction?.summary && actionFresh) {
    const elapsed = formatElapsed(actionAt, now)
    return {
      tone: 'live',
      text: elapsed
        ? `Core：${activity.liveAction.summary} · ${elapsed}`
        : `Core：${activity.liveAction.summary}`,
    }
  }

  const progressAge = activity.lastProgressAt == null ? Number.POSITIVE_INFINITY : now - activity.lastProgressAt
  const heartbeatAge = activity.lastHeartbeatAt == null ? Number.POSITIVE_INFINITY : now - activity.lastHeartbeatAt
  const linkAlive = activity.connection === 'connected' && heartbeatAge < 15_000

  if (progressAge < 45_000) {
    return {
      tone: 'live',
      text: `运行中 · 上次进展 ${formatRelativeTime(activity.lastProgressAt, now)}`,
    }
  }

  if (linkAlive && progressAge < STALE_MS) {
    return {
      tone: 'quiet',
      text: `仍在执行，暂无新消息 · 上次进展 ${formatRelativeTime(activity.lastProgressAt, now)}`,
    }
  }

  if (linkAlive) {
    return {
      tone: 'stale',
      text: `连接正常但长时间无进展 · 上次 ${formatRelativeTime(activity.lastProgressAt, now)}`,
    }
  }

  return {
    tone: 'offline',
    text: `实时状态未知 · 上次进展 ${formatRelativeTime(activity.lastProgressAt, now)}`,
  }
}

export function latestMessageTimestamp(messages: { created_at?: string }[]): number | null {
  let max = 0
  for (const m of messages) {
    if (!m.created_at) continue
    const t = Date.parse(m.created_at)
    if (!Number.isNaN(t) && t > max) max = t
  }
  return max > 0 ? max : null
}

/** Grace before treating ACTIVE+idle as "quiet / looks stuck". */
export const QUIET_ACTIVE_MS = 90_000

/**
 * ACTIVE but generation idle and no fresh live_action — the UI used to keep
 * saying "Core 正在执行 / Agent 规划中", which feels frozen.
 */
export function isQuietActive(input: {
  goalStatus?: string | null
  generationProgress?: string | null
  liveAction?: LiveAction | null
  lastProgressAt?: number | null
  now?: number
  graceMs?: number
}): boolean {
  if (input.goalStatus !== 'ACTIVE') return false
  const gen = String(input.generationProgress || '')
  // Explicit gen states already have their own StageBar copy / actions.
  if (gen && gen !== 'idle') return false

  const now = input.now ?? Date.now()
  const grace = input.graceMs ?? QUIET_ACTIVE_MS
  const actionAt = input.liveAction?.updated_at
    ? Date.parse(input.liveAction.updated_at)
    : NaN
  const actionFresh = !Number.isNaN(actionAt) && now - actionAt < 90_000
  if (input.liveAction?.summary && actionFresh) return false

  const progressAge =
    input.lastProgressAt == null
      ? Number.POSITIVE_INFINITY
      : now - input.lastProgressAt
  return progressAge >= grace
}

