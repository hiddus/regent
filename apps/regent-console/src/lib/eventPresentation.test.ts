import assert from 'node:assert/strict'
import test from 'node:test'
import { classifyEventForPresentation, connectionTruth, presentationPolicy } from './eventPresentation.ts'
import type { Message } from './types.ts'

const message = (role: string, message_type: string): Message => ({
  id: `${role}-${message_type}`, conversation_id: 'c', ordinal: 1, role,
  message_type, content: '', metadata: {}, created_by: 'test', created_at: '',
})

test('decisions remain in conversation while artifacts use the read-only panel', () => {
  assert.equal(classifyEventForPresentation(message('ASSISTANT', 'HUMAN_TASK_REQUIRED')), 'decision')
  assert.equal(presentationPolicy('decision').surface, 'decision_card')
  assert.equal(classifyEventForPresentation(message('EVENT', 'PREVIEW_READY')), 'artifact')
  assert.equal(presentationPolicy('artifact').surface, 'artifact_panel')
})

test('transient actions replace the live slot and repeated failures aggregate', () => {
  assert.equal(presentationPolicy(classifyEventForPresentation(message('EVENT', 'DELIVERY_BATCH_STARTED'))).surface, 'live_slot')
  assert.equal(presentationPolicy(classifyEventForPresentation(message('ASSISTANT', 'GENERATION_ATTEMPT_FAILED'))).surface, 'retry_cluster')
  assert.equal(presentationPolicy('retry').replacePrevious, true)
})

test('unknown infrastructure events do not become chat bubbles', () => {
  assert.equal(classifyEventForPresentation(message('SYSTEM', 'HEARTBEAT')), 'silent')
  assert.equal(classifyEventForPresentation(message('EVENT', 'NEW_STAGE_EVENT')), 'stage')
})

test('connection state never claims execution stopped or continued without evidence', () => {
  const now = 100_000
  assert.equal(connectionTruth({ connection: 'connected', lastHeartbeatAt: now - 1_000, now }), 'live')
  assert.equal(connectionTruth({ connection: 'reconnecting', lastHeartbeatAt: now - 1_000, now }), 'reconnecting')
  assert.equal(connectionTruth({ connection: 'connected', lastHeartbeatAt: now - 20_000, now }), 'unknown')
})
