import type { LiveAction } from './liveActivity'
import type { AgentActivity, Message, ProjectStatus, WorkspaceAgent } from './types'

const ROLE_LABELS: Record<string, string> = {
  core: '主助手',
  executor: '执行',
  pm: '产品',
  dev: '开发',
  qa: '质检',
  coordinator: '协调',
  reviewer: '审查',
}

export const ACTIVITY_LABEL: Record<AgentActivity, string> = {
  active: '活动中',
  ready: '待命',
  waiting: '等待确认',
  done: '已完成',
  failed: '异常',
  idle: '空闲',
}

export function roleLabel(role: string): string {
  return ROLE_LABELS[role] || role
}

function coreActivity(goalStatus: string | null | undefined, live: LiveAction | null): AgentActivity {
  if (!goalStatus) return 'idle'
  if (goalStatus === 'ACHIEVED') return 'done'
  if (goalStatus === 'FAILED' || goalStatus === 'CANCELLED') return 'failed'
  if (goalStatus === 'WAITING_HUMAN') return 'waiting'
  if (['ACTIVE', 'READY', 'PAUSED', 'BLOCKED', 'EXHAUSTED'].includes(goalStatus)) {
    return live?.summary ? 'active' : 'ready'
  }
  return 'idle'
}

/** Client-side fallback when status.agents is empty (older Core). */
export function deriveAgents(
  status: ProjectStatus | null,
  liveAction: LiveAction | null,
  _messages: Message[] = [],
): WorkspaceAgent[] {
  const fromApi = status?.agents
  if (Array.isArray(fromApi) && fromApi.length > 0) {
    return fromApi.map(a => ({
      ...a,
      role_label: a.role_label || roleLabel(a.role),
      detail: a.is_main && liveAction?.summary ? liveAction.summary : a.detail,
      activity: a.is_main
        ? (liveAction?.summary && a.activity === 'ready' ? 'active' : a.activity)
        : a.activity,
    }))
  }

  const goalStatus = status?.goal?.status
  const agents: WorkspaceAgent[] = [
    {
      id: 'core',
      name: '主助手',
      role: 'core',
      role_label: '主助手',
      kind: 'core',
      activity: coreActivity(goalStatus, liveAction),
      detail: liveAction?.summary || liveAction?.detail || null,
      is_main: true,
    },
  ]

  const meta = status?.goal?.metadata || {}
  const topo = (meta.topology || meta.selected_topology || meta.organization_topology) as
    | Record<string, unknown>
    | undefined
  const roles = Array.isArray(topo?.roles) ? topo.roles : []
  for (let i = 0; i < roles.length; i += 1) {
    const raw = roles[i]
    const role = typeof raw === 'string'
      ? raw
      : String((raw as Record<string, unknown>)?.role || 'executor')
    if (role === 'core') continue
    const label = roleLabel(role)
    agents.push({
      id: `derived-${role}-${i}`,
      name: label,
      role,
      role_label: label,
      kind: 'derived',
      activity: coreActivity(goalStatus, null) === 'active' ? 'ready' : coreActivity(goalStatus, null),
      detail: null,
      is_main: false,
    })
  }

  return agents
}

export function agentInitials(agent: WorkspaceAgent): string {
  if (agent.is_main || agent.role === 'core') return '主'
  const label = agent.role_label || agent.name
  return label.slice(0, 1)
}

export function countActiveAgents(agents: WorkspaceAgent[]): number {
  return agents.filter(a => a.activity === 'active' || a.activity === 'waiting').length
}
