// web/src/types/api.ts

// --- Engine state ---

export interface ProviderInfo {
  base_url: string
  api_key?: string
  api_key_env?: string
  model: string
  native_tools?: boolean
  api_mode?: string
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
  enabled: boolean
}

export interface SkillPackage {
  name: string
  description: string
  resources: string[]
  enabled: boolean
}

export interface SubagentSpec {
  name: string
  description: string
  tools: string[]
  max_turns: number
  permission: string
  system_prompt: string
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
  verbose: string
  usage_mode: string
  show_trace: boolean
  show_reasoning: boolean
  temperature: number
  max_turns: number
  disabled_skills: string[]
}

export interface ConfigInfo {
  model_name: string
  model_base_url: string
  default_provider: string
  active_provider: string
  config_path: string
  context_window: number
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

export interface McpServerInfo {
  name: string
  state: string
  tools: string[]
  error: string
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

// --- WebSocket protocol ---

export type WSClientMessage =
  | { type: 'chat'; text: string }
  | { type: 'command'; command: string; args?: string }
  | { type: 'interrupt' }
  | { type: 'approval'; approved: boolean; session_scope: boolean; id: string }
  | { type: 'plan_approval'; mode: string; id: string }

export type WSEvent =
  | { type: 'status'; data: StatusInfo }
  | { type: 'token'; kind: 'reasoning' | 'content'; delta: string }
  | { type: 'tool_start'; name: string }
  | { type: 'tool_result'; name: string; result: string }
  | { type: 'tool_calls'; calls: Array<{ name: string; args: Record<string, unknown> }> }
  | { type: 'spin'; message: string; elapsed: number; tokens: number }
  | { type: 'escalation'; tool_name: string; change_type: string; description: string; id: string }
  | { type: 'plan_approval'; plan: string; id: string }
  | { type: 'plan_step'; index: number; total: number; step_text: string; tool_name: string; status: 'pending' | 'running' | 'done' | 'failed'; result?: string }
  | { type: 'answer'; text: string; usage: { seconds: number; tokens: number; context?: number } }
  | { type: 'interrupted'; elapsed: number; partial: string }
  | { type: 'error'; message: string }
  | { type: 'session_saved'; name: string }
  | { type: 'memory_captured'; facts: string[] }
  | { type: 'command_result'; command: string; result: string; structured?: unknown }