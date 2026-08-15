interface GateMessage {
  id: string
  ordinal: number
  message_type: string
  metadata?: Record<string, unknown>
}

const GATE_TYPES = new Set(['APP_CONFIRMATION_REQUIRED', 'GOAL_PLAN_PROPOSED'])

export function selectActiveConfirmationId(
  messages: GateMessage[],
  goalMetadata: Record<string, unknown>,
): string | null {
  if (goalMetadata.execution_boundary_locked === true || goalMetadata.confirmation_state === 'USED') {
    return null
  }
  const currentHash = String(goalMetadata.goal_spec_hash || goalMetadata.spec_hash || '')
  const currentVersion = Number(goalMetadata.latest_goal_spec_version || 0)
  const currentGoalId = String(goalMetadata.goal_id || '')
  const candidates = messages.filter(message => {
    if (!GATE_TYPES.has(message.message_type)) return false
    const metadata = message.metadata || {}
    if (String(metadata.gate_status || '').toUpperCase() === 'SUPERSEDED') return false
    if (currentGoalId && metadata.goal_id && String(metadata.goal_id) !== currentGoalId) return false
    if (currentHash && String(metadata.goal_spec_hash || '') !== currentHash) return false
    if (currentVersion && metadata.goal_spec_version && Number(metadata.goal_spec_version) !== currentVersion) return false
    return true
  })
  candidates.sort((left, right) => right.ordinal - left.ordinal)
  return candidates[0]?.id || null
}

export function isStaleConfirmation(
  message: GateMessage,
  activeConfirmationId: string | null,
  goalMetadata: Record<string, unknown>,
): boolean {
  if (!GATE_TYPES.has(message.message_type)) return false
  if (goalMetadata.execution_boundary_locked === true) return false
  return message.id !== activeConfirmationId
}
