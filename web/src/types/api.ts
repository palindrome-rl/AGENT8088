// web/src/types/api.ts

// --- Engine state ---

export interface ProviderInfo {
  base_url: string
  api_key?: string
  api_key_env?: string
  model: string
  native_tools?: boolean
  api_mode?: string
  context_window?: string
  max_completion_tokens?: string
}

export interface ToolSpec {
  name: string
  description: string
  mode: string
  args?: string[]
  optional?: string[]
  arg_types?: Record<string, string>
  path_arg?: string
  content_arg?: string
  timeout: number
  aliases?: string[]
  category?: string
  enabled: boolean
}

export interface SkillPackage {
  name: string
  description: string
  resources: string[]
  enabled: boolean
  category?: string
}

export interface SubagentSpec {
  name: string
  description: string
  tools: string[]
  max_turns: number
  permission: string
  system_prompt: string
  model?: string
  builtin?: boolean
}

export interface MemoryFact {
  id: string
  text: string
  score: number
  user_id: string
  created_at: string
  source?: string
}

export interface MemoryStatus {
  enabled: boolean
  db_path: string
  user_id: string
  embed_model: string
  embed_provider: string
  extract_model: string
  capture_enabled: boolean
  recall_limit: number
  rrf_k: number
  scope_by_identity: boolean
  count: number
  stale_vectors: number
  embedder_ok: boolean | null
  embedder_error: string
  last_capture: Record<string, unknown>
  error: string
}

export interface SessionInfo {
  name: string
  message_count: number
  updated: string
  active: boolean
}

export interface SessionData {
  version: number
  name: string
  messages: ChatMessage[]
  temperature: number
  max_turns: number
  show_trace: boolean
  show_reasoning: boolean
  disabled_skills: string[]
  verbose: string
  usage_mode: string
  last_trace: unknown
  conversation_trace: unknown[]
  trace_path: string
}

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
  /** CLI/Rich output is terminal text, not Markdown. */
  format?: 'markdown' | 'terminal'
  attachments?: ChatAttachment[]
}

export interface ChatAttachment {
  id?: string
  name: string
  type?: string
  size?: number
}

export interface StatusInfo {
  model: string
  provider: string
  context_pct: number
  permission_mode: 'readonly' | 'full-auto' | 'plan-only'
  session_name: string
  last_usage: {
    seconds: number
    tokens: number
    context?: number
    interrupted?: boolean
  } | null
  rate_limit_status: {
    pct_remaining: number | null
    balance: { amount: number; currency: string } | null
  } | null
  verbose: string
  usage_mode: string
  show_trace: boolean
  show_reasoning: boolean
  temperature: number
  max_turns: number
  disabled_skills: string[]
  auto_compaction?: { threshold_pct: number; keep_messages: number }
  browser?: { current_host: string | null }
  /** Degradation registry rows, worst first (agent8088.capabilities.rows()). */
  capabilities?: CapabilityRow[]
  /** Which model answered last; fallback_for is set when the primary failed over. */
  model_served?: { provider: string; model: string; fallback_for: string; reason: string }
}

export interface CapabilityRow {
  name: string
  label: string
  active: string
  preferred: string
  state: 'ok' | 'degraded' | 'unavailable'
  reason: string
  impact: string
  fix: string
  model_note: string
  since: number
  updated: number
}

export interface ConfigInfo {
  model_name: string
  model_base_url: string
  default_provider: string
  active_provider: string
  config_path: string
  context_window: number
  tool_selection: 'full' | 'hybrid' | 'auto'
  max_turns: number
  temperature: number
  tools_file: string
  system_file: string
  skills_dir: string
  agents_dir: string
  project_root: string
  artifacts_root: string
  shell_cwd: string
  providers: Record<string, ProviderInfo>
  auto_compaction?: { threshold_pct: number; keep_messages: number }
  browser?: {
    max_steps: number
    task_timeout_seconds: number
    max_actions_per_step: number
    headless: boolean
    screenshots: boolean
    current_host: string | null
  }
}

export interface DurableTask {
  id: string
  goal: string
  state: 'queued' | 'running' | 'paused' | 'completed' | 'cancelled'
  slice_no: number
  last_answer: string
  error: string
  created_at: number
  updated_at: number
  operations?: Array<{ id: string; tool: string; state: string; result: string; started_at: number; finished_at: number | null }>
}

export interface FusionConfig {
  panel: string[]
  judge_provider: string
  judge_model: string
  max_panel: number
}

export interface FusionResult {
  query: string
  results: Array<{ provider: string; model: string; text: string; input_tokens: number; output_tokens: number; elapsed_s: number; error: string | null }>
  winner_index: number | null
  winner_answer: string
  verdict: string
  judge_error: string | null
  judge_parsed: boolean
  total_input_tokens: number
  total_output_tokens: number
  total_cost_usd: number | null
}

export interface DoctorCheck {
  model: string
  endpoint: string
  reachability: string
  authentication: string
  configuration: string
  sandbox: string
  capabilities: string
  web_search: string
  cli_anything: string
}

/** One entry of /api/doctor's `checks` (the same list /doctor prints). */
export interface DoctorCheckItem {
  name: string
  status: 'ok' | 'warn' | 'fail' | 'info'
  detail: string
  fix: string
  repair: string
}

export interface DoctorReport extends DoctorCheck {
  checks?: DoctorCheckItem[]
  /** Degradation registry rows (same shape as StatusInfo.capabilities). */
  limited?: CapabilityRow[]
  ok?: boolean
}

export interface McpServerInfo {
  name: string
  state: string
  tools: string[]
  error: string
}

export interface ArtifactItem {
  name: string
  path: string
  type: 'dir' | 'image' | 'text' | 'file'
  size: number | null
  modified: number
}

export interface ArtifactsListing {
  root: string
  cwd: string
  parent: string | null
  items: ArtifactItem[]
}

export interface SandboxStatus {
  resolved: string
  verification: string
  detail: string
}

export interface Capabilities {
  model: string
  permission_mode: string
  sandbox_backend: string
  max_turns: number
  tools: ToolSpec[]
  mcp_servers: McpServerInfo[]
  skills: SkillPackage[]
  subagents: SubagentSpec[]
  guardrails: string[]
}

export interface CommandInfo {
  name: string
  usage: string
  description: string
  aliases: string[]
}

// --- WebSocket protocol ---

export type ToolDiffLine = { text: string; tone: 'add' | 'del' | 'ctx' }

export type ToolDiffPayload = {
  file: string
  added: number
  removed: number
  lines: ToolDiffLine[]
  truncated: boolean
}

export type WSClientMessage =
  | { type: 'chat'; text: string; attachments?: string[] }
  | { type: 'command'; command: string; args?: string }
  | { type: 'interrupt' }
  | { type: 'approval'; approved: boolean; session_scope: boolean; id: string }
  | { type: 'plan_approval'; mode: string; id: string }

export type WSEvent =
  | { type: 'status'; data: StatusInfo }
  | { type: 'capabilities'; data: CapabilityRow[] }
  | { type: 'token'; kind: 'reasoning' | 'content'; delta: string }
  | { type: 'tool_start'; name: string }
  | { type: 'tool_result'; name: string; result: string; diff?: ToolDiffPayload }
  | { type: 'tool_calls'; calls: Array<{ name: string; args: Record<string, unknown> }> }
  | { type: 'spin'; message: string; elapsed: number; tokens: number }
  | { type: 'escalation'; tool_name: string; change_type: string; description: string; id: string }
  | { type: 'plan_approval'; plan: string; id: string }
  | { type: 'plan_step'; index: number; total: number; step_text: string; tool_name: string; status: 'pending' | 'running' | 'done' | 'failed'; result?: string }
  | { type: 'answer'; text: string; usage: { seconds: number; tokens: number; context?: number }; rate_limit_status: StatusInfo['rate_limit_status'] }
  | { type: 'turn_complete' }
  | { type: 'stream_reset' }
  | { type: 'interrupted'; elapsed: number; partial: string }
  | { type: 'error'; message: string }
  | { type: 'session_saved'; name: string }
  | { type: 'memory_captured'; facts: string[] }
  | { type: 'command_result'; command: string; result: string; structured?: unknown }
