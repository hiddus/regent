import { useEffect, useMemo, useState } from 'react'
import type { DiagnosticDelivery, Message } from '../lib/types'
import { buildTimeline, type ProgressNodeExtras } from '../lib/progressNodes'
import { collapseRetryClusters } from '../lib/retryClusters'
import { isQuietActive, latestMessageTimestamp } from '../lib/liveActivity'
import type { LiveAction } from '../lib/liveActivity'
import { ConfirmationCard } from './ConfirmationCard'
import { QuietExecutionCard } from './QuietExecutionCard'
import { RecoveryCard } from './RecoveryCard'
import { TaskCard, type TaskActionOptions } from './TaskCard'
import { ProgressNodeCard } from './ProgressNodeCard'
import { ResultCard } from './ResultCard'
import { RetryClusterCard } from './RetryClusterCard'
import { LeadLine, MarkdownBody } from './MarkdownBody'

const EMPTY_EXAMPLES = [
  '做一个景区门票预约小程序，支持选日期和人数',
  '帮我做一个团队周报收集网页，能导出 Markdown',
  '创建一个本地待办 App，支持标签和截止日期',
]

interface MessageListProps {
  messages: Message[]
  currentProjectId?: string | null
  goalStatus?: string | null
  goalDiagnostic?: DiagnosticDelivery | null
  executionStage?: string | null
  agentLoopExit?: Record<string, unknown> | null
  generationProgress?: string | null
  liveAction?: LiveAction | null
  toolEvents?: Record<string, unknown>[]
  liveTool?: string | null
  onConfirm: (projectId: string, goalId: string, hash: string) => void
  onSelectOption?: (projectId: string, optionId: string, label: string) => void
  onTaskAction: (taskId: string, approved: boolean, opts?: TaskActionOptions) => void
  onInspectSource?: () => void
  onOpenPreview?: () => void
  onOpenReview?: () => void
  onExampleSend?: (text: string) => void
  onQuickAction?: (text: string) => void
  pendingSend?: { text: string; startedAt: number; state: 'processing' | 'failed'; error?: string } | null
  onRetryPending?: () => void
  userHint?: string
  userHintError?: boolean
  coreHint?: string
  coreHintError?: boolean
  goalMetadata?: Record<string, unknown>
}

function buildMovingGoals(items: Message[]): Set<string> {
  const set = new Set<string>()
  for (const m of items) {
    const t = m.message_type || ''
    if (
      t === 'GOAL_CONFIRMED' || t === 'GOAL_EXECUTION_QUEUED' ||
      t.startsWith('GOAL_EXECUTION_') || t === 'PREVIEW_READY' ||
      t === 'PREVIEW_DEPLOYMENT_SUCCEEDED' || t === 'FORK_SELECTED'
    ) {
      const mid = m.metadata?.goal_id as string | undefined
      if (mid) set.add(mid)
      else set.add('*')
    }
  }
  return set
}

function buildResolvedTasks(items: Message[]): {
  byTaskId: Set<string>
  byGoalId: Set<string>
  anyApprove: boolean
} {
  const byTaskId = new Set<string>()
  const byGoalId = new Set<string>()
  let anyApprove = false
  for (const m of items) {
    if (m.message_type !== 'APPROVE_RESULT' && m.message_type !== 'REJECT_RESULT') continue
    const tid = String(m.metadata?.task_id || '')
    const gid = m.metadata?.goal_id as string | undefined
    if (tid) byTaskId.add(tid)
    if (gid) byGoalId.add(gid)
    if (m.message_type === 'APPROVE_RESULT') anyApprove = true
  }
  return { byTaskId, byGoalId, anyApprove }
}

function taskMetaResolved(taskMeta?: Record<string, unknown>) {
  if (!taskMeta) return false
  const status = String(taskMeta.status || '').toUpperCase()
  if (status === 'COMPLETED' || status === 'TIMED_OUT' || status === 'CANCELLED') return true
  const dueAt = typeof taskMeta.due_at === 'string' ? Date.parse(taskMeta.due_at) : NaN
  return Number.isFinite(dueAt) && dueAt <= Date.now()
}

function isProcessNoise(m: Message): boolean {
  const t = m.message_type || ''
  return (
    t === 'DELIVERY_GAP_CAPABILITY_ESCALATED' ||
    t === 'GENERATION_ATTEMPT_FAILED' ||
    t === 'STALE_PROGRESS_NOTE' ||
    (m.role === 'ASSISTANT' && t.endsWith('_FAILED') && t !== 'GOAL_FAILED')
  )
}

function looksLikeRawJson(text: string): boolean {
  const t = text.trim()
  return (t.startsWith('{') && t.includes('"')) || (t.startsWith('[') && t.includes('{'))
}

function PlanSummary({ metadata }: { metadata: Record<string, unknown> }) {
  const understanding = (metadata.understanding || {}) as Record<string, unknown>
  const plan = (metadata.plan || {}) as Record<string, unknown>
  const stepsRaw = plan.steps || plan.items || plan.milestones
  const steps = Array.isArray(stepsRaw) ? stepsRaw.slice(0, 6) : []
  const goal = String(understanding.first_deliverable || understanding.problem || '').trim()
  const unknowns = Array.isArray(understanding.unknowns) ? understanding.unknowns.map(String) : []
  if (!goal && steps.length === 0 && unknowns.length === 0) return null
  return <section className="message-plan-summary" aria-label="目标与计划摘要">
    <div className="message-plan-heading"><strong>目标与计划</strong><span>请在对话中补充或确认</span></div>
    {goal && <div className="message-plan-goal"><small>首个可验收结果</small><p>{goal}</p></div>}
    {steps.length > 0 && <ol>{steps.map((item, index) => {
      const row = item as Record<string, unknown>
      return <li key={index}>{String(row.title || row.content || row.name || item)}</li>
    })}</ol>}
    {unknowns.length > 0 && <div className="message-plan-unknown"><strong>仍需确认</strong><span>{unknowns.join('；')}</span></div>}
  </section>
}

function MessageItem({
  m,
  movingGoals,
  resolved,
  currentProjectId,
  goalDiagnostic,
  goalMetadata,
  stickyGate,
  onConfirm,
  onSelectOption,
  onTaskAction,
  onInspectSource,
}: {
  m: Message
  movingGoals: Set<string>
  resolved: boolean
  currentProjectId?: string | null
  goalDiagnostic?: DiagnosticDelivery | null
  goalMetadata?: Record<string, unknown>
  stickyGate?: boolean
  onConfirm: (projectId: string, goalId: string, hash: string) => void
  onSelectOption?: (projectId: string, optionId: string, label: string) => void
  onTaskAction: (taskId: string, approved: boolean, opts?: TaskActionOptions) => void
  onInspectSource?: () => void
}) {
  const isConfirmation =
    m.message_type === 'APP_CONFIRMATION_REQUIRED' ||
    m.message_type === 'GOAL_UNDERSTANDING_READY' ||
    m.message_type === 'GOAL_PLAN_PROPOSED'
  const goalId = m.metadata?.goal_id as string | undefined
  const needsUserFork = Boolean(m.metadata?.needs_user_fork)
  const forkResolved = Boolean(
    goalId ? movingGoals.has(goalId) : movingGoals.has('*'),
  )
  const isAwaiting =
    (m.message_type === 'APP_CONFIRMATION_REQUIRED' || m.message_type === 'GOAL_PLAN_PROPOSED') &&
    !(goalId ? movingGoals.has(goalId) : movingGoals.has('*'))
  const showForkActions =
    needsUserFork && !forkResolved && m.message_type === 'GOAL_PLAN_PROPOSED'
  const isCorrection = m.message_type === 'CORRECTION_APPLIED'
  const roleClass = m.role.toLowerCase()
  const avatarLabel = m.role === 'USER' ? '你' : 'R'
  const metaLabel = m.role === 'USER' ? '你' : 'Regent'
  const processNoise = isProcessNoise(m)

  if (m.message_type === 'PREVIEW_READY' || m.message_type === 'PREVIEW_DEPLOYMENT_SUCCEEDED') {
    return null
  }

  const taskMeta = { ...(m.metadata || {}) } as Record<string, unknown>
  const taskId = String(taskMeta.id || taskMeta.human_task_id || '')
  const taskTypeUpper = String(taskMeta.task_type || '').toUpperCase()
  const confirmationAction = String(
    ((taskMeta.confirmation as Record<string, unknown> | undefined)?.action as string) || '',
  ).toLowerCase()
  const isDeliveryGapIntervene =
    taskTypeUpper === 'DELIVERY_GAP_INTERVENE' ||
    confirmationAction === 'delivery_gap_intervene' ||
    m.message_type === 'DELIVERY_SOFT_PAUSE' ||
    m.message_type === 'DIAGNOSTIC_DELIVERY_READY' ||
    m.message_type === 'STALE_PROGRESS_NOTE'

  const diagnosticDelivery = (
    (taskMeta.diagnostic_delivery as DiagnosticDelivery | undefined)
    || (m.message_type === 'DIAGNOSTIC_DELIVERY_READY' ? (taskMeta as unknown as DiagnosticDelivery) : undefined)
    || ((m.message_type === 'DELIVERY_SOFT_PAUSE' || m.message_type === 'DIAGNOSTIC_DELIVERY_READY')
      ? goalDiagnostic || undefined
      : undefined)
  )
  const showRecoveryCard =
    !!diagnosticDelivery
    && (m.message_type === 'DIAGNOSTIC_DELIVERY_READY'
      || m.message_type === 'DELIVERY_SOFT_PAUSE'
      || !!diagnosticDelivery.terminal_reason)

  const isExhaustedHandoff =
    !isDeliveryGapIntervene &&
    (m.message_type === 'DELIVERY_GAP_EXHAUSTED' ||
      m.message_type === 'BUILD_DELIVERY_GAP_EXHAUSTED' ||
      m.message_type === 'RESEARCH_MORE_ADAPT_EXHAUSTED' ||
      (m.message_type === 'HUMAN_TASK_REQUIRED' &&
        (Boolean(taskMeta.confirmation) ||
          String(taskMeta.stage || '').includes('DELIVERY_GAP') ||
          String(taskMeta.stage || '').includes('NEEDS_HUMAN'))))

  if (isExhaustedHandoff && !taskMeta.confirmation) {
    taskMeta.confirmation = {
      action: String(taskMeta.task_type || 'DELIVERY_GAP_INTERVENE'),
      summary: '自动修复已用尽，需要你介入',
      rules_applied: ['stage:DELIVERY_GAP_EXHAUSTED'],
      risk_level: 'high',
      timeout_seconds: 300,
      default_on_timeout: 'deny',
      detail: m.content?.slice(0, 400) || null,
    }
  }
  if (isExhaustedHandoff && !taskMeta.task_type) {
    taskMeta.task_type = 'DELIVERY_GAP_INTERVENE'
  }
  if (isExhaustedHandoff && !taskMeta.prompt) {
    taskMeta.prompt = m.content
  }

  const showTaskCard =
    !isDeliveryGapIntervene &&
    (m.message_type === 'HUMAN_TASK_REQUIRED' || isExhaustedHandoff)

  // OpenHands-style: structured card is the primary surface; don't duplicate markdown body.
  const hideBodyForCard = isConfirmation || showTaskCard || showRecoveryCard || isCorrection
  const rawJsonBody = looksLikeRawJson(m.content || '')
  const showMarkdown =
    !hideBodyForCard &&
    !!m.content?.trim() &&
    !rawJsonBody

  const gateActive =
    stickyGate &&
    ((isConfirmation && (isAwaiting || showForkActions)) ||
      (showTaskCard && !resolved) ||
      showRecoveryCard)

  return (
    <article
      className={[
        'message',
        roleClass,
        showTaskCard ? 'message-task' : '',
        isConfirmation ? 'message-confirm' : '',
        processNoise ? 'message-noise' : '',
        gateActive ? 'gate-sticky' : '',
      ]
        .filter(Boolean)
        .join(' ')}
      data-message-type={m.message_type || ''}
    >
      <div className="avatar" aria-hidden>{avatarLabel}</div>
      <div className="body">
        <div className="meta">
          <span>{metaLabel}</span>
          {processNoise ? <span className="meta-chip">过程</span> : null}
          {isConfirmation ? <span className="meta-chip">方案</span> : null}
        </div>

        {isConfirmation && !showMarkdown && (
          <LeadLine>
            {showForkActions
              ? '需要你选一个方向后继续'
              : isAwaiting
                ? '已形成拟议方案，确认后开始'
                : '方案如下，可随时在输入框补充修正'}
          </LeadLine>
        )}

        {isConfirmation && <PlanSummary metadata={{ ...(m.metadata || {}), ...(goalMetadata || {}) }} />}

        {showMarkdown && (
          <MarkdownBody
            collapsible={processNoise || (m.role === 'ASSISTANT' && (m.content?.length || 0) > 480)}
            collapseAt={processNoise ? 180 : 420}
            collapsedLabel={processNoise ? '展开过程说明' : '展开全文'}
          >
            {m.content}
          </MarkdownBody>
        )}

        {showRecoveryCard && diagnosticDelivery && (
          <RecoveryCard
            delivery={diagnosticDelivery}
            summary={m.content}
            projectId={String(
              m.metadata?.app_project_id || currentProjectId || '',
            )}
            onInspect={onInspectSource}
            onAction={(action, label) => {
              const pid = String(
                m.metadata?.app_project_id || currentProjectId || '',
              )
              if (pid) onSelectOption?.(pid, action, label)
            }}
          />
        )}

        {isConfirmation && (
          <ConfirmationCard
            metadata={{ ...(m.metadata || {}), ...(goalMetadata || {}) }}
            canConfirm={isAwaiting}
            needsUserFork={showForkActions}
            docked={!!stickyGate && (isAwaiting || showForkActions)}
            onConfirm={() => {
              const pid = String(
                m.metadata?.app_project_id || currentProjectId || '',
              )
              const gid = m.metadata?.goal_id as string
              const hash = (m.metadata?.goal_spec_hash as string) || ''
              onConfirm(pid, gid, hash)
            }}
            onSelectOption={(optionId, label) => {
              const pid = String(
                m.metadata?.app_project_id || currentProjectId || '',
              )
              if (pid) onSelectOption?.(pid, optionId, label)
            }}
          />
        )}

        {isCorrection && (
          <div className="correction-card">
            <span className="ct-label">
              修正 [{(m.metadata?.correction_target as string) || '其他'}]
            </span>
            <span className="ct-detail">{(m.metadata?.correction_detail as string) || ''}</span>
          </div>
        )}

        {showTaskCard && (
          <TaskCard
            task={taskMeta}
            resolved={resolved}
            compact={resolved}
            onAction={onTaskAction}
          />
        )}
      </div>
    </article>
  )
}

export function MessageList({
  messages,
  currentProjectId,
  goalStatus,
  goalDiagnostic,
  executionStage,
  agentLoopExit,
  generationProgress = null,
  liveAction = null,
  toolEvents = [],
  liveTool = null,
  onConfirm,
  onSelectOption,
  onTaskAction,
  onInspectSource,
  onOpenPreview,
  onOpenReview,
  onExampleSend,
  onQuickAction,
  pendingSend = null,
  onRetryPending,
  userHint = '',
  userHintError = false,
  coreHint = '',
  coreHintError = false,
  goalMetadata = {},
}: MessageListProps) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 15_000)
    return () => clearInterval(id)
  }, [])

  const movingGoals = useMemo(() => buildMovingGoals(messages), [messages])
  const resolvedIndex = useMemo(() => buildResolvedTasks(messages), [messages])

  const extras: ProgressNodeExtras = useMemo(
    () => ({ toolEvents, liveTool }),
    [toolEvents, liveTool],
  )

  const hasMessageRecovery = useMemo(
    () =>
      messages.some(m => {
        const meta = (m.metadata || {}) as Record<string, unknown>
        return (
          m.message_type === 'DIAGNOSTIC_DELIVERY_READY'
          || !!meta.diagnostic_delivery
        )
      }),
    [messages],
  )
  const showPinnedRecovery =
    !!goalDiagnostic
    && !hasMessageRecovery
    && (executionStage === 'DELIVERY_SOFT_PAUSE' || !!goalDiagnostic.terminal_reason)

  const exitKind = String(agentLoopExit?.exit_kind || '')
  const resultBundle = (agentLoopExit?.result_bundle || null) as Record<string, unknown> | null
  const showResultCard = exitKind === 'COMPLETE' || exitKind === 'STOP'

  const executionQuiet = isQuietActive({
    goalStatus,
    generationProgress,
    liveAction,
    lastProgressAt: latestMessageTimestamp(messages),
    now,
  })
  const showQuietGate = executionQuiet && !showPinnedRecovery && !showResultCard

  // Must run before any early return — hooks order must be stable.
  const timeline = useMemo(
    () => collapseRetryClusters(buildTimeline(messages, extras)),
    [messages, extras],
  )

  const activityCount = timeline.filter(item => item.kind === 'node' || item.kind === 'retry_cluster').length
  const pendingAlreadyPersisted = pendingSend
    ? messages.some(message => {
        if (message.role !== 'USER' || message.content.trim() !== pendingSend.text.trim()) return false
        const createdAt = Date.parse(message.created_at || '')
        return Number.isFinite(createdAt) && createdAt >= pendingSend.startedAt - 2_000
      })
    : false

  if (messages.length === 0 && !showPinnedRecovery && !showResultCard && !pendingSend && !userHint && !coreHint) {
    return (
      <section className="messages">
        <div className="stream">
          <div className="empty">
            <div className="empty-brand">Regent</div>
            <h1>用一句话描述你要的 App</h1>
            <p>Core 会给出拟议方案与工作清单；需要时再请你拍板。随时可停止。</p>
            <div className="empty-examples">
              {EMPTY_EXAMPLES.map(ex => (
                <button
                  key={ex}
                  type="button"
                  className="empty-example"
                  onClick={() => onExampleSend?.(ex)}
                >
                  {ex}
                </button>
              ))}
            </div>
          </div>
        </div>
      </section>
    )
  }

  const liveMode = !goalStatus || ['ACTIVE', 'WAITING_HUMAN', 'PAUSED', 'READY', 'BLOCKED', 'EXHAUSTED'].includes(goalStatus)

  // Last unsettled progress node stays detailed; older settled ones compress (Claude/Cursor).
  let lastLiveNodeIdx = -1
  timeline.forEach((item, idx) => {
    if (item.kind === 'node' && (item.node.status === 'running' || item.node.status === 'waiting')) {
      lastLiveNodeIdx = idx
    }
  })
  let lastSettledNodeIdx = -1
  timeline.forEach((item, idx) => {
    if (item.kind === 'node' && (item.node.status === 'done' || item.node.status === 'failed')) {
      lastSettledNodeIdx = idx
    }
  })

  // Sticky the last unresolved gate — keep it reachable without eating the viewport (cards stay slim).
  let stickyMessageId: string | null = null
  for (let i = timeline.length - 1; i >= 0; i -= 1) {
    const item = timeline[i]
    if (item.kind !== 'message') continue
    const m = item.message
    const taskMeta = (m.metadata || {}) as Record<string, unknown>
    const taskId = String(taskMeta.id || taskMeta.human_task_id || '')
    const goalId = (taskMeta.goal_id as string | undefined) || (m.metadata?.goal_id as string | undefined)
    const resolved =
      taskMetaResolved(taskMeta) ||
      (taskId ? resolvedIndex.byTaskId.has(taskId) : false) ||
      (goalId ? resolvedIndex.byGoalId.has(goalId) : false)
    const isConf =
      m.message_type === 'APP_CONFIRMATION_REQUIRED' ||
      m.message_type === 'GOAL_PLAN_PROPOSED'
    const awaiting =
      ((m.message_type === 'APP_CONFIRMATION_REQUIRED' || m.message_type === 'GOAL_PLAN_PROPOSED') &&
        !(goalId ? movingGoals.has(goalId) : movingGoals.has('*'))) ||
      (Boolean(m.metadata?.needs_user_fork) &&
        m.message_type === 'GOAL_PLAN_PROPOSED' &&
        !(goalId ? movingGoals.has(goalId) : movingGoals.has('*')))
    const isTask = m.message_type === 'HUMAN_TASK_REQUIRED' || String(m.message_type).includes('EXHAUSTED')
    const isRec =
      m.message_type === 'DIAGNOSTIC_DELIVERY_READY' ||
      m.message_type === 'DELIVERY_SOFT_PAUSE'
    if ((isConf && awaiting) || (isTask && !resolved) || isRec) {
      stickyMessageId = m.id
      break
    }
  }

  return (
    <section className="messages">
      <div className="stream">
        {activityCount > 1 && (
          <div className="activity-compression-note">
            <span>运行活动已自动归并</span>
            <small>{activityCount} 组进度与重试记录，可逐项展开查看</small>
          </div>
        )}
        {timeline.map((item, idx) => {
          if (item.kind === 'retry_cluster') {
            return <RetryClusterCard key={`retry-${item.fingerprint}-${idx}`} cluster={item} />
          }
          if (item.kind === 'node') {
            const preferCompressed =
              liveMode &&
              (item.node.status === 'done' || item.node.status === 'failed') &&
              (executionQuiet || (idx !== lastSettledNodeIdx && idx !== lastLiveNodeIdx))
            return (
              <ProgressNodeCard
                key={`node-${item.node.key}-${idx}`}
                node={item.node}
                liveMode={liveMode && !executionQuiet}
                preferCompressed={preferCompressed}
              />
            )
          }
          const m = item.message
          const taskMeta = (m.metadata || {}) as Record<string, unknown>
          const taskId = String(taskMeta.id || taskMeta.human_task_id || '')
          const goalId = (taskMeta.goal_id as string | undefined) || (m.metadata?.goal_id as string | undefined)
          const resolved =
            taskMetaResolved(taskMeta) ||
            (taskId ? resolvedIndex.byTaskId.has(taskId) : false) ||
            (goalId ? resolvedIndex.byGoalId.has(goalId) : false) ||
            (!goalId && !taskId && resolvedIndex.anyApprove)

          return (
            <MessageItem
              key={item.message.id}
              m={item.message}
              movingGoals={movingGoals}
              resolved={resolved}
              currentProjectId={currentProjectId}
              goalDiagnostic={goalDiagnostic}
              goalMetadata={goalMetadata}
              stickyGate={stickyMessageId === m.id}
              onConfirm={onConfirm}
              onSelectOption={onSelectOption}
              onTaskAction={onTaskAction}
              onInspectSource={onInspectSource}
            />
          )
        })}
        {showQuietGate && (
          <article className="message assistant gate-sticky gate-slim">
            <div className="body">
              <QuietExecutionCard
                lastProgressAt={latestMessageTimestamp(messages)}
                now={now}
                onContinue={() =>
                  onQuickAction?.('继续尝试，请根据上次失败重新规划')
                }
                onStop={() => onQuickAction?.('停止执行')}
              />
            </div>
          </article>
        )}
        {showPinnedRecovery && goalDiagnostic && (
          <article className="message assistant gate-sticky gate-slim">
            <div className="body">
              <RecoveryCard
                delivery={goalDiagnostic}
                summary={goalDiagnostic.summary}
                projectId={String(currentProjectId || '')}
                onInspect={onInspectSource}
                onAction={(action, label) => {
                  if (currentProjectId) onSelectOption?.(currentProjectId, action, label)
                }}
              />
            </div>
          </article>
        )}
        {showResultCard && (
          <article className="message assistant result-message">
            <div className="avatar">R</div>
            <div className="body">
              <div className="meta">Regent</div>
              <ResultCard
                exitKind={exitKind}
                summary={String(resultBundle?.summary || '')}
                openItems={
                  Array.isArray(resultBundle?.open_items)
                    ? (resultBundle!.open_items as string[])
                    : []
                }
                artifacts={
                  Array.isArray(resultBundle?.artifacts)
                    ? (resultBundle!.artifacts as Array<{
                        uri: string
                        label?: string
                        kind?: string
                      }>)
                    : resultBundle?.artifact_uri
                      ? [
                          {
                            uri: String(resultBundle.artifact_uri),
                            label: '主产物',
                            kind: 'primary',
                          },
                        ]
                      : []
                }
                previewUrl={
                  resultBundle?.preview_url ? String(resultBundle.preview_url) : null
                }
                stopReason={String(agentLoopExit?.stop_reason || '')}
                onOpenPreview={onOpenPreview}
                onOpenReview={onOpenReview}
              />
            </div>
          </article>
        )}
        {pendingSend && !pendingAlreadyPersisted && <article className="message user optimistic-message"><div className="avatar">你</div><div className="body"><div className="meta">你 · 发送中</div><MarkdownBody>{pendingSend.text}</MarkdownBody></div></article>}
        {pendingSend && <article className="message assistant pending-response" aria-live="polite"><div className="avatar">R</div><div className="body"><div className="meta">Regent · 实时进度</div><div className={`pending-response-card ${pendingSend.state}`}><span className="pending-dot"/><div><strong>{pendingSend.state === 'failed' ? '发送失败' : '服务器正在处理…'}</strong><p>{pendingSend.state === 'failed' ? (pendingSend.error || '未能提交，请重试。') : (liveAction?.summary || coreHint || userHint || '已收到你的消息，等待最新进度。')}</p><small>{Math.max(0, Math.floor((now - pendingSend.startedAt) / 1000))} 秒</small></div>{pendingSend.state === 'failed' && <button type="button" onClick={onRetryPending}>重试</button>}</div></div></article>}
        {!pendingSend && (userHint || coreHint) && <article className={`message assistant feed-status ${(userHintError || (!userHint && coreHintError)) ? 'error' : ''}`} aria-live="polite"><div className="avatar">R</div><div className="body"><div className="meta">Regent · 当前状态</div><div className="feed-status-card"><span className="feed-status-dot"/><p>{userHint || coreHint}</p></div></div></article>}
      </div>
    </section>
  )
}
