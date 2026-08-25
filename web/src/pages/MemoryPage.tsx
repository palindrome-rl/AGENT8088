import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Brain, Search, Plus, Trash2, Power, Loader2, AlertCircle,
  Database, Zap, RefreshCw,
} from 'lucide-react'
import type { MemoryStatus, MemoryFact } from '@/types/api'
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

async function deleteJSON<T>(url: string): Promise<T> {
  const res = await fetch(url, { method: 'DELETE' })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json() as Promise<T>
}

interface SearchResponse {
  query: string
  results: MemoryFact[]
}

interface ActionResponse {
  ok?: boolean
  error?: string
  enabled?: boolean
}

// ---- Status Panel ----

function StatusRow({ label, value, mono }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-center justify-between border-b border-zinc-800/60 py-2 text-sm last:border-0">
      <span className="text-zinc-500">{label}</span>
      <span className={cn('text-zinc-200', mono && 'font-mono text-xs')}>{value}</span>
    </div>
  )
}

function StatusBadge({ ok, label }: { ok: boolean | null; label: string }) {
  if (ok === null) {
    return <span className="rounded bg-zinc-800 px-2 py-0.5 text-xs text-zinc-400">unknown</span>
  }
  return (
    <span className={cn(
      'rounded px-2 py-0.5 text-xs font-medium',
      ok ? 'bg-green-500/15 text-green-400' : 'bg-red-500/15 text-red-400',
    )}>
      {ok ? '✓' : '✗'} {label}
    </span>
  )
}

function StatusPanel({ status, isLoading, isError, error }: {
  status: MemoryStatus | undefined
  isLoading: boolean
  isError: boolean
  error: Error | null
}) {
  if (isLoading) {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-zinc-800 bg-zinc-900/50 p-6 text-sm text-zinc-400">
        <Loader2 className="h-4 w-4 animate-spin text-brand-cyan" /> Loading memory status…
      </div>
    )
  }
  if (isError || !status) {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-red-900/50 bg-red-950/30 p-4 text-sm text-red-400">
        <AlertCircle className="h-4 w-4 shrink-0" />
        {error?.message ?? 'Failed to load memory status'}
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
      <div className="mb-4 flex items-center gap-2">
        <Database className="h-4 w-4 text-brand-cyan" />
        <h3 className="text-sm font-semibold text-zinc-200">System Status</h3>
        <div className="ml-auto flex items-center gap-2">
          <StatusBadge ok={status.embedder_ok ?? null} label="embedder" />
          <span className={cn(
            'rounded px-2 py-0.5 text-xs font-medium',
            status.enabled ? 'bg-green-500/15 text-green-400' : 'bg-zinc-800 text-zinc-500',
          )}>
            {status.enabled ? 'enabled' : 'disabled'}
          </span>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-x-8 gap-y-0 sm:grid-cols-2">
        <StatusRow label="Facts stored" value={status.count} mono />
        <StatusRow label="Stale vectors" value={status.stale_vectors} mono />
        <StatusRow label="Embed model" value={status.embed_model} mono />
        <StatusRow label="Embed provider" value={status.embed_provider} mono />
        <StatusRow label="Extract model" value={status.extract_model} mono />
        <StatusRow label="Recall limit" value={status.recall_limit} mono />
        <StatusRow label="RRF k" value={status.rrf_k} mono />
        <StatusRow label="Scope by identity" value={status.scope_by_identity ? 'yes' : 'no'} />
        <StatusRow label="Capture enabled" value={status.capture_enabled ? 'yes' : 'no'} />
        <StatusRow label="User ID" value={status.user_id} mono />
        <StatusRow label="DB path" value={status.db_path} mono />
      </div>

      {status.embedder_error && (
        <div className="mt-4 rounded-lg border border-red-900/50 bg-red-950/20 p-3 text-xs text-red-400">
          <span className="font-semibold">Embedder error:</span> {status.embedder_error}
        </div>
      )}
      {status.error && (
        <div className="mt-2 rounded-lg border border-red-900/50 bg-red-950/20 p-3 text-xs text-red-400">
          <span className="font-semibold">Error:</span> {status.error}
        </div>
      )}
    </div>
  )
}

// ---- Context Card (search result) ----

function ContextCard({ fact, onForget, forgetting }: {
  fact: MemoryFact
  onForget: (id: string) => void
  forgetting: boolean
}) {
  return (
    <div className="group rounded-lg border border-zinc-800 bg-zinc-900/40 p-4 transition-colors hover:border-brand-border/40">
      <div className="mb-2 flex items-start justify-between gap-2">
        <p className="text-sm text-zinc-200">{fact.text}</p>
        <button
          type="button"
          aria-label={`Forget ${fact.id}`}
          onClick={() => onForget(fact.id)}
          disabled={forgetting}
          className="shrink-0 rounded p-1 text-zinc-600 opacity-0 transition-opacity hover:bg-red-500/10 hover:text-red-400 group-hover:opacity-100"
          title="Forget this fact"
        >
          {forgetting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-3 text-xs text-zinc-600">
        {fact.source && (
          <span className="rounded bg-zinc-800/60 px-1.5 py-0.5 font-mono">{fact.source}</span>
        )}
        <span className="font-mono">{new Date(fact.created_at).toLocaleDateString()}</span>
        <span className="flex items-center gap-1">
          <Zap className="h-3 w-3 text-brand-cyan/60" />
          {(fact.score * 100).toFixed(0)}%
        </span>
        <span className="font-mono text-zinc-700">id: {fact.id.slice(0, 8)}</span>
      </div>
    </div>
  )
}

// ---- Add Fact Form ----

function AddFactForm({ onAdd, isAdding }: { onAdd: (text: string) => void; isAdding: boolean }) {
  const [text, setText] = useState('')
  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!text.trim()) return
    onAdd(text.trim())
    setText('')
  }
  return (
    <form onSubmit={submit} className="flex gap-2">
      <input
        type="text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Add a fact to memory…"
        className="flex-1 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-brand-primary/50 focus:outline-none"
      />
      <button
        type="submit"
        disabled={isAdding || !text.trim()}
        className="flex items-center gap-1.5 rounded-lg bg-brand-primary px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-primary/80 disabled:cursor-not-allowed disabled:opacity-40"
      >
        {isAdding ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
        Add
      </button>
    </form>
  )
}

// ---- Main Page ----

export default function MemoryPage() {
  const queryClient = useQueryClient()
  const [searchQuery, setSearchQuery] = useState('')

  // Status query
  const statusQuery = useQuery({
    queryKey: ['memory', 'status'],
    queryFn: () => fetchJSON<MemoryStatus>('/api/memory/status'),
  })

  // Search query (only fires when user submits)
  const searchQueryFn = useQuery({
    queryKey: ['memory', 'search', searchQuery],
    queryFn: () => fetchJSON<SearchResponse>(`/api/memory/search?q=${encodeURIComponent(searchQuery)}`),
    enabled: searchQuery.length > 0,
  })

  // Toggle mutation
  const toggleMutation = useMutation({
    mutationFn: (enabled: boolean) => postJSON<ActionResponse>('/api/memory/toggle', { enabled }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['memory', 'status'] }),
  })

  // Add fact mutation
  const addMutation = useMutation({
    mutationFn: (text: string) => postJSON<ActionResponse>('/api/memory/add', { text }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['memory', 'status'] })
      if (searchQuery) queryClient.invalidateQueries({ queryKey: ['memory', 'search', searchQuery] })
    },
  })

  // Forget (delete) mutation
  const forgetMutation = useMutation({
    mutationFn: (factId: string) => deleteJSON<ActionResponse>(`/api/memory/${factId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['memory', 'status'] })
      if (searchQuery) queryClient.invalidateQueries({ queryKey: ['memory', 'search', searchQuery] })
    },
  })

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault()
    if (searchQuery.trim()) {
      searchQueryFn.refetch()
    }
  }

  const isCurrentlyEnabled = statusQuery.data?.enabled ?? false
  const toggleError = toggleMutation.data?.error

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Brain className="h-6 w-6 text-brand-cyan" />
          <div>
            <h1 className="text-lg font-semibold text-zinc-100">Memory</h1>
            <p className="text-xs text-zinc-500">Persistent knowledge store & semantic recall</p>
          </div>
        </div>
        <button
          onClick={() => toggleMutation.mutate(!isCurrentlyEnabled)}
          disabled={toggleMutation.isPending}
          className={cn(
            'flex items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium transition-colors',
            isCurrentlyEnabled
              ? 'border-zinc-700 bg-zinc-800/50 text-zinc-300 hover:bg-red-500/10 hover:text-red-400 hover:border-red-900/50'
              : 'border-brand-primary/40 bg-brand-primary/10 text-brand-cyan hover:bg-brand-primary/20',
          )}
        >
          {toggleMutation.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Power className="h-4 w-4" />
          )}
          {isCurrentlyEnabled ? 'Disable' : 'Enable'}
        </button>
      </div>

      {toggleError && (
        <div className="flex items-center gap-2 rounded-lg border border-red-900/50 bg-red-950/30 p-3 text-xs text-red-400">
          <AlertCircle className="h-3.5 w-3.5 shrink-0" /> {toggleError}
        </div>
      )}

      {/* Status Panel */}
      <StatusPanel
        status={statusQuery.data}
        isLoading={statusQuery.isLoading}
        isError={statusQuery.isError}
        error={statusQuery.error}
      />

      {/* Add Fact */}
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
        <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-zinc-200">
          <Plus className="h-4 w-4 text-brand-cyan" /> Add Fact
        </h3>
        <AddFactForm
          onAdd={(text) => addMutation.mutate(text)}
          isAdding={addMutation.isPending}
        />
        {addMutation.data?.error && (
          <p className="mt-2 text-xs text-red-400">{addMutation.data.error}</p>
        )}
        {addMutation.isSuccess && !addMutation.data?.error && (
          <p className="mt-2 text-xs text-green-400">✓ Fact added to memory</p>
        )}
      </div>

      {/* Search */}
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
        <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-zinc-200">
          <Search className="h-4 w-4 text-brand-cyan" /> Semantic Search
        </h3>
        <form onSubmit={handleSearch} className="flex gap-2">
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search memory…"
            className="flex-1 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-brand-primary/50 focus:outline-none"
          />
          <button
            type="submit"
            disabled={searchQueryFn.isLoading || !searchQuery.trim()}
            className="flex items-center gap-1.5 rounded-lg border border-brand-primary/40 bg-brand-primary/10 px-3 py-2 text-sm font-medium text-brand-cyan transition-colors hover:bg-brand-primary/20 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {searchQueryFn.isLoading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Search className="h-4 w-4" />
            )}
            Search
          </button>
        </form>

        {/* Search results */}
        {searchQueryFn.isLoading && (
          <div className="mt-4 flex items-center gap-2 text-sm text-zinc-400">
            <Loader2 className="h-4 w-4 animate-spin text-brand-cyan" /> Searching…
          </div>
        )}
        {searchQueryFn.isError && (
          <div className="mt-4 flex items-center gap-2 text-sm text-red-400">
            <AlertCircle className="h-4 w-4" /> Search failed
          </div>
        )}
        {searchQueryFn.data && searchQueryFn.data.results.length === 0 && (
          <div className="mt-4 text-sm text-zinc-500">No results for "{searchQuery}"</div>
        )}
        {searchQueryFn.data && searchQueryFn.data.results.length > 0 && (
          <div className="mt-4 space-y-2">
            <div className="flex items-center gap-1.5 text-xs text-zinc-600">
              <RefreshCw className="h-3 w-3" />
              {searchQueryFn.data.results.length} result{searchQueryFn.data.results.length !== 1 ? 's' : ''}
            </div>
            {searchQueryFn.data.results.map((fact) => (
              <ContextCard
                key={fact.id}
                fact={fact}
                onForget={(id) => forgetMutation.mutate(id)}
                forgetting={forgetMutation.isPending}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
