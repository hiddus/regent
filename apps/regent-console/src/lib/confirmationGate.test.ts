import test from 'node:test'
import assert from 'node:assert/strict'
import { isStaleConfirmation, selectActiveConfirmationId } from './confirmationGate.ts'

const gate = (id: string, ordinal: number, version: number, hash: string) => ({
  id,
  ordinal,
  message_type: 'APP_CONFIRMATION_REQUIRED',
  metadata: { goal_id: 'g1', goal_spec_version: version, goal_spec_hash: hash },
})

test('only the confirmation matching the current spec is active', () => {
  const messages = [gate('v4', 4, 4, 'old'), gate('v5', 6, 5, 'current')]
  const metadata = { goal_id: 'g1', latest_goal_spec_version: 5, goal_spec_hash: 'current' }
  assert.equal(selectActiveConfirmationId(messages, metadata), 'v5')
  assert.equal(isStaleConfirmation(messages[0], 'v5', metadata), true)
})

test('locked goals expose no active confirmation', () => {
  const messages = [gate('v5', 6, 5, 'current')]
  assert.equal(selectActiveConfirmationId(messages, {
    latest_goal_spec_version: 5,
    goal_spec_hash: 'current',
    execution_boundary_locked: true,
  }), null)
})

test('replayed duplicate gates select only the newest one', () => {
  const messages = [gate('first', 6, 5, 'current'), gate('second', 8, 5, 'current')]
  assert.equal(selectActiveConfirmationId(messages, {
    latest_goal_spec_version: 5,
    goal_spec_hash: 'current',
  }), 'second')
})
