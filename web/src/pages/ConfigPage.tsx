import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Settings, Loader2, AlertCircle, ChevronDown, Check,
  Sliders, Gauge, Shield, FileText, Server, Activity,
} from 'lucide-react'
import type { ConfigInfo, ProviderInfo } from '@/types/api'
import { cn } from '@/lib/utils'

// ---- API helpers ----

async function fetchJSON<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json() as Promise<T>
}

async function postJSON<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json() as Promise<T>
}

interface ProvidersResponse {
  configured: string[]
  builtins: string[]
  active: string
  details: Record<string, {
    label: string
    base_url: string
    default_model: string
    api_key_env: string
  }>
}

interface ModelsResponse {
  provider: string
  models: string[]
}

interface ModelSwitchResponse {
  ok: boolean
  provider?: string
  model?: string
  error?: string
}

interface ActionResponse {
  ok?: boolean
  error?: string
  key?: string
  old?: number
  new?: number
  mode?: string
}

interface LimitsResponse {
  max_turns: number
  max_turn_seconds: number
  max_turn_tokens: number
  max_turn_cost_usd: number
  max_writes_per_turn: number
  max_write_bytes: number
  max_tool_timeout_seconds: number
  max_subagent_answer_chars: number
  denial_breaker_threshold: number
  context_window: number
  max_completion_tokens: number
}

interface SandboxResponse {
  resolved: string
  verification: string
  detail?: string
}

interface CapabilitiesResponse {
  report: string
}

// ---- Section wrapper ----

function Section({ icon: Icon, title, subtitle, children }: {
  icon: React.ComponentType<{ className?: string }>
  title: string
  subtitle?: string
  children: React.ReactNode
}) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
      <div className="mb-4 flex items-center gap-2">
        <Icon className="h-4 w-4 text-brand-cyan" />
        <div>
          <h3 className="text-sm font-semibold text-zinc-200">{title}</h3>
          {subtitle && <p className="text-xs text-zinc-500">{subtitle}</p>}
        </div>
      </div>
      {children}
    </div>
  )
}

// ---- Info Row ----

function InfoRow({ label, value, mono }: { label: string; value: string | number; mono?: boolean }) {
  return (
    <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b border-zinc-800/60 py-2 text-sm last:border-0">
      <span className="min-w-0 truncate text-zinc-500" title={label}>{label}</span>
      <span className={cn('min-w-0 max-w-full break-all text-right text-zinc-200', mono && 'font-mono text-xs')} title={String(value)}>{value}</span>
    </div>
  )
}

// ---- Model Switcher ----

function ModelSwitcher({ providers, config, onSwitch, switching }: {
  providers: ProvidersResponse | undefined
  config: ConfigInfo | undefined
  onSwitch: (provider: string, model: string) => void
  switching: boolean
}) {
  const [selectedProvider, setSelectedProvider] = useState('')
  const [selectedModel, setSelectedModel] = useState('')

  // Models for selected provider
  const modelsQuery = useQuery({
    queryKey: ['models', selectedProvider],
    queryFn: () => fetchJSON<ModelsResponse>(`/api/models/${selectedProvider}`),
    enabled: selectedProvider.length > 0,
  })

  const activeProvider = providers?.active ?? config?.active_provider ?? ''
  const activeModel = config?.model_name ?? ''

  const providerOptions = providers
    ? [...new Set([...providers.configured, ...providers.builtins])].sort()
    : []

  return (
    <div className="space-y-4">
      {/* Current model */}
      <div className="flex items-center gap-2 rounded-lg border border-brand-border/30 bg-brand-primary/5 px-3 py-2.5">
        <Server className="h-4 w-4 text-brand-cyan" />
        <span className="text-sm text-zinc-400">Active:</span>
        <span className="font-mono text-sm text-brand-cyan">{activeProvider}:{activeModel}</span>
      </div>

      {/* Provider dropdown */}
      <div>
        <label className="mb-1.5 block text-xs text-zinc-500">Provider</label>
        <div className="relative">
          <select
            value={selectedProvider}
            onChange={(e) => {
              setSelectedProvider(e.target.value)
              setSelectedModel('')
            }}
            className="w-full appearance-none rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 focus:border-brand-primary/50 focus:outline-none"
          >
            <option value="">Select provider…</option>
            {providerOptions.map((p) => (
              <option key={p} value={p} className="bg-zinc-900">
                {p} {providers?.active === p ? '✓' : ''}
              </option>
            ))}
          </select>
          <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-600" />
        </div>
      </div>

      {/* Model dropdown */}
      {selectedProvider && (
        <div>
          <label className="mb-1.5 block text-xs text-zinc-500">Model</label>
          {modelsQuery.isLoading ? (
            <div className="flex items-center gap-2 text-xs text-zinc-500">
              <Loader2 className="h-3.5 w-3.5 animate-spin text-brand-cyan" /> Fetching models…
            </div>
          ) : modelsQuery.isError ? (
            <div className="flex items-center gap-2 text-xs text-red-400">
              <AlertCircle className="h-3.5 w-3.5" /> Failed to load models
            </div>
          ) : (
            <div className="relative">
              <select
                value={selectedModel}
                onChange={(e) => setSelectedModel(e.target.value)}
                className="w-full appearance-none rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 focus:border-brand-primary/50 focus:outline-none"
              >
                <option value="">Select model…</option>
                {modelsQuery.data?.models.map((m) => (
                  <option key={m} value={m} className="bg-zinc-900">{m}</option>
                ))}
              </select>
              <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-600" />
            </div>
          )}
        </div>
      )}

      {/* Switch button */}
      <button
        onClick={() => onSwitch(selectedProvider, selectedModel)}
        disabled={switching || !selectedProvider || !selectedModel}
        className="flex w-full items-center justify-center gap-2 rounded-lg bg-brand-primary px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-primary/80 disabled:cursor-not-allowed disabled:opacity-40"
      >
        {switching ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
        Switch Model
      </button>
    </div>
  )
}

// ---- Preferences Panel ----

function PreferencesPanel({ onSet, setting }: {
  onSet: (prefs: Record<string, unknown>) => void
  setting: boolean
}) {
  const [temperature, setTemperature] = useState(0.1)
  const [maxTurns, setMaxTurns] = useState(10)
  const [verbose, setVerbose] = useState('on')
  const [usageMode, setUsageMode] = useState('tokens')
  const [showTrace, setShowTrace] = useState(false)
  const [showReasoning, setShowReasoning] = useState(false)

  const save = () => {
    onSet({
      temperature,
      max_turns: maxTurns,
      verbose,
      usage_mode: usageMode,
      show_trace: showTrace,
      show_reasoning: showReasoning,
    })
  }

  return (
    <div className="space-y-4">
      {/* Temperature */}
      <div>
        <label className="mb-1.5 flex items-center justify-between text-xs text-zinc-500">
          <span>Temperature</span>
          <span className="font-mono text-zinc-300">{temperature.toFixed(2)}</span>
        </label>
        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={temperature}
          onChange={(e) => setTemperature(Number(e.target.value))}
          className="w-full accent-brand-primary"
        />
      </div>

      {/* Max turns */}
      <div>
        <label className="mb-1.5 block text-xs text-zinc-500">Max Turns</label>
        <input
          type="number"
          min={1}
          max={100}
          value={maxTurns}
          onChange={(e) => setMaxTurns(Number(e.target.value))}
          className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 focus:border-brand-primary/50 focus:outline-none"
        />
      </div>

      {/* Verbose */}
      <div>
        <label className="mb-1.5 block text-xs text-zinc-500">Verbose</label>
        <div className="flex gap-1.5">
          {(['on', 'off', 'full'] as const).map((v) => (
            <button
              key={v}
              onClick={() => setVerbose(v)}
              className={cn(
                'flex-1 rounded-lg border px-2 py-1.5 text-xs font-medium transition-colors',
                verbose === v
                  ? 'border-brand-primary/50 bg-brand-primary/15 text-brand-cyan'
                  : 'border-zinc-800 bg-zinc-950 text-zinc-400 hover:border-zinc-700',
              )}
            >
              {v}
            </button>
          ))}
        </div>
      </div>

      {/* Usage mode */}
      <div>
        <label className="mb-1.5 block text-xs text-zinc-500">Usage Mode</label>
        <div className="flex gap-1.5">
          {(['off', 'tokens', 'full'] as const).map((m) => (
            <button
              key={m}
              onClick={() => setUsageMode(m)}
              className={cn(
                'flex-1 rounded-lg border px-2 py-1.5 text-xs font-medium transition-colors',
                usageMode === m
                  ? 'border-brand-primary/50 bg-brand-primary/15 text-brand-cyan'
                  : 'border-zinc-800 bg-zinc-950 text-zinc-400 hover:border-zinc-700',
              )}
            >
              {m}
            </button>
          ))}
        </div>
      </div>

      {/* Toggles */}
      <div className="space-y-2">
        <label className="flex items-center justify-between text-sm text-zinc-300">
          <span>Show trace</span>
          <button
            onClick={() => setShowTrace(!showTrace)}
            className={cn(
              'relative h-6 w-11 rounded-full transition-colors',
              showTrace ? 'bg-brand-primary' : 'bg-zinc-700',
            )}
          >
            <span className={cn(
              'absolute top-0.5 h-5 w-5 rounded-full bg-white transition-transform',
              showTrace ? 'translate-x-5' : 'translate-x-0.5',
            )} />
          </button>
        </label>
        <label className="flex items-center justify-between text-sm text-zinc-300">
          <span>Show reasoning</span>
          <button
            onClick={() => setShowReasoning(!showReasoning)}
            className={cn(
              'relative h-6 w-11 rounded-full transition-colors',
              showReasoning ? 'bg-brand-primary' : 'bg-zinc-700',
            )}
          >
            <span className={cn(
              'absolute top-0.5 h-5 w-5 rounded-full bg-white transition-transform',
              showReasoning ? 'translate-x-5' : 'translate-x-0.5',
            )} />
          </button>
        </label>
      </div>

      {/* Save */}
      <button
        onClick={save}
        disabled={setting}
        className="flex w-full items-center justify-center gap-2 rounded-lg bg-brand-primary px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-primary/80 disabled:opacity-40"
      >
        {setting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
        Save Preferences
      </button>
    </div>
  )
}

// ---- Limits Panel ----

function LimitsPanel({ limits, onSetLimit, setting }: {
  limits: LimitsResponse | undefined
  onSetLimit: (key: string, value: string) => void
  setting: boolean
}) {
  const [editKey, setEditKey] = useState('')
  const [editValue, setEditValue] = useState('')

  if (!limits) return null

  const limitEntries: Array<[string, string | number]> = [
    ['max_turns', limits.max_turns],
    ['max_turn_seconds', limits.max_turn_seconds],
    ['max_turn_tokens', limits.max_turn_tokens],
    ['max_turn_cost_usd', limits.max_turn_cost_usd],
    ['max_writes_per_turn', limits.max_writes_per_turn],
    ['max_write_bytes', limits.max_write_bytes],
    ['max_tool_timeout_seconds', limits.max_tool_timeout_seconds],
    ['max_subagent_answer_chars', limits.max_subagent_answer_chars],
    ['denial_breaker_threshold', limits.denial_breaker_threshold],
    ['context_window', limits.context_window],
    ['max_completion_tokens', limits.max_completion_tokens],
  ]

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-x-8 gap-y-0 sm:grid-cols-2">
        {limitEntries.map(([key, value]) => (
          <InfoRow key={key} label={key} value={value} mono />
        ))}
      </div>

      {/* Set limit */}
      <div className="flex gap-2 border-t border-zinc-800 pt-4">
        <select
          value={editKey}
          onChange={(e) => setEditKey(e.target.value)}
          className="w-1/2 appearance-none rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 focus:border-brand-primary/50 focus:outline-none"
        >
          <option value="">key…</option>
          {limitEntries.map(([key]) => (
            <option key={key} value={key} className="bg-zinc-900">{key}</option>
          ))}
        </select>
        <input
          type="text"
          value={editValue}
          onChange={(e) => setEditValue(e.target.value)}
          placeholder="value"
          className="flex-1 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-brand-primary/50 focus:outline-none"
        />
        <button
          onClick={() => {
            if (editKey && editValue) onSetLimit(editKey, editValue)
          }}
          disabled={setting || !editKey || !editValue}
          className="rounded-lg bg-brand-primary px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-primary/80 disabled:opacity-40"
        >
          {setting ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Set'}
        </button>
      </div>
    </div>
  )
}

// ---- Mode Selector ----

function ModeSelector({ onSet, setting }: {
  onSet: (mode: string) => void
  setting: boolean
}) {
  const modes = [
    { value: 'readonly', label: 'Read-Only', color: 'yellow' },
    { value: 'full-auto', label: 'Full-Auto', color: 'green' },
  ] as const

  return (
    <div className="flex gap-2">
      {modes.map((m) => (
        <button
          key={m.value}
          onClick={() => onSet(m.value)}
          disabled={setting}
          className={cn(
            'flex-1 rounded-lg border px-3 py-2 text-sm font-medium transition-colors disabled:opacity-40',
            m.color === 'yellow'
              ? 'border-yellow-900/40 bg-yellow-500/10 text-yellow-400 hover:bg-yellow-500/15'
              : 'border-green-900/40 bg-green-500/10 text-green-400 hover:bg-green-500/15',
          )}
        >
          {m.label}
        </button>
      ))}
    </div>
  )
}

// ---- Audit Toggle ----

function AuditToggle({ onToggle, toggling }: { onToggle: (enable: boolean) => void; toggling: boolean }) {
  const [enabled, setEnabled] = useState(false)
  return (
    <label className="flex items-center justify-between text-sm text-zinc-300">
      <span>Plan auditing</span>
      <button
        onClick={() => {
          const next = !enabled
          setEnabled(next)
          onToggle(next)
        }}
        disabled={toggling}
        className={cn(
          'relative h-6 w-11 rounded-full transition-colors disabled:opacity-40',
          enabled ? 'bg-brand-primary' : 'bg-zinc-700',
        )}
      >
        {toggling ? (
          <Loader2 className="absolute left-2 top-0.5 h-5 w-5 animate-spin text-white" />
        ) : (
          <span className={cn(
            'absolute top-0.5 h-5 w-5 rounded-full bg-white transition-transform',
            enabled ? 'translate-x-5' : 'translate-x-0.5',
          )} />
        )}
      </button>
    </label>
  )
}

// ---- Sandbox Panel ----

function SandboxPanel({ sandbox, onSetMode, setting }: {
  sandbox: SandboxResponse | undefined
  onSetMode: (mode: string) => void
  setting: boolean
}) {
  const modes = ['auto', 'native', 'docker', 'local'] as const
  const resolved = sandbox?.resolved ?? '—'
  const verification = sandbox?.verification ?? ''
  const healthy = verification === 'ok' || verification === 'verified'

  return (
    <div className="space-y-4">
      {/* Current backend with health indicator */}
      <div className="flex items-center gap-2 rounded-lg border border-brand-border/30 bg-brand-primary/5 px-3 py-2.5">
        <span
          className={cn(
            'h-2.5 w-2.5 rounded-full',
            healthy ? 'bg-green-500' : verification === 'error' || verification === 'failed' ? 'bg-red-500' : 'bg-yellow-500',
          )}
        />
        <span className="text-sm text-zinc-400">Backend:</span>
        <span className="font-mono text-sm text-brand-cyan">{resolved}</span>
        {verification && (
          <span className={cn(
            'ml-auto text-xs',
            healthy ? 'text-green-400' : verification === 'error' || verification === 'failed' ? 'text-red-400' : 'text-yellow-400',
          )}>
            {verification}
          </span>
        )}
      </div>

      {sandbox?.detail && (
        <p className="text-xs text-zinc-500">{sandbox.detail}</p>
      )}

      {/* Mode buttons */}
      <div className="flex gap-2">
        {modes.map((m) => (
          <button
            key={m}
            onClick={() => onSetMode(m)}
            disabled={setting}
            className={cn(
              'flex-1 rounded-lg border px-3 py-2 text-sm font-medium transition-colors disabled:opacity-40',
              resolved === m || resolved === `sandbox:${m}`
                ? 'border-brand-primary/50 bg-brand-primary/15 text-brand-cyan'
                : 'border-zinc-800 bg-zinc-950 text-zinc-400 hover:border-zinc-700',
            )}
          >
            {m}
          </button>
        ))}
      </div>
    </div>
  )
}

// ---- Provider Card ----

function ProviderCard({ name, info }: { name: string; info: ProviderInfo }) {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-950/50 p-3">
      <div className="mb-2 flex items-center gap-2">
        <Server className="h-3.5 w-3.5 text-zinc-600" />
        <span className="font-mono text-sm text-zinc-200">{name}</span>
      </div>
      <div className="space-y-1 text-xs text-zinc-500">
        <div className="truncate"><span className="text-zinc-600">url:</span> {info.base_url}</div>
        <div><span className="text-zinc-600">model:</span> {info.model}</div>
        {info.api_key_env && <div><span className="text-zinc-600">key_env:</span> {info.api_key_env}</div>}
        {info.api_mode && <div><span className="text-zinc-600">mode:</span> {info.api_mode}</div>}
      </div>
    </div>
  )
}

// ---- Main Page ----

export default function ConfigPage() {
  const queryClient = useQueryClient()

  // Config
  const configQuery = useQuery({
    queryKey: ['config'],
    queryFn: () => fetchJSON<ConfigInfo>('/api/config'),
  })

  // Providers
  const providersQuery = useQuery({
    queryKey: ['providers'],
    queryFn: () => fetchJSON<ProvidersResponse>('/api/providers'),
  })

  // Limits
  const limitsQuery = useQuery({
    queryKey: ['limits'],
    queryFn: () => fetchJSON<LimitsResponse>('/api/limits'),
  })

  // Model switch
  const switchMutation = useMutation({
    mutationFn: ({ provider, model }: { provider: string; model: string }) =>
      postJSON<ModelSwitchResponse>('/api/model/switch', { provider, model }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['config'] })
      queryClient.invalidateQueries({ queryKey: ['providers'] })
    },
  })

  // Preferences
  const prefMutation = useMutation({
    mutationFn: (prefs: Record<string, unknown>) => postJSON<ActionResponse>('/api/preferences', prefs),
  })

  // Limits
  const limitMutation = useMutation({
    mutationFn: ({ key, value }: { key: string; value: string }) =>
      postJSON<ActionResponse>('/api/limits', { key, value }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['limits'] }),
  })

  // Mode
  const modeMutation = useMutation({
    mutationFn: (mode: string) => postJSON<ActionResponse>('/api/mode', { mode }),
  })

  // Audit
  const auditMutation = useMutation({
    mutationFn: (enable: boolean) => postJSON<ActionResponse>('/api/audit', { enable }),
  })

  // Sandbox
  const sandboxQuery = useQuery({
    queryKey: ['sandbox'],
    queryFn: () => fetchJSON<SandboxResponse>('/api/sandbox'),
  })

  const sandboxMutation = useMutation({
    mutationFn: (mode: string) => postJSON<ActionResponse>('/api/sandbox', { mode }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['sandbox'] }),
  })

  // Capabilities
  const capabilitiesQuery = useQuery({
    queryKey: ['capabilities'],
    queryFn: () => fetchJSON<CapabilitiesResponse>('/api/capabilities'),
  })

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Settings className="h-6 w-6 text-brand-cyan" />
        <div>
          <h1 className="text-lg font-semibold text-zinc-100">Config</h1>
          <p className="text-xs text-zinc-500">Model, preferences, limits & runtime settings</p>
        </div>
      </div>

      {/* Errors */}
      {configQuery.isError && (
        <div className="flex items-center gap-2 rounded-lg border border-red-900/50 bg-red-950/30 p-3 text-xs text-red-400">
          <AlertCircle className="h-3.5 w-3.5 shrink-0" /> Failed to load config
        </div>
      )}

      {switchMutation.data?.error && (
        <div className="flex items-center gap-2 rounded-lg border border-red-900/50 bg-red-950/30 p-3 text-xs text-red-400">
          <AlertCircle className="h-3.5 w-3.5 shrink-0" /> Model switch failed: {switchMutation.data.error}
        </div>
      )}
      {switchMutation.data?.ok && !switchMutation.data?.error && (
        <div className="flex items-center gap-2 rounded-lg border border-green-900/50 bg-green-950/20 p-3 text-xs text-green-400">
          <Check className="h-3.5 w-3.5" /> Switched to {switchMutation.data.provider}:{switchMutation.data.model}
        </div>
      )}

      {prefMutation.data?.ok && (
        <div className="flex items-center gap-2 rounded-lg border border-green-900/50 bg-green-950/20 p-3 text-xs text-green-400">
          <Check className="h-3.5 w-3.5" /> Preferences saved
        </div>
      )}

      {limitMutation.data?.ok && (
        <div className="flex items-center gap-2 rounded-lg border border-green-900/50 bg-green-950/20 p-3 text-xs text-green-400">
          <Check className="h-3.5 w-3.5" /> {limitMutation.data.key} → {limitMutation.data.new}
        </div>
      )}

      {modeMutation.data?.ok && (
        <div className="flex items-center gap-2 rounded-lg border border-green-900/50 bg-green-950/20 p-3 text-xs text-green-400">
          <Check className="h-3.5 w-3.5" /> Mode: {modeMutation.data.mode}
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Left column */}
        <div className="space-y-6">
          {/* Model Switcher */}
          <Section icon={Server} title="Model Switcher" subtitle="Change active provider & model">
            {providersQuery.isLoading || configQuery.isLoading ? (
              <div className="flex items-center gap-2 text-sm text-zinc-400">
                <Loader2 className="h-4 w-4 animate-spin text-brand-cyan" /> Loading…
              </div>
            ) : (
              <ModelSwitcher
                providers={providersQuery.data}
                config={configQuery.data}
                onSwitch={(provider, model) => switchMutation.mutate({ provider, model })}
                switching={switchMutation.isPending}
              />
            )}
          </Section>

          {/* Mode Selector */}
          <Section icon={Shield} title="Permission Mode" subtitle="Control agent autonomy">
            <ModeSelector onSet={(mode) => modeMutation.mutate(mode)} setting={modeMutation.isPending} />
          </Section>

          {/* Audit Toggle */}
          <Section icon={FileText} title="Plan Auditing" subtitle="Record executed plans for review">
            <AuditToggle onToggle={(enable) => auditMutation.mutate(enable)} toggling={auditMutation.isPending} />
            {auditMutation.data?.error && (
              <p className="mt-2 text-xs text-red-400">{auditMutation.data.error}</p>
            )}
            {auditMutation.data?.ok && (
              <p className="mt-2 text-xs text-green-400">✓ Updated</p>
            )}
          </Section>

          {/* Sandbox */}
          <Section icon={Shield} title="Sandbox" subtitle="Execution isolation backend">
            {sandboxQuery.isLoading ? (
              <div className="flex items-center gap-2 text-sm text-zinc-400">
                <Loader2 className="h-4 w-4 animate-spin text-brand-cyan" /> Loading sandbox…
              </div>
            ) : sandboxQuery.isError ? (
              <div className="flex items-center gap-2 text-sm text-red-400">
                <AlertCircle className="h-4 w-4" /> Failed to load sandbox
              </div>
            ) : (
              <SandboxPanel
                sandbox={sandboxQuery.data}
                onSetMode={(mode) => sandboxMutation.mutate(mode)}
                setting={sandboxMutation.isPending}
              />
            )}
            {sandboxMutation.data?.error && (
              <p className="mt-2 text-xs text-red-400">{sandboxMutation.data.error}</p>
            )}
            {sandboxMutation.data?.ok && (
              <p className="mt-2 text-xs text-green-400">✓ Sandbox mode: {sandboxMutation.data.mode}</p>
            )}
          </Section>
        </div>

        {/* Right column */}
        <div className="space-y-6">
          {/* Preferences */}
          <Section icon={Sliders} title="Preferences" subtitle="Session-level settings">
            <PreferencesPanel
              onSet={(prefs) => prefMutation.mutate(prefs)}
              setting={prefMutation.isPending}
            />
          </Section>

          {/* Limits */}
          <Section icon={Gauge} title="Limits" subtitle="Runtime guardrails & caps">
            {limitsQuery.isLoading ? (
              <div className="flex items-center gap-2 text-sm text-zinc-400">
                <Loader2 className="h-4 w-4 animate-spin text-brand-cyan" /> Loading limits…
              </div>
            ) : limitsQuery.isError ? (
              <div className="flex items-center gap-2 text-sm text-red-400">
                <AlertCircle className="h-4 w-4" /> Failed to load limits
              </div>
            ) : (
              <LimitsPanel
                limits={limitsQuery.data}
                onSetLimit={(key, value) => limitMutation.mutate({ key, value })}
                setting={limitMutation.isPending}
              />
            )}
            {limitMutation.data?.error && (
              <p className="mt-2 text-xs text-red-400">{limitMutation.data.error}</p>
            )}
          </Section>
        </div>
      </div>

      {/* Config details */}
      {configQuery.data && (
        <Section icon={Settings} title="Configuration Details" subtitle="Active config paths & settings">
          <div className="grid grid-cols-1 gap-x-8 gap-y-0 sm:grid-cols-2">
            <InfoRow label="Model name" value={configQuery.data.model_name} mono />
            <InfoRow label="Base URL" value={configQuery.data.model_base_url} mono />
            <InfoRow label="Default provider" value={configQuery.data.default_provider} mono />
            <InfoRow label="Active provider" value={configQuery.data.active_provider} mono />
            <InfoRow label="Context window" value={configQuery.data.context_window} mono />
            <InfoRow label="Max turns" value={configQuery.data.max_turns} mono />
            <InfoRow label="Temperature" value={configQuery.data.temperature} mono />
            <InfoRow label="Config path" value={configQuery.data.config_path} mono />
            <InfoRow label="Tools file" value={configQuery.data.tools_file} mono />
            <InfoRow label="System file" value={configQuery.data.system_file} mono />
            <InfoRow label="Skills dir" value={configQuery.data.skills_dir} mono />
            <InfoRow label="Agents dir" value={configQuery.data.agents_dir} mono />
            <InfoRow label="Project root" value={configQuery.data.project_root} mono />
            <InfoRow label="Artifacts root" value={configQuery.data.artifacts_root} mono />
            <InfoRow label="Shell CWD" value={configQuery.data.shell_cwd} mono />
          </div>
        </Section>
      )}

      {/* Providers */}
      {configQuery.data && Object.keys(configQuery.data.providers).length > 0 && (
        <Section icon={Server} title="Configured Providers" subtitle="All provider connections">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {Object.entries(configQuery.data.providers).map(([name, info]) => (
              <ProviderCard key={name} name={name} info={info} />
            ))}
          </div>
        </Section>
      )}

      {/* Capabilities */}
      <Section icon={Activity} title="Capabilities" subtitle="Runtime capability report">
        {capabilitiesQuery.isLoading ? (
          <div className="flex items-center gap-2 text-sm text-zinc-400">
            <Loader2 className="h-4 w-4 animate-spin text-brand-cyan" /> Loading capabilities…
          </div>
        ) : capabilitiesQuery.isError ? (
          <div className="flex items-center gap-2 text-sm text-red-400">
            <AlertCircle className="h-4 w-4" /> Failed to load capabilities
          </div>
        ) : (
          <pre className="max-h-96 overflow-auto rounded-lg border border-zinc-800 bg-zinc-950 p-4 text-xs text-zinc-300 whitespace-pre-wrap break-words font-mono">
            {capabilitiesQuery.data?.report}
          </pre>
        )}
      </Section>
    </div>
  )
}
