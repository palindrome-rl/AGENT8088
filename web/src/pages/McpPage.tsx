import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Network, RefreshCw, Plus, Trash2, X, Loader2, CheckCircle2, AlertCircle,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import type { McpServerInfo } from '@/types/api'

/* ------------------------------------------------------------------ */
/* API helpers                                                         */
/* ------------------------------------------------------------------ */

async function fetchMcp(): Promise<McpServerInfo[]> {
  const res = await fetch('/api/mcp')
  if (!res.ok) throw new Error(`Failed to load MCP servers (${res.status})`)
  return res.json() as Promise<McpServerInfo[]>
}

async function reloadMcp(): Promise<{ ok: boolean }> {
  const res = await fetch('/api/mcp/reload', { method: 'POST' })
  if (!res.ok) throw new Error(`Reload failed (${res.status})`)
  return res.json() as Promise<{ ok: boolean }>
}

interface McpAddBody {
  name: string
  transport: 'stdio' | 'http'
  command?: string
  url?: string
  project?: boolean
}

async function addMcp(body: McpAddBody): Promise<{ ok?: boolean; error?: string }> {
  const res = await fetch('/api/mcp/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`Add failed (${res.status})`)
  return res.json() as Promise<{ ok?: boolean; error?: string }>
}

async function removeMcp(name: string, project: boolean): Promise<{ ok?: boolean; error?: string }> {
  const res = await fetch('/api/mcp/remove', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, project }),
  })
  if (!res.ok) throw new Error(`Remove failed (${res.status})`)
  return res.json() as Promise<{ ok?: boolean; error?: string }>
}

/* ------------------------------------------------------------------ */
/* Presentational helpers                                              */
/* ------------------------------------------------------------------ */

function StateBadge({ state }: { state: string }) {
  const styles: Record<string, string> = {
    connected: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
    connecting: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/30',
    disconnected: 'bg-zinc-700/30 text-zinc-400 border-zinc-700',
    failed: 'bg-red-500/15 text-red-400 border-red-500/30',
    error: 'bg-red-500/15 text-red-400 border-red-500/30',
  }
  return (
    <span className={cn(
      'inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-medium',
      styles[state] ?? 'bg-zinc-700/30 text-zinc-400 border-zinc-700',
    )}>
      <span className={cn(
        'h-1.5 w-1.5 rounded-full',
        state === 'connected' ? 'bg-emerald-500' : state === 'connecting' ? 'animate-pulse bg-yellow-500' : 'bg-zinc-600',
      )} />
      {state || 'unknown'}
    </span>
  )
}

/* ------------------------------------------------------------------ */
/* Add-server modal                                                    */
/* ------------------------------------------------------------------ */

interface AddModalProps {
  onClose: () => void
}

function AddModal({ onClose }: AddModalProps) {
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [transport, setTransport] = useState<'stdio' | 'http'>('stdio')
  const [command, setCommand] = useState('')
  const [url, setUrl] = useState('')
  const [project, setProject] = useState(false)

  const mutation = useMutation({
    mutationFn: (body: McpAddBody) => addMcp(body),
    onSuccess: (data) => {
      if (data.error) return  // server-side error surfaced below
      queryClient.invalidateQueries({ queryKey: ['mcp'] })
      onClose()
    },
  })

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) return
    const body: McpAddBody = {
      name: name.trim(),
      transport,
      project,
    }
    if (transport === 'stdio') body.command = command.trim()
    else body.url = url.trim()
    mutation.mutate(body)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
         onClick={onClose}>
      <div className="w-full max-w-md rounded-xl border border-zinc-800 bg-zinc-950 shadow-2xl"
           onClick={(e) => e.stopPropagation()}>
        {/* header */}
        <div className="flex items-center justify-between border-b border-zinc-800 px-5 py-3">
          <div className="flex items-center gap-2">
            <Plus className="h-4 w-4 text-brand-cyan" />
            <h3 className="text-sm font-semibold text-zinc-100">Add MCP Server</h3>
          </div>
          <button type="button" aria-label="Close MCP dialog" onClick={onClose} className="text-zinc-500 hover:text-zinc-200">
            <X className="h-4 w-4" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-3 px-5 py-4">
          {/* name */}
          <div>
            <label className="mb-1 block text-xs font-medium text-zinc-300">Name</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. my-mcp-server"
              className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-1.5 text-sm text-zinc-100 outline-none transition-colors placeholder:text-zinc-600 focus:border-brand-primary"
            />
          </div>

          {/* transport toggle */}
          <div>
            <label className="mb-1 block text-xs font-medium text-zinc-300">Transport</label>
            <div className="flex gap-1.5">
              {(['stdio', 'http'] as const).map((t) => (
                <button key={t} type="button"
                  onClick={() => setTransport(t)}
                  className={cn(
                    'rounded-md border px-3 py-1.5 text-xs font-medium transition-colors',
                    transport === t
                      ? 'border-brand-primary bg-brand-primary/10 text-brand-cyan'
                      : 'border-zinc-800 text-zinc-400 hover:bg-zinc-800/50',
                  )}
                >
                  {t}
                </button>
              ))}
            </div>
          </div>

          {/* conditional fields */}
          {transport === 'stdio' ? (
            <div>
              <label className="mb-1 block text-xs font-medium text-zinc-300">Command</label>
              <input
                value={command}
                onChange={(e) => setCommand(e.target.value)}
                placeholder="e.g. npx -y @modelcontextprotocol/server-everything"
                className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-1.5 font-mono text-xs text-zinc-100 outline-none transition-colors placeholder:text-zinc-600 focus:border-brand-primary"
              />
            </div>
          ) : (
            <div>
              <label className="mb-1 block text-xs font-medium text-zinc-300">URL</label>
              <input
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://mcp-server.example.com/sse"
                className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-1.5 font-mono text-xs text-zinc-100 outline-none transition-colors placeholder:text-zinc-600 focus:border-brand-primary"
              />
            </div>
          )}

          {/* project scope checkbox */}
          <label className="flex items-center gap-2 text-xs text-zinc-400">
            <input
              type="checkbox"
              checked={project}
              onChange={(e) => setProject(e.target.checked)}
              className="h-3.5 w-3.5 rounded border-zinc-700 bg-zinc-900 accent-brand-primary"
            />
            Project-scoped (not global)
          </label>

          {/* error */}
          {mutation.isError && (
            <div className="flex items-start gap-2 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-400">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span className="break-all">{(mutation.error as Error)?.message ?? 'Add failed'}</span>
            </div>
          )}
          {mutation.isSuccess && mutation.data?.error && (
            <div className="flex items-start gap-2 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-400">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span className="break-all">{mutation.data.error}</span>
            </div>
          )}

          {/* actions */}
          <div className="flex justify-end gap-2">
            <button type="button" onClick={onClose}
              className="rounded-md border border-zinc-800 px-3 py-1.5 text-xs text-zinc-400 hover:bg-zinc-800/50">
              Cancel
            </button>
            <button type="submit" disabled={mutation.isPending || !name.trim()}
              className="flex items-center gap-1.5 rounded-md bg-brand-primary px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-brand-primary/90 disabled:opacity-50">
              {mutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
              Add
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

export default function McpPage() {
  const queryClient = useQueryClient()
  const [showAdd, setShowAdd] = useState(false)
  const [removing, setRemoving] = useState<string | null>(null)

  const { data: servers, isLoading, isError, error } = useQuery({
    queryKey: ['mcp'],
    queryFn: fetchMcp,
  })

  const reloadMutation = useMutation({
    mutationFn: reloadMcp,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['mcp'] }),
  })

  const removeMutation = useMutation({
    mutationFn: ({ name, project }: { name: string; project: boolean }) => removeMcp(name, project),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['mcp'] })
      setRemoving(null)
    },
  })

  function handleRemove(name: string) {
    if (!confirm(`Remove MCP server "${name}"?`)) return
    setRemoving(name)
    removeMutation.mutate({ name, project: false })
  }

  return (
    <div className="mx-auto max-w-5xl p-6">
      {/* header */}
      <div className="mb-6 flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <Network className="h-5 w-5 text-brand-cyan" />
          <h1 className="text-lg font-semibold text-zinc-100">MCP Servers</h1>
          {servers && (
            <span className="rounded-full bg-zinc-800 px-2 py-0.5 text-[11px] text-zinc-400">
              {servers.length}
            </span>
          )}
        </div>

        <div className="flex items-center gap-2">
          {/* reload */}
          <button
            onClick={() => reloadMutation.mutate()}
            disabled={reloadMutation.isPending}
            className="flex items-center gap-1.5 rounded-md border border-zinc-800 px-3 py-1.5 text-xs text-zinc-300 transition-colors hover:border-brand-primary hover:text-brand-cyan disabled:opacity-50"
          >
            <RefreshCw className={cn('h-3.5 w-3.5', reloadMutation.isPending && 'animate-spin')} />
            Reload
          </button>

          {/* add */}
          <button
            onClick={() => setShowAdd(true)}
            className="flex items-center gap-1.5 rounded-md bg-brand-primary px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-brand-primary/90"
          >
            <Plus className="h-3.5 w-3.5" />
            Add Server
          </button>
        </div>
      </div>

      {/* reload status */}
      {reloadMutation.isSuccess && (
        <div className="mb-4 flex items-center gap-2 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-400">
          <CheckCircle2 className="h-3.5 w-3.5" />
          MCP servers reloaded successfully.
        </div>
      )}
      {reloadMutation.isError && (
        <div className="mb-4 flex items-center gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-400">
          <AlertCircle className="h-3.5 w-3.5" />
          {(reloadMutation.error as Error)?.message ?? 'Reload failed'}
        </div>
      )}

      {/* loading */}
      {isLoading && (
        <div className="flex items-center justify-center py-20">
          <Loader2 className="h-6 w-6 animate-spin text-brand-cyan" />
        </div>
      )}

      {/* error */}
      {isError && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-400">
          {(error as Error)?.message ?? 'Failed to load MCP servers.'}
        </div>
      )}

      {/* table */}
      {servers && servers.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-zinc-800">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-800 bg-zinc-900/50 text-left text-xs uppercase tracking-wide text-zinc-500">
                <th className="px-3 py-2.5 font-medium">Name</th>
                <th className="px-3 py-2.5 font-medium">State</th>
                <th className="px-3 py-2.5 font-medium">Tools</th>
                <th className="px-3 py-2.5 font-medium">Error</th>
                <th className="w-12 px-3 py-2.5" />
              </tr>
            </thead>
            <tbody>
              {servers.map((srv) => (
                <tr key={srv.name} className="border-b border-zinc-800/50 transition-colors hover:bg-zinc-900/30">
                  <td className="px-3 py-2.5 font-mono text-[13px] text-zinc-100">{srv.name}</td>
                  <td className="px-3 py-2.5"><StateBadge state={srv.state} /></td>
                  <td className="px-3 py-2.5">
                    {srv.tools.length > 0 ? (
                      <div className="flex flex-wrap gap-1">
                        {srv.tools.slice(0, 3).map((t) => (
                          <span key={t} className="rounded bg-zinc-800/60 px-1.5 py-0.5 font-mono text-[10px] text-zinc-400">
                            {t}
                          </span>
                        ))}
                        {srv.tools.length > 3 && (
                          <span className="text-[10px] text-zinc-600">+{srv.tools.length - 3}</span>
                        )}
                      </div>
                    ) : (
                      <span className="text-xs text-zinc-600">—</span>
                    )}
                  </td>
                  <td className="max-w-xs truncate px-3 py-2.5 text-xs text-red-400" title={srv.error}>
                    {srv.error || <span className="text-zinc-600">—</span>}
                  </td>
                  <td className="px-3 py-2.5">
                    <button
                      type="button"
                      aria-label={`Remove ${srv.name}`}
                      onClick={() => handleRemove(srv.name)}
                      disabled={removing === srv.name && removeMutation.isPending}
                      className="rounded p-1 text-zinc-500 transition-colors hover:bg-red-500/10 hover:text-red-400 disabled:opacity-50"
                      title={`Remove ${srv.name}`}
                    >
                      {removing === srv.name && removeMutation.isPending
                        ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        : <Trash2 className="h-3.5 w-3.5" />}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* empty */}
      {servers && servers.length === 0 && (
        <div className="py-20 text-center">
          <Network className="mx-auto mb-3 h-10 w-10 text-zinc-700" />
          <p className="text-sm text-zinc-500">No MCP servers configured.</p>
          <button
            onClick={() => setShowAdd(true)}
            className="mt-3 flex items-center gap-1.5 rounded-md border border-zinc-800 px-3 py-1.5 text-xs text-zinc-300 transition-colors hover:border-brand-primary hover:text-brand-cyan"
          >
            <Plus className="h-3.5 w-3.5" /> Add your first server
          </button>
        </div>
      )}

      {/* remove error */}
      {removeMutation.isError && (
        <div className="mt-4 flex items-center gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-400">
          <AlertCircle className="h-3.5 w-3.5" />
          {(removeMutation.error as Error)?.message ?? 'Remove failed'}
        </div>
      )}

      {/* add modal */}
      {showAdd && <AddModal onClose={() => setShowAdd(false)} />}
    </div>
  )
}
