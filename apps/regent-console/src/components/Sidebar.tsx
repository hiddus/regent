import { useEffect, useState } from 'react'
import type { PlanItem } from '../lib/types'
import type { LiveActivity } from '../lib/liveActivity'
import { deriveLiveLabel } from '../lib/liveActivity'

const STAGE_LABELS: Record<string, string> = {
  NOT_STARTED: '准备开始',
  QUEUED: '排队中',
  DISCOVERING: '正在市场调研...',
  DECIDED: '方案已决策',
  RESOLVED: '技术已就绪',
  GENERATING: '正在生成应用...',
  SNAPSHOT_READY: '快照已就绪',
  BUILD_PASSED: '构建已通过',
  BUILD_FAILED: '构建未通过，正在修复',
  BUILD_DELIVERY_GAP_EXHAUSTED: '构建修复已用尽，需要你介入',
  DEPLOYED: '预览已部署',
  DEPLOY_FAILED: '部署未成功，正在重试',
  DEPLOY_NOT_SUCCEEDED: '部署未成功，正在重试',
  DEPLOY_FAILED_NEEDS_HUMAN: '部署失败，需要你介入',
  DEPLOY_NOT_SUCCEEDED_NEEDS_HUMAN: '部署失败，需要你介入',
  DEPLOY_DELIVERY_REJECTED: '交付审查未通过，需要你介入',
  DELIVERY_GAP_EXHAUSTED: '自动修复已用尽，需要你介入',
  DISCOVERY_NO_SELECT: '调研未选定方案，正在重试',
  DISCOVERY_NO_SELECT_NEEDS_HUMAN: '调研未选定方案，需要你介入',
  RESEARCH_MORE: '正在深入调研...',
  RESEARCH_MORE_NEEDS_HUMAN: '调研取证不足，需要你介入',
  PREVIEW_SUCCEEDED: '预览已就绪',
  GATE_INSUFFICIENT_EVIDENCE: '需要更多数据',
  GATE_PASSED: '验证已通过',
  GATE_FAILED: '验证未通过，正在重试',
  WAITING_HUMAN: '需要你确认',
  WAITING_HUMAN_VERIFICATION: '需要你确认交付',
  BLOCKED: '已受阻，需要介入',
  REORGANIZING: '正在重组能力...',
  DELIVERY_SOFT_PAUSE: '自动修复已暂停',
  DELIVERED_FOR_REVIEW: '成果已交付审阅',
  FAILED: '遇到问题，正在处理',
}

const GENERATION_PROGRESS_LABELS: Record<string, string> = {
  queued: '排队等待生成...',
  calling_model: '正在调用模型生成...',
  stalled: '生成停滞，可点继续',
  needs_continue: '已失败，可点继续',
  waiting_human: '需要你确认',
}

const GOAL_STATUS_LABELS: Record<string, string> = {
  DRAFT: '草稿',
  READY: '准备中',
  ACTIVE: '进行中',
  PAUSED: '已暂停',
  WAITING_HUMAN: '需要你确认',
  BLOCKED: '已受阻',
  ACHIEVED: '已完成',
  EXHAUSTED: '需要介入',
  FAILED: '遇到问题',
  CANCELLED: '已取消',
}

interface SidebarProps {
  projects: import('../lib/types').Project[]
  currentProject: import('../lib/types').Project | null
  onSelect: (id: string) => void
  onNew: () => void
  isOpen: boolean
}

function statusDot(status: string) {
  if (status === 'ACTIVE') return 'dot-active'
  if (status === 'PAUSED') return 'dot-paused'
  if (status === 'WAITING_HUMAN' || status === 'EXHAUSTED' || status === 'BLOCKED') {
    return 'dot-waiting'
  }
  return 'dot-terminal'
}

export function Sidebar({ projects, currentProject, onSelect, onNew, isOpen }: SidebarProps) {
  const [showTerminal, setShowTerminal] = useState(false)
  const visible = projects.filter(p => {
    const s = String(p.status || '').toUpperCase()
    if (s === 'FAILED' || s === 'CANCELLED') return showTerminal
    return true
  })
  const hiddenCount = projects.length - visible.length

  return (
    <aside className={`sidebar ${isOpen ? 'open' : ''}`}>
      <div className="brand">
        <span className="mark">R</span>
        <span>Regent</span>
      </div>
      <button className="new-btn" onClick={onNew}>+ 新建 App</button>
      <div className="threads">
        {visible.map(p => (
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
      {hiddenCount > 0 || showTerminal ? (
        <button
          type="button"
          className="sidebar-toggle-terminal"
          onClick={() => setShowTerminal(v => !v)}
        >
          {showTerminal ? '隐藏已结束僵尸' : `显示已结束（${hiddenCount}）`}
        </button>
      ) : null}
      <div className="sidefoot">所有对话、执行与决策均持久保存</div>
    </aside>
  )
}

interface StageBarProps {
  status: {
    goal: { status: string; metadata: Record<string, unknown>; execution_stage?: { stage: string } } | null
    generation_progress?: string
  } | null
  planItems?: PlanItem[]
  liveActivity?: LiveActivity
  onQuickAction: (text: string) => void
}

/** Slim top bar: stage micro-label + plan ratio + RunControls (Stop first-class). */
export function StageBar({ status, planItems = [], liveActivity, onQuickAction }: StageBarProps) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])

  if (!status?.goal) {
    return (
      <div className="stage-bar-wrap">
        <div className="stage-bar">
          <span className="stage-badge">准备开始</span>
        </div>
      </div>
    )
  }

  const goal = status.goal
  const meta = goal.metadata || {}
  const stage = (meta.execution_stage as string) || goal.execution_stage?.stage || goal.status
  const genProgress = String(status.generation_progress || meta.generation_progress || '')
  const label =
    GENERATION_PROGRESS_LABELS[genProgress] ||
    STAGE_LABELS[stage] ||
    GOAL_STATUS_LABELS[goal.status] ||
    stage
  const goalStatusLabel = GOAL_STATUS_LABELS[goal.status] || goal.status
  const live = deriveLiveLabel(
    liveActivity ?? { connection: 'idle', lastProgressAt: null, lastHeartbeatAt: null, liveAction: null },
    goal.status,
    now,
  )

  const liveSummary = liveActivity?.liveAction?.summary
  const actionAt = liveActivity?.liveAction?.updated_at
    ? Date.parse(liveActivity.liveAction.updated_at)
    : NaN
  const actionElapsed = !Number.isNaN(actionAt)
    ? Math.max(0, Math.floor((now - actionAt) / 1000))
    : null
  const waitingLive =
    !!liveSummary &&
    (liveSummary.includes('等待你确认') || liveSummary.includes('需要你介入'))
  const stripSummary =
    waitingLive && goal.status === 'ACTIVE'
      ? '已批准，正在继续执行'
      : genProgress === 'queued'
        ? '排队等待生成槽位'
        : genProgress === 'calling_model'
          ? (liveSummary || '正在调用模型生成代码')
          : liveSummary
  const showElapsed = !(waitingLive && goal.status === 'ACTIVE') && genProgress !== 'queued'

  const planDone = planItems.filter(i => {
    const s = String(i.status || '').toLowerCase()
    return s === 'completed' || s === 'cancelled'
  }).length
  const planTotal = planItems.length
  const planInProgress = planItems.find(i => String(i.status || '').toLowerCase() === 'in_progress')

  const isActive = goal.status === 'ACTIVE' || goal.status === 'WAITING_HUMAN' || goal.status === 'PAUSED'
  const canContinue =
    goal.status === 'EXHAUSTED' ||
    goal.status === 'BLOCKED' ||
    goal.status === 'FAILED' ||
    genProgress === 'stalled' ||
    genProgress === 'needs_continue' ||
    stage === 'DELIVERY_SOFT_PAUSE'

  const hideRunningStrip =
    genProgress === 'needs_continue' ||
    genProgress === 'stalled' ||
    stage === 'DELIVERY_SOFT_PAUSE' ||
    !!meta.diagnostic_delivery

  const showStop =
    goal.status === 'ACTIVE' ||
    goal.status === 'WAITING_HUMAN' ||
    stage === 'GENERATING' ||
    stage === 'DELIVERY_SOFT_PAUSE'

  return (
    <div className="stage-bar-wrap">
      <div className="stage-bar">
        <div className="stage-bar-left">
          <span className={`stage-badge ${goal.status.toLowerCase()} ${live.tone === 'live' ? 'is-live' : ''}`}>
            {(live.tone === 'live' || isActive) && <span className="live-pulse-dot" aria-hidden />}
            {label}
          </span>
          {goal.status !== 'DRAFT' && (
            <span className="stage-goal-status">{goalStatusLabel}</span>
          )}
          <span className={`stage-live tone-${live.tone}`} title="实时连接与进展">
            {live.text}
          </span>
        </div>

        {planTotal > 0 ? (
          <div
            className="stage-plan-ratio"
            title={planInProgress ? `进行中：${planInProgress.content || planInProgress.item_key}` : '清单进度'}
          >
            <div className="stage-plan-track">
              <div
                className="stage-plan-fill"
                style={{ width: `${Math.round((planDone / planTotal) * 100)}%` }}
              />
            </div>
            <span className="stage-plan-label">
              清单 {planDone}/{planTotal}
            </span>
          </div>
        ) : (
          <div className="stage-plan-ratio muted">
            <span className="stage-plan-label">清单尚未生成</span>
          </div>
        )}

        <div className="quick-actions run-controls">
          {showStop && (
            <button className="qa-btn danger stop-btn" onClick={() => onQuickAction('停止执行')}>
              停止
            </button>
          )}
          {goal.status === 'ACTIVE' && genProgress !== 'stalled' && (
            <button className="qa-btn" onClick={() => onQuickAction('暂停执行')}>暂停</button>
          )}
          {goal.status === 'PAUSED' && (
            <button className="qa-btn" onClick={() => onQuickAction('恢复执行')}>恢复</button>
          )}
          {canContinue && (
            <button className="qa-btn" onClick={() => onQuickAction('继续尝试，请根据上次失败重新规划')}>
              继续此目标
            </button>
          )}
        </div>
      </div>
      {isActive && !hideRunningStrip && (
        <div className={`core-live-strip tone-${live.tone}`}>
          <span className="live-pulse-dot" aria-hidden />
          <span className="core-live-text">
            {stripSummary
              ? `Core 正在：${stripSummary}`
              : live.text.startsWith('Core：')
                ? live.text
                : `Core 正在执行 · ${label}`}
          </span>
          {showElapsed && actionElapsed != null && (
            <span className="core-live-elapsed">{actionElapsed}s</span>
          )}
        </div>
      )}
      {hideRunningStrip && (
        <div className="core-live-strip tone-idle">
          <span className="core-live-text">
            {genProgress === 'stalled'
              ? '生成已停滞，可点「继续此目标」或补充方向'
              : '自动修复已暂停；当前成果已保存，可查看源码或继续'}
          </span>
        </div>
      )}
    </div>
  )
}
