import type {
  Project,
  Conversation,
  Message,
  DraftResult,
  GuidanceResult,
  ProjectStatus,
  HealthStatus,
  DeliveryReview,
  PlanItem,
  ActivityEvent,
  RuntimeAgent,
  WorkspaceTreeNode,
} from './types'

const ACTOR = 'trial-user'

async function request<T>(path: string, body?: unknown, method?: string): Promise<T> {
  const res = await fetch(path, {
    method: method || (body ? 'POST' : 'GET'),
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  const text = await res.text()
  let data: T
  try {
    data = JSON.parse(text) as T
  } catch {
    if (!res.ok) throw new Error(text || `${res.status}`)
    return text as unknown as T
  }
  if (!res.ok) {
    const msg = (data as Record<string, unknown>)?.error
    throw new Error(
      (msg as Record<string, string>)?.message || String(msg) || `${res.status}`
    )
  }
  return data
}

export const api = {
  health: () => request<HealthStatus>('/health/ready'),

  listProjects: () => request<Project[]>('/v1/app-projects'),

  getProject: (id: string) => request<Project>(`/v1/app-projects/${id}`),

  getProjectStatus: (id: string) => request<ProjectStatus>(`/v1/app-projects/${id}/status`),

  confirmProject: (id: string, hash: string) =>
    request<{ project: Project }>(`/v1/app-projects/${id}/confirm`, {
      actor: ACTOR,
      expected_spec_hash: hash,
    }),

  createDraft: (idea: string) =>
    request<DraftResult>('/v1/app-projects/drafts', { idea, actor: ACTOR }),

  guidance: (projectId: string, message: string) =>
    request<GuidanceResult>(`/v1/app-projects/${projectId}/guidance`, {
      message,
      actor: ACTOR,
    }),

  listConversations: () => request<Conversation[]>('/v1/conversations'),

  getMessages: (convId: string) =>
    request<Message[]>(`/v1/conversations/${convId}/messages`),

  sendMessage: (convId: string, content: string, messageType = 'TEXT', metadata = {}) =>
    request<Message>(`/v1/conversations/${convId}/messages`, {
      role: 'USER',
      message_type: messageType,
      content,
      actor: ACTOR,
      metadata: { ...metadata, idempotency_key: `console-${crypto.randomUUID()}` },
    }),

  startGoal: (goalId: string) =>
    request<Record<string, unknown>>(`/v1/goals/${goalId}/start`, {
      actor: ACTOR,
      idempotency_key: `console-start-${goalId}`,
    }),

  completeTask: (
    taskId: string,
    approved: boolean,
    reason?: string,
    opts?: { always_allow?: boolean; option_id?: string },
  ) =>
    request<Record<string, unknown>>(`/v1/human-tasks/${taskId}/complete`, {
      assigned_to: ACTOR,
      response: {
        ...(approved
          ? { approved: true, decision: 'APPROVE' }
          : { approved: false, decision: 'REJECT', rejection_reason: reason || '未提供原因' }),
        ...(opts?.always_allow ? { always_allow: true } : {}),
        ...(opts?.option_id ? { option_id: opts.option_id } : {}),
      },
    }),

  // CD-3.2: read-only delivery review (plan/transcript/verification/budget).
  getDeliveryReview: (projectId: string) =>
    request<DeliveryReview>(`/v1/app-projects/${projectId}/delivery-review`),

  getPlanItems: (goalId: string) =>
    request<PlanItem[]>(`/v1/goals/${goalId}/plan-items`),

  abortGoal: (goalId: string, actor = ACTOR, reason = 'user_abort') =>
    request<{ ok: boolean; goal_id: string; abort?: boolean }>(`/v1/goals/${goalId}/abort`, {
      actor,
      reason,
    }),

  getAgentLoopExit: (goalId: string) =>
    request<{
      exit: Record<string, unknown> | null
      pending_ask?: Record<string, unknown> | null
      execution_mode?: string
      work_plan_approved?: boolean
    }>(`/v1/goals/${goalId}/agent-loop-exit`),

  setExecutionMode: (goalId: string, mode: 'ask' | 'act', actor = ACTOR) =>
    request<{ ok: boolean; execution_mode: string }>(`/v1/goals/${goalId}/execution-mode`, {
      actor,
      mode,
    }),

  /** TRANSITIONAL: metadata ring buffers — not durable event truth. */
  getGoalActivity: (goalId: string) =>
    request<{
      events: ActivityEvent[]
      tool_events: Record<string, unknown>[]
      live_action?: Record<string, unknown> | null
      regent_events?: Record<string, unknown>[]
      agent_loop_exit?: Record<string, unknown> | null
      execution_mode?: string
      pending_agent_loop_ask?: Record<string, unknown> | null
    }>(`/v1/goals/${goalId}/activity`),

  /** TRANSITIONAL: in-process subagent roster may be empty after worker restart. */
  getGoalAgents: (goalId: string) =>
    request<RuntimeAgent[]>(`/v1/goals/${goalId}/agents`),

  getWorkspaceTree: (projectId: string) =>
    request<{ root: string; entries: WorkspaceTreeNode[] }>(
      `/v1/app-projects/${projectId}/workspace/tree`,
    ),

  getWorkspaceFile: (projectId: string, path: string) =>
    request<{ path: string; content: string; truncated?: boolean }>(
      `/v1/app-projects/${projectId}/workspace/file?path=${encodeURIComponent(path)}`,
    ),

  getWorkspaceDiff: (projectId: string, from?: string, to?: string) => {
    const q = new URLSearchParams()
    if (from) q.set('from', from)
    if (to) q.set('to', to)
    const qs = q.toString()
    return request<{ from: string; to: string; patch: string }>(
      `/v1/app-projects/${projectId}/workspace/diff${qs ? `?${qs}` : ''}`,
    )
  },

  uploadFile: async (file: File, projectId?: string) => {
    const form = new FormData()
    form.append('file', file)
    if (projectId) form.append('project_id', projectId)
    form.append('actor', ACTOR)
    const res = await fetch('/v1/uploads', { method: 'POST', body: form })
    if (!res.ok) {
      const text = await res.text()
      throw new Error(text || `${res.status}`)
    }
    return res.json() as Promise<{ id: string; filename: string; size: number }>
  },

  downloadArtifact: (projectId: string) => {
    const a = document.createElement('a')
    a.href = `/v1/app-delivery/${projectId}/download`
    a.download = ''
    a.click()
  },
}
