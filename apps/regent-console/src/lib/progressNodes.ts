/**
 * Map conversation EVENT messages → user-facing progress nodes.
 * Product-friendly language: show stage nodes + status + conclusion,
 * not every internal log line.
 */

import type { Message } from './types'

export type NodeStatus = 'pending' | 'running' | 'done' | 'failed' | 'waiting'

export type NodeKey =
  | 'understand'
  | 'discover'
  | 'require'
  | 'capability'
  | 'generate'
  | 'build'
  | 'preview'
  | 'verify'
  | 'milestone'
  | 'human'
  | 'outcome'

export interface ProgressNode {
  key: NodeKey
  title: string
  status: NodeStatus
  /** One-line conclusion for the user (product language) */
  conclusion: string
  /** Supporting detail (optional, shown in expanded view) */
  detail?: string
  updatedAt?: string
  /** Message ids collapsed into this node */
  messageIds: string[]
  /** User-friendly highlights (preview URL, deliverable name, etc.) */
  highlights?: Record<string, string>
}

interface StageDef {
  key: NodeKey
  title: string
  /** event type → how it affects node status */
  events: Record<string, { status: NodeStatus; conclusion?: string }>
}

const STAGES: StageDef[] = [
  {
    key: 'understand',
    title: '理解你的想法',
    events: {
      APP_CONFIRMATION_REQUIRED: { status: 'waiting', conclusion: '已初步理解你的产品想法' },
      GOAL_UNDERSTANDING_READY: { status: 'running', conclusion: '正在探索你的产品方向' },
      GOAL_CONFIRMED: { status: 'done', conclusion: '产品方向已确认' },
      CORRECTION_APPLIED: { status: 'running', conclusion: '已记录你的补充，继续完善方案' },
    },
  },
  {
    key: 'discover',
    title: '市场调研',
    events: {
      GOAL_EXECUTION_QUEUED: { status: 'running', conclusion: '已开始为你工作' },
      DISCOVERY_ROUND_CREATED: { status: 'running', conclusion: '正在进行市场调研' },
      DISCOVERY_COMPLETED: { status: 'done', conclusion: '市场调研已完成' },
    },
  },
  {
    key: 'require',
    title: '方案规划',
    events: {
      REQUIREMENT_REQUESTED: { status: 'running', conclusion: '正在规划产品方案' },
      REQUIREMENT_VALIDATED: { status: 'done', conclusion: '产品方案已规划完成' },
    },
  },
  {
    key: 'capability',
    title: '技术准备',
    events: {
      CAPABILITY_RESOLUTION_REQUESTED: { status: 'running', conclusion: '正在准备技术方案' },
      CAPABILITY_RESOLUTION_PLANNED: { status: 'done', conclusion: '技术方案已就绪' },
      ORGANIZATION_SELECTED: { status: 'done', conclusion: '执行环境已就绪' },
      ADAPTIVE_ORGANIZATION_PROPOSED: { status: 'waiting', conclusion: '有新的技术建议需要你确认' },
      REORGANIZATION_TRIGGERED: { status: 'running', conclusion: '正在优化技术配置' },
    },
  },
  {
    key: 'generate',
    title: '应用生成',
    events: {
      GENERATION_RUN_REQUESTED: { status: 'running', conclusion: '正在生成你的应用' },
      DELIVERY_BATCH_PLANNED: { status: 'running', conclusion: '正在分步生成应用' },
      DELIVERY_BATCH_STARTED: { status: 'running', conclusion: '正在生成中...' },
      DELIVERY_BATCH_VERIFIED: { status: 'running', conclusion: '当前部分已生成完成' },
      DELIVERY_BATCH_MERGED: { status: 'running', conclusion: '已合并到主工程' },
      DELIVERY_BATCH_REJECTED: { status: 'failed', conclusion: '部分生成未通过，正在重新生成' },
      DELIVERY_GLOBAL_VERIFY_FAILED: { status: 'failed', conclusion: '生成过程遇到问题，正在尝试修复' },
      DELIVERY_BATCHES_COMPLETED: { status: 'done', conclusion: '应用代码已全部生成完成' },
      WORKSPACE_SNAPSHOT_READY: { status: 'done', conclusion: '应用已打包完成' },
    },
  },
  {
    key: 'build',
    title: '质量检查',
    events: {
      APP_BUILD_REQUESTED: { status: 'running', conclusion: '正在进行质量检查' },
      APP_BUILD_PASSED: { status: 'done', conclusion: '质量检查已通过' },
      FAILURE_COMPLIANCE: { status: 'failed', conclusion: '质量检查未通过，正在修复' },
    },
  },
  {
    key: 'preview',
    title: '预览准备',
    events: {
      PREVIEW_READY: { status: 'done', conclusion: '预览环境已就绪，可以体验' },
      PREVIEW_DEPLOYMENT_SUCCEEDED: { status: 'done', conclusion: '预览已发布，可以查看' },
      PREVIEW_SUCCEEDED: { status: 'done', conclusion: '预览链路已完成' },
    },
  },
  {
    key: 'verify',
    title: '最终验证',
    events: {
      ITERATION_DECISION: { status: 'running', conclusion: '正在进行最终验证' },
      GATE_INSUFFICIENT_EVIDENCE: { status: 'waiting', conclusion: '需要更多运行数据来完成验证' },
      GATE_INSUFFICIENT_REORGANIZED: { status: 'running', conclusion: '正在补充验证数据' },
      QUALITY_SELF_VERIFIED: { status: 'done', conclusion: '所有验证已通过' },
      VERIFICATION_REQUIRED: { status: 'waiting', conclusion: '需要你确认是否满意当前结果' },
      ITERATION_REVISE_STARTED: { status: 'running', conclusion: '正在根据你的反馈优化' },
    },
  },
  {
    key: 'milestone',
    title: '阶段完成',
    events: {
      MILESTONE_ATTAINED: { status: 'done', conclusion: '当前阶段已达成' },
      MILESTONE_ADVANCE_BLOCKED: { status: 'waiting', conclusion: '阶段推进遇到阻碍，正在处理' },
    },
  },
  {
    key: 'human',
    title: '需要你确认',
    events: {
      HUMAN_TASK_REQUIRED: { status: 'waiting', conclusion: '有一个步骤需要你确认' },
    },
  },
  {
    key: 'outcome',
    title: '完成',
    events: {
      GOAL_ACHIEVED: { status: 'done', conclusion: '你的 App 已准备就绪' },
      GOAL_EXHAUSTED: { status: 'failed', conclusion: '执行已暂停，需要你补充信息' },
      GOAL_EXECUTION_STAGE_HALTED: { status: 'failed', conclusion: '执行已停止，请查看详情' },
    },
  },
]

const TYPE_INDEX = new Map<string, { stage: StageDef; status: NodeStatus; conclusion?: string }>()
for (const stage of STAGES) {
  for (const [type, effect] of Object.entries(stage.events)) {
    TYPE_INDEX.set(type, { stage, status: effect.status, conclusion: effect.conclusion })
  }
}

const STATUS_RANK: Record<NodeStatus, number> = {
  pending: 0,
  running: 1,
  waiting: 2,
  done: 3,
  failed: 4,
}

export function isProgressEvent(m: Message): boolean {
  if (m.role === 'EVENT') return true
  return TYPE_INDEX.has(m.message_type) && m.message_type !== 'APP_CONFIRMATION_REQUIRED'
    && m.message_type !== 'HUMAN_TASK_REQUIRED'
    && m.message_type !== 'CORRECTION_APPLIED'
}

/** Interactive / chat messages that remain as bubbles */
export function isChatSurfaceMessage(m: Message): boolean {
  if (m.role === 'USER' || m.role === 'ASSISTANT') {
    return true
  }
  const t = m.message_type
  return (
    t === 'APP_CONFIRMATION_REQUIRED' ||
    t === 'HUMAN_TASK_REQUIRED' ||
    t === 'CORRECTION_APPLIED' ||
    t === 'PREVIEW_READY' ||
    t === 'PREVIEW_DEPLOYMENT_SUCCEEDED'
  )
}

function summarizeContent(content: string, max = 120): string {
  const one = content.replace(/\s+/g, ' ').trim()
  if (one.length <= max) return one
  return one.slice(0, max - 1) + '…'
}

/**
 * Extract only user-friendly highlights from message metadata.
 * Internal fields like decision, verdict, gate_status are NOT exposed.
 */
function extractHighlights(m: Message): Record<string, string> {
  const meta = m.metadata || {}
  const highlights: Record<string, string> = {}
  // Only expose user-meaningful information
  if (meta.endpoint) highlights['预览地址'] = String(meta.endpoint)
  if (meta.milestone_title) highlights['阶段'] = String(meta.milestone_title)
  if (meta.next_key) highlights['下一步'] = String(meta.next_key)
  const dv = meta.delivery_verification as Record<string, unknown> | undefined
  if (dv?.summary) highlights['验证结果'] = String(dv.summary)
  return highlights
}

/**
 * Collapse EVENT stream into ordered progress nodes.
 * Only nodes that have received at least one event are returned.
 */
export function buildProgressNodes(messages: Message[]): ProgressNode[] {
  const byKey = new Map<NodeKey, ProgressNode>()
  const order: NodeKey[] = []

  for (const m of messages) {
    const hit = TYPE_INDEX.get(m.message_type)
    if (!hit) {
      if (m.role !== 'EVENT') continue
      continue
    }
    const { stage, status } = hit
    let node = byKey.get(stage.key)
    if (!node) {
      node = {
        key: stage.key,
        title: stage.title,
        status: 'pending',
        conclusion: '',
        messageIds: [],
        highlights: {},
      }
      byKey.set(stage.key, node)
      order.push(stage.key)
    }

    const nextRank = STATUS_RANK[status]
    const curRank = STATUS_RANK[node.status]
    if (status === 'failed' || status === 'waiting' || nextRank >= curRank || status === 'running') {
      if (!(node.status === 'failed' && status !== 'failed')) {
        node.status = status
      }
    }
    if (status === 'running' && (node.status === 'done' || node.status === 'waiting')) {
      node.status = 'running'
    }

    const defaultConclusion = hit.conclusion || stage.title
    node.conclusion = summarizeContent(m.content) || defaultConclusion
    node.detail = hit.conclusion && hit.conclusion !== node.conclusion ? hit.conclusion : undefined
    node.updatedAt = m.created_at
    node.messageIds.push(m.id)
    Object.assign(node.highlights || {}, extractHighlights(m))
  }

  return order.map(k => byKey.get(k)!).filter(Boolean)
}

export const NODE_STATUS_LABEL: Record<NodeStatus, string> = {
  pending: '待开始',
  running: '进行中',
  waiting: '待你处理',
  done: '已完成',
  failed: '未通过',
}

/** Timeline items for MessageList: chat bubbles interleaved with node cards */
export type TimelineItem =
  | { kind: 'message'; message: Message }
  | { kind: 'node'; node: ProgressNode }

/**
 * Build a user-facing timeline:
 * - USER / confirmation / task / correction stay as messages
 * - EVENT progress collapses into nodes inserted at first occurrence
 * - Preview events are shown in the artifact panel, not inline
 */
export function buildTimeline(messages: Message[]): TimelineItem[] {
  const nodes = buildProgressNodes(messages)
  const nodeByFirstMsg = new Map<string, ProgressNode>()
  for (const n of nodes) {
    if (n.messageIds[0]) nodeByFirstMsg.set(n.messageIds[0], n)
  }
  const consumedNodeKeys = new Set<NodeKey>()
  const items: TimelineItem[] = []

  for (const m of messages) {
    const node = nodeByFirstMsg.get(m.id)
    if (node && !consumedNodeKeys.has(node.key)) {
      items.push({ kind: 'node', node })
      consumedNodeKeys.add(node.key)
      // Keep interactive chat surface messages
      if (isChatSurfaceMessage(m) && m.role !== 'EVENT') {
        items.push({ kind: 'message', message: m })
      } else if (
        m.message_type === 'APP_CONFIRMATION_REQUIRED' ||
        m.message_type === 'HUMAN_TASK_REQUIRED' ||
        m.message_type === 'CORRECTION_APPLIED'
      ) {
        items.push({ kind: 'message', message: m })
      }
      // Preview events are NOT shown inline — they go to artifact panel
      continue
    }

    // Skip subsequent events already folded into a node
    if (isProgressEvent(m) && TYPE_INDEX.has(m.message_type)) {
      // Preview events: skip inline, handled by artifact panel
      continue
    }

    if (m.role === 'EVENT' && !TYPE_INDEX.has(m.message_type)) {
      // Unknown internal events: hide from main stream
      continue
    }

    items.push({ kind: 'message', message: m })
  }

  // Update node cards to latest state
  const latest = new Map(nodes.map(n => [n.key, n]))
  return items.map(item =>
    item.kind === 'node' ? { kind: 'node', node: latest.get(item.node.key) || item.node } : item
  )
}
