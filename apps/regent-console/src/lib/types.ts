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
  /** queued | calling_model | stalled | needs_continue | waiting_human | idle */
  generation_progress?: string
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
  plan?: Record<string, unknown>
  needs_user_fork?: boolean
  auto_started?: boolean
}

export interface GuidanceResult {
  command_type: string
  resulting_goal_id?: string | null
  requires_confirmation: boolean
  message?: string
}

export interface ProjectStatus extends GoalStatus {}

export interface DiagnosticRecommendation {
  id: string
  label: string
  description?: string
  action: string
}

export interface DiagnosticDelivery {
  id?: string
  schema_version?: string
  goal_id?: string
  terminal_reason?: string
  status?: string
  resumable?: boolean
  promote_allowed?: boolean
  summary?: string
  gap_kind?: string
  attempts?: number
  budget?: {
    turns_used?: number
    turns_limit?: number
    tokens_used?: number
    tokens_limit?: number
    elapsed_seconds?: number
  }
  artifacts?: Array<{
    kind: string
    snapshot_id?: string
    file_count?: number
    sha256?: string
    size?: number
    name?: string
  }>
  preview?: {
    state?: 'VERIFIED' | 'DRAFT' | 'UNAVAILABLE' | string
    reason?: string
    last_verified_endpoint?: string | null
  }
  findings?: Array<{
    code: string
    title?: string
    detail?: string
    severity?: string
  }>
  recommendations?: DiagnosticRecommendation[]
  resume?: {
    base_snapshot_id?: string | null
    allowed_actions?: string[]
  }
}

/** CD-3.2 / CD-3.5: option-based handoff shown on TaskCard instead of plain allow/deny. */
export interface HandoffOption {
  id: string
  label: string
  cost_hint?: string
}

/** CD-3.3 budget summary (turns/tokens), shown when the API provides it. */
export interface DeliveryReviewBudget {
  turns?: number
  max_turns?: number
  input_tokens?: number
  output_tokens?: number
  max_tokens?: number
}

/** CD-3.1: read-only review payload (plan / transcript / verification / budget). */
export interface DeliveryReview {
  plan?: Record<string, unknown> | null
  transcript?: unknown[] | null
  verification?: Record<string, unknown> | null
  budget?: DeliveryReviewBudget | null
}

export interface PlanItem {
  id: string
  item_key: string
  content: string
  status: string
  owner_agent_id?: string | null
  dependencies?: string[]
  updated_at?: string | null
}

export interface ActivityEvent {
  type?: string
  turn?: number | null
  tool?: string | null
  summary?: string
  args_preview?: string | null
  result_preview?: string | null
  input_tokens?: number | null
  output_tokens?: number | null
  cached_tokens?: number | null
  updated_at?: string | null
}

export interface RuntimeAgent {
  id: string
  name: string
  role?: string
  role_label?: string
  activity: AgentActivity
  detail?: string | null
  milestone_key?: string | null
  tool?: string | null
  is_main?: boolean
  kind?: string
}

export interface WorkspaceTreeNode {
  path: string
  name: string
  kind: 'file' | 'dir'
  size?: number
}
