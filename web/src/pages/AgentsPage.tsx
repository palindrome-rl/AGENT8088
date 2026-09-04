import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Bot, Rocket, X, Loader2, CheckCircle2, XCircle, Cpu, Shield, Terminal, Plus, Trash2,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import type { SubagentSpec } from '@/types/api'

/* ------------------------------------------------------------------ */
/* Types — the /api/agents endpoint omits system_prompt, so we narrow.  */
/* ------------------------------------------------------------------ */

type AgentProfile = Omit<SubagentSpec, 'system_prompt'>

interface AgentRunResult {
  agent: string
  result: unknown
}

/* ------------------------------------------------------------------ */
/* API helpers                                                         */
/* ------------------------------------------------------------------ */

async function fetchAgents(): Promise<AgentProfile[]> {
  const res = await fetch('/api/agents')
  if (!res.ok) throw new Error(`Failed to load sub-agents (${res.status})`)
  return res.json() as Promise<AgentProfile[]>
}

async function runAgent(name: string, task: string): Promise<AgentRunResult> {
  const res = await fetch(`/api/agent/${encodeURIComponent(name)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ task }),
  })
  if (!res.ok) throw new Error(`Agent launch failed (${res.status})`)
  return res.json() as Promise<AgentRunResult>
}

async function createAgent(body: Record<string, unknown>): Promise<void> {
  const res = await fetch('/api/agents', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const data = await res.json() as { error?: string }
  if (!res.ok || data.error) throw new Error(data.error ?? `Profile creation failed (${res.status})`)
}

async function deleteAgent(name: string): Promise<void> {
  const res = await fetch(`/api/agents/${encodeURIComponent(name)}`, { method: 'DELETE' })
  const data = await res.json() as { error?: string }
  if (!res.ok || data.error) throw new Error(data.error ?? `Profile deletion failed (${res.status})`)
}

/* ------------------------------------------------------------------ */
/* Presentational helpers                                              */
/* ------------------------------------------------------------------ */

function PermissionBadge({ permission }: { permission: string }) {
  const styles: Record<string, string> = {
    'full-auto': 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
    'readonly': 'bg-yellow-500/15 text-yellow-400 border-yellow-500/30',
    'plan-only': 'bg-purple-500/15 text-purple-400 border-purple-500/30',
  }
  const Icon = permission === 'full-auto' ? Cpu : permission === 'readonly' ? Shield : Terminal
  return (
    <span className={cn(
      'inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-medium',
      styles[permission] ?? 'bg-zinc-700/30 text-zinc-400 border-zinc-700',
    )}>
      <Icon className="h-2.5 w-2.5" />
      {permission || '—'}
    </span>
  )
}

/* ------------------------------------------------------------------ */
/* Launch modal                                                        */
/* ------------------------------------------------------------------ */

interface LaunchModalProps {
  agent: AgentProfile
  onClose: () => void
}

function LaunchModal({ agent, onClose }: LaunchModalProps) {
  const [task, setTask] = useState('')

  const mutation = useMutation({
    mutationFn: (t: string) => runAgent(agent.name, t),
  })

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!task.trim()) return
    mutation.mutate(task.trim())
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
         onClick={onClose}>
      <div className="w-full max-w-lg rounded-xl border border-zinc-800 bg-zinc-950 shadow-2xl"
           onClick={(e) => e.stopPropagation()}>
        {/* header */}
        <div className="flex items-center justify-between border-b border-zinc-800 px-5 py-3">
          <div className="flex items-center gap-2">
            <Bot className="h-4 w-4 text-brand-cyan" />
            <h3 className="text-sm font-semibold text-zinc-100">Launch {agent.name}</h3>
          </div>
          <button type="button" aria-label="Close agent launcher" onClick={onClose} className="text-zinc-500 hover:text-zinc-200">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* agent meta */}
        <div className="flex flex-wrap items-center gap-3 px-5 py-3 text-xs text-zinc-400">
          <PermissionBadge permission={agent.permission} />
          <span className="flex items-center gap-1">
            <Cpu className="h-3 w-3 text-zinc-500" />
            max {agent.max_turns} turns
          </span>
          <span className="flex items-center gap-1">
            <Terminal className="h-3 w-3 text-zinc-500" />
            {agent.tools.length} tools
          </span>
        </div>

        {/* description */}
        {agent.description && (
          <p className="px-5 py-1 text-xs leading-relaxed text-zinc-400">{agent.description}</p>
        )}

        {/* tools list */}
        {agent.tools.length > 0 && (
          <div className="px-5 py-2">
            <div className="mb-1 text-[10px] uppercase tracking-wide text-zinc-600">Allowed tools</div>
            <div className="flex flex-wrap gap-1">
              {agent.tools.map((t) => (
                <span key={t} className="rounded bg-zinc-800/60 px-1.5 py-0.5 font-mono text-[10px] text-zinc-400">
                  {t}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* form */}
        <form onSubmit={handleSubmit} className="space-y-3 px-5 py-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-zinc-300">Task</label>
            <textarea
              autoFocus
              value={task}
              onChange={(e) => setTask(e.target.value)}
              rows={3}
              placeholder={`Describe a task for ${agent.name}…`}
              className="w-full resize-none rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 outline-none transition-colors placeholder:text-zinc-600 focus:border-brand-primary"
            />
          </div>

          {/* result */}
          {mutation.isPending && (
            <div className="flex items-center gap-2 rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-xs text-zinc-400">
              <Loader2 className="h-3.5 w-3.5 animate-spin text-brand-cyan" />
              {agent.name} is working…
            </div>
          )}
          {mutation.isError && (
            <div className="flex items-start gap-2 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-400">
              <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span className="break-all">{(mutation.error as Error)?.message ?? 'Launch failed'}</span>
            </div>
          )}
          {mutation.isSuccess && (
            <div className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2">
              <div className="mb-1 flex items-center gap-1.5 text-xs font-medium text-emerald-400">
                <CheckCircle2 className="h-3.5 w-3.5" /> Result
              </div>
              <pre className="max-h-48 overflow-auto text-[11px] leading-relaxed text-zinc-300">
{typeof mutation.data?.result === 'string'
  ? mutation.data.result
  : JSON.stringify(mutation.data?.result, null, 2)}
              </pre>
            </div>
          )}

          {/* actions */}
          <div className="flex justify-end gap-2">
            <button type="button" onClick={onClose}
              className="rounded-md border border-zinc-800 px-3 py-1.5 text-xs text-zinc-400 hover:bg-zinc-800/50">
              Close
            </button>
            <button type="submit" disabled={mutation.isPending || !task.trim()}
              className="flex items-center gap-1.5 rounded-md bg-brand-primary px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-brand-primary/90 disabled:opacity-50">
              <Rocket className="h-3.5 w-3.5" />
              Launch
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* Page                                                                */
/* ------------------------------------------------------------------ */

export default function AgentsPage() {
  const queryClient = useQueryClient()
  const [launchAgent, setLaunchAgent] = useState<AgentProfile | null>(null)
  const [creating, setCreating] = useState(false)
  const [profile, setProfile] = useState({ name: '', description: '', model: 'inherit', prompt: '' })

  const { data: agents, isLoading, isError, error } = useQuery({
    queryKey: ['agents'],
    queryFn: fetchAgents,
  })
  const createMutation = useMutation({
    mutationFn: () => createAgent(profile),
    onSuccess: () => { setProfile({ name: '', description: '', model: 'inherit', prompt: '' }); setCreating(false); void queryClient.invalidateQueries({ queryKey: ['agents'] }) },
  })
  const deleteMutation = useMutation({
    mutationFn: deleteAgent,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['agents'] }),
  })

  return (
    <div className="mx-auto max-w-5xl p-6">
      {/* header */}
      <div className="mb-6 flex items-center gap-2.5">
        <Bot className="h-5 w-5 text-brand-cyan" />
        <h1 className="text-lg font-semibold text-zinc-100">Sub-Agents</h1>
        {agents && (
          <span className="rounded-full bg-zinc-800 px-2 py-0.5 text-[11px] text-zinc-400">
            {agents.length}
          </span>
        )}
        <button onClick={() => setCreating((open) => !open)} className="ml-auto inline-flex items-center gap-1.5 rounded-md border border-zinc-700 px-2.5 py-1.5 text-xs text-zinc-300 hover:border-brand-primary hover:text-brand-cyan"><Plus className="h-3.5 w-3.5" />New profile</button>
      </div>

      {creating && <form onSubmit={(event) => { event.preventDefault(); if (profile.name && profile.prompt) createMutation.mutate() }} className="mb-5 grid gap-3 rounded-xl border border-zinc-800 bg-zinc-900/50 p-4 sm:grid-cols-2">
        <input aria-label="Profile name" value={profile.name} onChange={(event) => setProfile((current) => ({ ...current, name: event.target.value }))} placeholder="profile-name" className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600" />
        <input aria-label="Profile description" value={profile.description} onChange={(event) => setProfile((current) => ({ ...current, description: event.target.value }))} placeholder="Description" className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600" />
        <input aria-label="Profile model" value={profile.model} onChange={(event) => setProfile((current) => ({ ...current, model: event.target.value }))} placeholder="inherit" className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 font-mono text-sm text-zinc-100 placeholder:text-zinc-600" />
        <textarea aria-label="Profile prompt" value={profile.prompt} onChange={(event) => setProfile((current) => ({ ...current, prompt: event.target.value }))} placeholder="System prompt" rows={2} className="rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600" />
        {createMutation.error && <p className="sm:col-span-2 text-xs text-red-400">{(createMutation.error as Error).message}</p>}
        <button disabled={!profile.name || !profile.prompt || createMutation.isPending} className="inline-flex w-fit items-center gap-1.5 rounded-lg bg-brand-primary px-3 py-2 text-xs font-medium text-white disabled:opacity-40">{createMutation.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}Create custom profile</button>
      </form>}

      {/* loading */}
      {isLoading && (
        <div className="flex items-center justify-center py-20">
          <Loader2 className="h-6 w-6 animate-spin text-brand-cyan" />
        </div>
      )}

      {/* error */}
      {isError && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-400">
          {(error as Error)?.message ?? 'Failed to load sub-agents.'}
        </div>
      )}

      {/* cards */}
      {agents && agents.length > 0 && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {agents.map((agent) => (
            <div key={agent.name}
              className="group flex flex-col rounded-lg border border-zinc-800 bg-zinc-900/30 p-4 transition-colors hover:border-brand-primary/40">
              {/* title */}
              <div className="mb-2 flex items-center gap-2">
                <div className="flex h-8 w-8 items-center justify-center rounded-md bg-brand-primary/10 text-brand-cyan">
                  <Bot className="h-4 w-4" />
                </div>
                <h3 className="font-mono text-sm font-medium text-zinc-100">{agent.name}</h3>
              </div>

              {/* description */}
              <p className="mb-3 line-clamp-3 flex-1 text-xs leading-relaxed text-zinc-400">
                {agent.description || 'No description'}
              </p>

              {/* meta */}
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <PermissionBadge permission={agent.permission} />
                <span className="rounded bg-zinc-800/60 px-1.5 py-0.5 text-[10px] text-zinc-500">
                  {agent.max_turns} turns
                </span>
                <span className="rounded bg-zinc-800/60 px-1.5 py-0.5 text-[10px] text-zinc-500">
                  {agent.tools.length} tools
                </span>
                <span className="rounded bg-zinc-800/60 px-1.5 py-0.5 font-mono text-[10px] text-zinc-500" title="Sub-agent model routing">
                  {agent.model || 'inherit'}
                </span>
                {agent.builtin === false && <span className="rounded bg-brand-primary/10 px-1.5 py-0.5 text-[10px] text-brand-cyan">custom</span>}
              </div>

              {/* tool chips */}
              {agent.tools.length > 0 && (
                <div className="mb-3 flex flex-wrap gap-1">
                  {agent.tools.slice(0, 4).map((t) => (
                    <span key={t} className="rounded bg-zinc-800/40 px-1.5 py-0.5 font-mono text-[10px] text-zinc-500">
                      {t}
                    </span>
                  ))}
                  {agent.tools.length > 4 && (
                    <span className="text-[10px] text-zinc-600">+{agent.tools.length - 4}</span>
                  )}
                </div>
              )}

              {/* launch */}
              <button
                onClick={() => setLaunchAgent(agent)}
                className="flex items-center justify-center gap-1.5 rounded-md border border-zinc-700 bg-zinc-800/50 px-3 py-1.5 text-xs font-medium text-zinc-200 transition-colors hover:border-brand-primary hover:text-brand-cyan"
              >
                <Rocket className="h-3.5 w-3.5" />
                Launch
              </button>
              {agent.builtin === false && <button onClick={() => { if (window.confirm(`Delete custom profile ${agent.name}?`)) deleteMutation.mutate(agent.name) }} disabled={deleteMutation.isPending} className="mt-2 inline-flex items-center justify-center gap-1.5 rounded-md border border-red-500/30 px-3 py-1.5 text-xs text-red-400 hover:bg-red-500/10 disabled:opacity-40"><Trash2 className="h-3.5 w-3.5" />Delete</button>}
            </div>
          ))}
        </div>
      )}

      {/* empty */}
      {agents && agents.length === 0 && (
        <div className="py-20 text-center text-sm text-zinc-500">
          No sub-agent profiles configured.
        </div>
      )}

      {/* launch modal */}
      {launchAgent && <LaunchModal agent={launchAgent} onClose={() => setLaunchAgent(null)} />}
    </div>
  )
}
