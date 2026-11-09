import assert from 'node:assert/strict'
import test from 'node:test'
import { getStartReadiness } from './confirmationReadiness.ts'

test('only a feasible, clarified goal with no unknowns may start', () => {
  assert.equal(getStartReadiness({ feasibility_verdict: 'FEASIBLE', clarification_rounds: 2, unknowns: [] }).ready, true)
  assert.equal(getStartReadiness({ feasibility_verdict: 'FEASIBLE', clarification_rounds: 1, unknowns: [] }).ready, false)
  assert.equal(getStartReadiness({ feasibility_verdict: 'FEASIBLE', clarification_rounds: 2, unknowns: ['预算'] }).ready, false)
})

test('advisory unknowns do not trap a feasible goal in clarification', () => {
  const readiness = getStartReadiness({
    feasibility_verdict: 'FEASIBLE',
    clarification_rounds: 2,
    unknowns: [
      { question: 'WCAG 的最终等级', blocking: false },
      { question: '必须确认的预算', blocking: true },
    ],
  })
  assert.deepEqual(readiness.unknowns, ['必须确认的预算'])
  assert.equal(readiness.ready, false)
  assert.equal(getStartReadiness({
    feasibility_verdict: 'FEASIBLE',
    clarification_rounds: 2,
    unknowns: [{ question: '上线后再研究的偏好', blocking: false }],
  }).ready, true)
})

test('revision required and not feasible never pass the start gate', () => {
  assert.equal(getStartReadiness({ feasibility_verdict: 'REVISION_REQUIRED', clarification_rounds: 3 }).ready, false)
  assert.equal(getStartReadiness({ feasibility_verdict: 'NOT_FEASIBLE', clarification_rounds: 3 }).ready, false)
})

test('readiness accepts feasibility fields nested in plan payload', () => {
  const result = getStartReadiness({
    plan: { feasibility_verdict: 'FEASIBLE', clarification_rounds: 2, unknowns: [], feasibility_reasons: ['技术路径明确'] },
  })
  assert.equal(result.ready, true)
  assert.deepEqual(result.reasons, ['技术路径明确'])
})
