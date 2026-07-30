export interface Project {
  id: string
  name: string
  status: string
  created_at: string
  updated_at: string
  metadata?: Record<string, unknown>
}

export interface Conversation {
  id: string
  app_project_id: string | null
  goal_id: string | null
  title: string
  status: string
  created_by: string
  metadata: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface Message {
  id: string
  conversation_id: string
  ordinal: number
  role: string
  message_type: string
  content: string
  metadata: Record<string, unknown>
  created_by: string
  created_at: string
}

export type AgentActivity = 'active' | 'ready' | 'waiting' | 'done' | 'failed' | 'idle'

export interface WorkspaceAgent {
  id: string
  name: string
  role: string
  role_label: string
  kind: 'core' | 'hive' | 'spec' | 'derived'
  activity: AgentActivity
  detail?: string | null
  is_main?: boolean
  deployment_status?: string
  spec_status?: string
}

export interface GoalStatus {
  goal: {
    id: string
    status: string
    original_input: string
    metadata: Record<string, unknown>
    execution_stage?: { stage: string }
  } | null
  preview: {
    status: string
    endpoint?: string
    failure_summary?: string
  } | null
  agents?: WorkspaceAgent[]
}

export interface HealthStatus {
  status: string
  environment: string
  database: string
  goals_active: number
  goals_achieved: number
}

export interface SSEEvent {
  type: string
  project_id?: string
  data: Record<string, unknown>
}

export interface DraftResult {
  project: Project
  conversation_id: string
  goal_id: string
}

export interface GuidanceResult {
  command_type: string
  resulting_goal_id?: string | null
  requires_confirmation: boolean
  message?: string
}

export interface ProjectStatus extends GoalStatus {}
