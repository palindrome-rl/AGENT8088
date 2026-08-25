import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  FolderClock, Plus, Play, RotateCcw, Archive, Loader2,
  AlertCircle, MessageSquare, CheckCircle2, X,
} from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useSessionStore } from '@/stores/session'
import type { ChatMessage, SessionInfo } from '@/types/api'
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

interface SessionActionResponse {
  ok?: boolean
  error?: string
  name?: string
  messages?: number
  summary?: string
  message?: string
  history?: { messages: ChatMessage[] }
}

// ---- New Session Dialog ----

function NewSessionDialog({ open, onClose, onCreate, isCreating }: {
  open: boolean
  onClose: () => void
  onCreate: (name: string) => void
  isCreating: boolean
}) {
  const [name, setName] = useState('')
  if (!open) return null
  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return
    onCreate(name.trim())
    setName('')
    onClose()
  }
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div
        className="w-full max-w-sm rounded-xl border border-zinc-700 bg-zinc-900 p-5 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-zinc-100">New Session</h3>
          <button type="button" aria-label="Close new session dialog" onClick={onClose} className="text-zinc-500 hover:text-zinc-200">
            <X className="h-4 w-4" />
          </button>
        </div>
        <form onSubmit={submit} className="space-y-3">
          <input
            autoFocus
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="session-name"
            className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-brand-primary/50 focus:outline-none"
          />
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg px-3 py-2 text-sm text-zinc-400 hover:text-zinc-200"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isCreating || !name.trim()}
              className="flex items-center gap-1.5 rounded-lg bg-brand-primary px-3 py-2 text-sm font-medium text-white hover:bg-brand-primary/80 disabled:opacity-40"
            >
              {isCreating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
              Create
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ---- Compact Dialog ----

function CompactDialog({ open, onClose, onCompact, isCompacting }: {
  open: boolean
  onClose: () => void
  onCompact: (keep: number) => void
  isCompacting: boolean
}) {
  const [keep, setKeep] = useState(6)
  if (!open) return null
  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    onCompact(keep)
    onClose()
  }
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div
        className="w-full max-w-sm rounded-xl border border-zinc-700 bg-zinc-900 p-5 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-zinc-100">Compact Session</h3>
          <button type="button" aria-label="Close compact session dialog" onClick={onClose} className="text-zinc-500 hover:text-zinc-200">
            <X className="h-4 w-4" />
          </button>
        </div>
        <form onSubmit={submit} className="space-y-3">
          <p className="text-xs text-zinc-500">
            Summarize older turns, keeping the most recent messages intact.
          </p>
          <label className="block text-xs text-zinc-400">
            Messages to keep
            <input
              type="number"
              min={1}
              max={50}
              value={keep}
              onChange={(e) => setKeep(Number(e.target.value))}
              className="mt-1 w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 focus:border-brand-primary/50 focus:outline-none"
            />
          </label>
          <div className="flex justify-end gap-2">
            <button type="button" onClick={onClose} className="rounded-lg px-3 py-2 text-sm text-zinc-400 hover:text-zinc-200">
              Cancel
            </button>
            <button
              type="submit"
              disabled={isCompacting}
              className="flex items-center gap-1.5 rounded-lg bg-brand-primary px-3 py-2 text-sm font-medium text-white hover:bg-brand-primary/80 disabled:opacity-40"
            >
              {isCompacting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Archive className="h-4 w-4" />}
              Compact
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ---- Session Row ----

function SessionRow({ session, onResume, isResuming }: {
  session: SessionInfo
  onResume: (name: string) => void
  isResuming: boolean
}) {
  return (
    <tr className="group border-b border-zinc-800/60 transition-colors hover:bg-zinc-800/20 last:border-0">
      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          {session.active && (
            <span className="h-2 w-2 rounded-full bg-brand-cyan" title="active" />
          )}
          <span className={cn('font-mono text-sm', session.active ? 'text-brand-cyan' : 'text-zinc-200')}>
            {session.name}
          </span>
        </div>
      </td>
      <td className="px-4 py-3 text-sm text-zinc-400">
        <span className="flex items-center gap-1.5">
          <MessageSquare className="h-3.5 w-3.5 text-zinc-600" />
          {session.message_count}
        </span>
      </td>
      <td className="px-4 py-3 text-sm text-zinc-500">{session.updated}</td>
      <td className="px-4 py-3 text-right">
        {session.active ? (
          <span className="inline-flex items-center gap-1 text-xs text-green-400">
            <CheckCircle2 className="h-3.5 w-3.5" /> active
          </span>
        ) : (
          <button
            onClick={() => onResume(session.name)}
            disabled={isResuming}
            className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-700 px-2.5 py-1 text-xs text-zinc-300 opacity-0 transition-all hover:border-brand-primary/40 hover:text-brand-cyan group-hover:opacity-100 disabled:opacity-40"
          >
            {isResuming ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
            Resume
          </button>
        )}
      </td>
    </tr>
  )
}

// ---- Main Page ----

export default function SessionsPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { clearChat, setMessages, setSessionName } = useSessionStore()
  const [newSessionOpen, setNewSessionOpen] = useState(false)
  const [compactOpen, setCompactOpen] = useState(false)
  const [resumingName, setResumingName] = useState<string | null>(null)

  // List sessions
  const sessionsQuery = useQuery({
    queryKey: ['sessions'],
    queryFn: () => fetchJSON<SessionInfo[]>('/api/sessions'),
  })

  // New session
  const newMutation = useMutation({
    mutationFn: (name: string) => postJSON<SessionActionResponse>('/api/sessions/new', { name }),
    onSuccess: (data) => {
      if (!data.ok || !data.name) return
      clearChat()
      setSessionName(data.name)
      void queryClient.invalidateQueries({ queryKey: ['sessions'] })
      navigate('/')
    },
  })

  // Resume session
  const resumeMutation = useMutation({
    mutationFn: async (name: string) => {
      const result = await postJSON<SessionActionResponse>('/api/sessions/resume', { name })
      if (result.error || !result.ok) return result
      return {
        ...result,
        history: await fetchJSON<{ messages: ChatMessage[] }>('/api/history'),
      }
    },
    onMutate: (name) => setResumingName(name),
    onSettled: () => setResumingName(null),
    onSuccess: (data, name) => {
      if (!data.ok) return
      setMessages(data.history?.messages ?? [])
      setSessionName(data.name || name)
      void queryClient.invalidateQueries({ queryKey: ['sessions'] })
      navigate('/')
    },
  })

  // Reset session
  const resetMutation = useMutation({
    mutationFn: () => postJSON<SessionActionResponse>('/api/sessions/reset', {}),
    onSuccess: (data) => {
      if (!data.ok) return
      clearChat()
      void queryClient.invalidateQueries({ queryKey: ['sessions'] })
    },
  })

  // Compact session
  const compactMutation = useMutation({
    mutationFn: (keep: number) => postJSON<SessionActionResponse>('/api/sessions/compact', { keep }),
    onSuccess: async (data) => {
      if (!data.ok) return
      const history = await fetchJSON<{ messages: ChatMessage[] }>('/api/history')
      setMessages(history.messages)
      void queryClient.invalidateQueries({ queryKey: ['sessions'] })
    },
  })

  const activeSession = sessionsQuery.data?.find((s) => s.active)

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <FolderClock className="h-6 w-6 text-brand-cyan" />
          <div>
            <h1 className="text-lg font-semibold text-zinc-100">Sessions</h1>
            <p className="text-xs text-zinc-500">Named conversation snapshots</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setNewSessionOpen(true)}
            className="flex items-center gap-1.5 rounded-lg bg-brand-primary px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-primary/80"
          >
            <Plus className="h-4 w-4" /> New
          </button>
          <button
            onClick={() => resetMutation.mutate()}
            disabled={resetMutation.isPending}
            className="flex items-center gap-1.5 rounded-lg border border-zinc-700 bg-zinc-800/50 px-3 py-2 text-sm text-zinc-300 transition-colors hover:border-red-900/50 hover:text-red-400 disabled:opacity-40"
            title="Clear active session messages"
          >
            {resetMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />}
            Reset
          </button>
          <button
            onClick={() => setCompactOpen(true)}
            disabled={compactMutation.isPending}
            className="flex items-center gap-1.5 rounded-lg border border-zinc-700 bg-zinc-800/50 px-3 py-2 text-sm text-zinc-300 transition-colors hover:border-brand-primary/40 hover:text-brand-cyan disabled:opacity-40"
            title="Summarize older turns"
          >
            {compactMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Archive className="h-4 w-4" />}
            Compact
          </button>
        </div>
      </div>

      {/* Mutation feedback */}
      {newMutation.data?.error && (
        <div className="flex items-center gap-2 rounded-lg border border-red-900/50 bg-red-950/30 p-3 text-xs text-red-400">
          <AlertCircle className="h-3.5 w-3.5 shrink-0" /> {newMutation.data.error}
        </div>
      )}
      {newMutation.isSuccess && !newMutation.data?.error && (
        <div className="flex items-center gap-2 rounded-lg border border-green-900/50 bg-green-950/20 p-3 text-xs text-green-400">
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0" /> Created session "{newMutation.data?.name}"
        </div>
      )}
      {resumeMutation.data?.error && (
        <div className="flex items-center gap-2 rounded-lg border border-red-900/50 bg-red-950/30 p-3 text-xs text-red-400">
          <AlertCircle className="h-3.5 w-3.5 shrink-0" /> {resumeMutation.data.error}
        </div>
      )}
      {resumeMutation.isSuccess && !resumeMutation.data?.error && (
        <div className="flex items-center gap-2 rounded-lg border border-green-900/50 bg-green-950/20 p-3 text-xs text-green-400">
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0" /> Resumed "{resumeMutation.data?.name}" ({resumeMutation.data?.messages} messages)
        </div>
      )}
      {resetMutation.isSuccess && !resetMutation.data?.error && (
        <div className="flex items-center gap-2 rounded-lg border border-green-900/50 bg-green-950/20 p-3 text-xs text-green-400">
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0" /> Active session cleared
        </div>
      )}
      {compactMutation.data?.error && (
        <div className="flex items-center gap-2 rounded-lg border border-red-900/50 bg-red-950/30 p-3 text-xs text-red-400">
          <AlertCircle className="h-3.5 w-3.5 shrink-0" /> {compactMutation.data.error}
        </div>
      )}
      {compactMutation.isSuccess && !compactMutation.data?.error && (
        <div className="flex items-center gap-2 rounded-lg border border-green-900/50 bg-green-950/20 p-3 text-xs text-green-400">
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0" /> {compactMutation.data?.message ?? 'Compacted'}
          {compactMutation.data?.summary && (
            <span className="text-zinc-500">— {compactMutation.data.summary.slice(0, 80)}…</span>
          )}
        </div>
      )}

      {/* Sessions Table */}
      <div className="overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900/50">
        {sessionsQuery.isLoading ? (
          <div className="flex items-center gap-2 p-8 text-sm text-zinc-400">
            <Loader2 className="h-4 w-4 animate-spin text-brand-cyan" /> Loading sessions…
          </div>
        ) : sessionsQuery.isError ? (
          <div className="flex items-center gap-2 p-8 text-sm text-red-400">
            <AlertCircle className="h-4 w-4" /> Failed to load sessions
          </div>
        ) : !sessionsQuery.data || sessionsQuery.data.length === 0 ? (
          <div className="p-8 text-center">
            <FolderClock className="mx-auto mb-3 h-8 w-8 text-zinc-700" />
            <p className="text-sm text-zinc-500">No saved sessions yet</p>
            <p className="mt-1 text-xs text-zinc-600">Create a new session to start saving conversations</p>
          </div>
        ) : (
          <table className="w-full">
            <thead>
              <tr className="border-b border-zinc-800 text-left text-xs uppercase tracking-wider text-zinc-600">
                <th className="px-4 py-3 font-medium">Name</th>
                <th className="px-4 py-3 font-medium">Messages</th>
                <th className="px-4 py-3 font-medium">Updated</th>
                <th className="px-4 py-3 text-right font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {sessionsQuery.data.map((session) => (
                <SessionRow
                  key={session.name}
                  session={session}
                  onResume={(name) => resumeMutation.mutate(name)}
                  isResuming={resumingName === session.name}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Active session info */}
      {activeSession && (
        <div className="rounded-xl border border-brand-border/30 bg-brand-primary/5 p-4">
          <div className="flex items-center gap-2 text-sm text-zinc-300">
            <span className="h-2 w-2 animate-pulse rounded-full bg-brand-cyan" />
            <span className="font-medium">Active session:</span>
            <span className="font-mono text-brand-cyan">{activeSession.name}</span>
            <span className="text-zinc-600">·</span>
            <span className="text-zinc-500">{activeSession.message_count} messages</span>
          </div>
        </div>
      )}

      {/* Dialogs */}
      <NewSessionDialog
        open={newSessionOpen}
        onClose={() => setNewSessionOpen(false)}
        onCreate={(name) => newMutation.mutate(name)}
        isCreating={newMutation.isPending}
      />
      <CompactDialog
        open={compactOpen}
        onClose={() => setCompactOpen(false)}
        onCompact={(keep) => compactMutation.mutate(keep)}
        isCompacting={compactMutation.isPending}
      />
    </div>
  )
}
