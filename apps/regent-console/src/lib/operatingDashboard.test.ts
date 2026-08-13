import assert from 'node:assert/strict'
import test from 'node:test'
import { deriveOperatingDashboard } from './operatingDashboard.ts'

test('does not invent business data when metadata is absent', () => {
  const model = deriveOperatingDashboard({
    goal: { id: 'g1', status: 'ACTIVE', original_input: '', metadata: {} },
    preview: null,
  })

  assert.equal(model.charter.goal, '未接入')
  assert.deepEqual(model.metrics, [])
  assert.deepEqual(model.learnings, [])
  assert.deepEqual(model.explorations, [])
  assert.equal(model.budget.used, '未接入')
  assert.deepEqual(model.risks, [])
  assert.deepEqual(model.decisions, [])
})

test('maps available status metadata into operating concepts', () => {
  const model = deriveOperatingDashboard({
    goal: {
      id: 'g1',
      status: 'ACTIVE',
      original_input: '提高试用转付费率',
      metadata: {
        goal_charter: { status: 'CONFIRMED', period: '6 周' },
        operating_metrics: [{ name: '试用转付费率', value: '12.4%', trend: '+1.2pp' }],
        cycle_learnings: ['新手引导第二步流失最高'],
        exploration_portfolio: [{ hypothesis: '缩短注册流程', stage: '验证中', evidence: '影子分析完成' }],
        operating_budget: { spent: 1200, remaining: 3800, currency: 'CNY' },
        open_risks: ['样本量不足'],
        pending_decisions: ['是否开放 10% 流量'],
      },
    },
    preview: null,
  })

  assert.equal(model.charter.goal, '提高试用转付费率')
  assert.equal(model.charter.status, 'CONFIRMED')
  assert.equal(model.metrics[0]?.trend, '+1.2pp')
  assert.equal(model.explorations[0]?.title, '缩短注册流程')
  assert.equal(model.budget.remaining, 'CNY 3,800')
  assert.deepEqual(model.decisions, ['是否开放 10% 流量'])
})
