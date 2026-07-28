import type { Project } from '../lib/types'
import type { NodeKey, NodeStatus } from '../lib/progressNodes'

const STAGE_LABELS: Record<string, string> = {
  NOT_STARTED: '准备开始',
  QUEUED: '排队中',
  DISCOVERING: '正在市场调研...',
  DECIDED: '方案已决策',
  RESOLVED: '技术已就绪',
  GENERATING: '正在生成应用...',
  SNAPSHOT_READY: '快照已就绪',
  BUILD_PASSED: '构建已通过',
  DEPLOYED: '预览已部署',
  RESEARCH_MORE: '正在深入调研...',
  PREVIEW_SUCCEEDED: '预览已就绪',
  GATE_INSUFFICIENT_EVIDENCE: '需要更多数据',
  GATE_PASSED: '验证已通过',
  GATE_FAILED: '验证未通过',
  FAILED: '遇到问题',
}

/** Product-friendly status labels (shown in badge) */
const GOAL_STATUS_LABELS: Record<string, string> = {
  DRAFT: '草稿',
  READY: '准备中',
  ACTIVE: '进行中',
  PAUSED: '已暂停',
  WAITING_HUMAN: '需要你确认',
  ACHIEVED: '已完成',
  EXHAUSTED: '已暂停',
  FAILED: '遇到问题',
  CANCELLED: '已取消',
}

/** Ordered stage keys for progress bar */
const PROGRESS_STAGES: NodeKey[] = [
  'understand', 'discover', 'require', 'capability', 'generate',
  'build', 'preview', 'verify', 'milestone', 'outcome',
]

const STAGE_TITLES: Record<NodeKey, string> = {
  understand: '理解',
  discover: '调研',
  require: '规划',
  capability: '准备',
  generate: '生成',
  build: '检查',
  preview: '预览',
  verify: '验证',
  milestone: '里程碑',
  human: '确认',
  outcome: '完成',
}

interface SidebarProps {
  projects: Project[]
  currentProject: Project | null
  onSelect: (id: string) => void
  onNew: () => void
  isOpen: boolean
}

function statusDot(status: string) {
  if (status === 'ACTIVE') return 'dot-active'
  if (status === 'PAUSED') return 'dot-paused'
  if (status === 'WAITING_HUMAN') return 'dot-waiting'
  return 'dot-terminal'
}

export function Sidebar({ projects, currentProject, onSelect, onNew, isOpen }: SidebarProps) {
  return (
    <aside className={`sidebar ${isOpen ? 'open' : ''}`}>
      <div className="brand">
        <span className="mark">R</span>
        <span>Regent</span>
      </div>
      <button className="new-btn" onClick={onNew}>+ 新建 App</button>
      <div className="threads">
        {projects.map(p => (
          <button
            key={p.id}
            className={`thread ${currentProject?.id === p.id ? 'active' : ''}`}
            onClick={() => onSelect(p.id)}
          >
            <span className={`thread-dot ${statusDot(p.status)}`} />
            <span className="thread-name">{p.name}</span>
          </button>
        ))}
      </div>
      <div className="sidefoot">所有对话、执行与决策均持久保存</div>
    </aside>
  )
}

interface StageBarProps {
  status: {
    goal: { status: string; metadata: Record<string, unknown>; execution_stage?: { stage: string } } | null
  } | null
  progressNodes?: { key: NodeKey; status: NodeStatus }[]
  onQuickAction: (text: string) => void
}

export function StageBar({ status, progressNodes, onQuickAction }: StageBarProps) {
  if (!status?.goal) {
    return (
      <div className="stage-bar">
        <span className="stage-badge">准备开始</span>
      </div>
    )
  }

  const goal = status.goal
  const meta = goal.metadata || {}
  const stage = (meta.execution_stage as string) || goal.execution_stage?.stage || goal.status
  const label = STAGE_LABELS[stage] || GOAL_STATUS_LABELS[goal.status] || stage
  const goalStatusLabel = GOAL_STATUS_LABELS[goal.status] || goal.status

  const corrections = (meta.active_corrections as unknown[]) || []

  // Compute progress from nodes
  const completedCount = progressNodes
    ? progressNodes.filter(n => n.status === 'done').length
    : 0
  const totalStages = PROGRESS_STAGES.length

  return (
    <div className="stage-bar">
      <div className="stage-bar-left">
        <span className={`stage-badge ${goal.status.toLowerCase()}`}>
          {label}
        </span>
        {goal.status !== 'DRAFT' && (
          <span className="stage-goal-status">{goalStatusLabel}</span>
        )}
        {corrections.length > 0 && (
          <span className="stage-corrections">有 {corrections.length} 条修正</span>
        )}
      </div>

      {/* Progress bar */}
      <div className="stage-progress" title={`${completedCount} / ${totalStages} 阶段已完成`}>
        {PROGRESS_STAGES.map(key => {
          const nodeState = progressNodes?.find(n => n.key === key)
          const nodeStatus = nodeState?.status
          let cls = 'stage-step-pending'
          if (nodeStatus === 'done') cls = 'stage-step-done'
          else if (nodeStatus === 'running') cls = 'stage-step-running'
          else if (nodeStatus === 'failed') cls = 'stage-step-failed'
          else if (nodeStatus === 'waiting') cls = 'stage-step-waiting'
          return (
            <div
              key={key}
              className={`stage-step ${cls}`}
              title={STAGE_TITLES[key]}
            />
          )
        })}
      </div>

      <div className="quick-actions">
        {goal.status === 'ACTIVE' && (
          <button className="qa-btn" onClick={() => onQuickAction('暂停执行')}>暂停</button>
        )}
        {goal.status === 'PAUSED' && (
          <button className="qa-btn" onClick={() => onQuickAction('恢复执行')}>恢复</button>
        )}
        {goal.status === 'WAITING_HUMAN' && (
          <>
            <button className="qa-btn" onClick={() => onQuickAction('批准')}>批准</button>
            <button className="qa-btn danger" onClick={() => onQuickAction('拒绝，需要修改')}>拒绝</button>
          </>
        )}
      </div>
    </div>
  )
}
