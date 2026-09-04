import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CircleStop, ClipboardList, Loader2, Play, RotateCcw, XCircle } from 'lucide-react'
import type { DurableTask } from '@/types/api'

async function request<T>(url: string, body?: unknown): Promise<T> {
  const response = await fetch(url, body === undefined ? undefined : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  const data = await response.json() as T & { error?: string }
  if (data.error) throw new Error(data.error)
  return data
}

const stateColor: Record<string, string> = { running: 'text-brand-cyan bg-brand-primary/10', queued: 'text-yellow-400 bg-yellow-500/10', paused: 'text-orange-400 bg-orange-500/10', completed: 'text-emerald-400 bg-emerald-500/10', cancelled: 'text-zinc-400 bg-zinc-800' }

export default function TasksPage() {
  const client = useQueryClient()
  const [goal, setGoal] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const tasks = useQuery({
    queryKey: ['tasks'], queryFn: () => request<DurableTask[]>('/api/tasks'),
    refetchInterval: (query) => query.state.data?.some((task) => task.state === 'running' || task.state === 'queued') ? 1500 : false,
  })
  const detail = useQuery({ queryKey: ['task', selected], queryFn: () => request<DurableTask>(`/api/tasks/${selected}`), enabled: Boolean(selected), refetchInterval: () => tasks.data?.some((task) => task.id === selected && (task.state === 'running' || task.state === 'queued')) ? 1500 : false })
  const refresh = () => { void client.invalidateQueries({ queryKey: ['tasks'] }); void client.invalidateQueries({ queryKey: ['task', selected] }) }
  const start = useMutation({ mutationFn: () => request<DurableTask>('/api/tasks', { goal }), onSuccess: (task) => { setGoal(''); setSelected(task.id); refresh() } })
  const resume = useMutation({ mutationFn: (id: string) => request<DurableTask>(`/api/tasks/${id}/resume`, {}), onSuccess: refresh })
  const end = useMutation({ mutationFn: (id: string) => request<DurableTask>(`/api/tasks/${id}/end`, {}), onSuccess: refresh })
  const error = tasks.error ?? detail.error ?? start.error ?? resume.error ?? end.error
  const active = detail.data

  return <div className="mx-auto max-w-5xl space-y-5 p-4 sm:p-6">
    <div className="flex items-center gap-2.5"><ClipboardList className="h-5 w-5 text-brand-cyan" /><div><h1 className="text-lg font-semibold text-zinc-100">Durable Tasks</h1><p className="text-xs text-zinc-500">Checkpointed work that can be resumed after interruption.</p></div></div>
    {error && <div className="flex gap-2 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-400"><XCircle className="h-4 w-4 shrink-0" />{(error as Error).message}</div>}
    <form onSubmit={(event) => { event.preventDefault(); if (goal.trim()) start.mutate() }} className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4"><label className="mb-2 block text-sm font-semibold text-zinc-200">New task</label><div className="flex flex-col gap-2 sm:flex-row"><input value={goal} onChange={(event) => setGoal(event.target.value)} placeholder="Describe the durable task…" className="min-w-0 flex-1 rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-brand-primary/50 focus:outline-none" /><button disabled={!goal.trim() || start.isPending} className="inline-flex items-center justify-center gap-2 rounded-lg bg-brand-primary px-3 py-2 text-sm font-medium text-white disabled:opacity-40">{start.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}Start</button></div></form>
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]"><section className="overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900/50"><div className="border-b border-zinc-800 px-4 py-3 text-sm font-semibold text-zinc-200">Tasks</div>{tasks.isLoading ? <div className="flex justify-center p-10"><Loader2 className="h-5 w-5 animate-spin text-brand-cyan" /></div> : tasks.data?.length ? <div>{tasks.data.map((task) => <button key={task.id} onClick={() => setSelected(task.id)} className={`w-full border-b border-zinc-800 p-4 text-left last:border-0 hover:bg-zinc-800/30 ${selected === task.id ? 'bg-brand-primary/5' : ''}`}><div className="flex items-start justify-between gap-2"><span className="line-clamp-2 text-sm text-zinc-200">{task.goal}</span><span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${stateColor[task.state]}`}>{task.state}</span></div><div className="mt-2 font-mono text-[11px] text-zinc-600">{task.id.slice(0, 12)} · slice {task.slice_no}</div></button>)}</div> : <div className="p-10 text-center text-sm text-zinc-500">No durable tasks yet.</div>}</section>
      <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">{!selected ? <div className="p-10 text-center text-sm text-zinc-500">Select a task to view its live output.</div> : detail.isLoading ? <div className="flex justify-center p-10"><Loader2 className="h-5 w-5 animate-spin text-brand-cyan" /></div> : active ? <><div className="flex items-start justify-between gap-3"><div><h2 className="text-sm font-semibold text-zinc-200">Task output</h2><p className="mt-1 font-mono text-[11px] text-zinc-600">{active.id}</p></div><span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${stateColor[active.state]}`}>{active.state}</span></div><p className="mt-3 text-sm text-zinc-400">{active.goal}</p>{active.error && <p className="mt-3 rounded bg-red-500/10 p-2 text-xs text-red-400">{active.error}</p>}<div className="mt-4 flex gap-2">{(active.state === 'paused' || active.state === 'queued') && <button onClick={() => resume.mutate(active.id)} disabled={resume.isPending} className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-700 px-2.5 py-1.5 text-xs text-zinc-300 hover:border-brand-primary"><RotateCcw className="h-3.5 w-3.5" />Resume</button>}{!['completed', 'cancelled'].includes(active.state) && <button onClick={() => { if (window.confirm('End this durable task?')) end.mutate(active.id) }} disabled={end.isPending} className="inline-flex items-center gap-1.5 rounded-lg border border-red-500/30 px-2.5 py-1.5 text-xs text-red-400 hover:bg-red-500/10"><CircleStop className="h-3.5 w-3.5" />End</button>}</div><div className="mt-4 rounded-lg border border-zinc-800 bg-zinc-950 p-3"><p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-zinc-500">Latest answer</p><pre className="max-h-72 overflow-auto whitespace-pre-wrap text-xs leading-relaxed text-zinc-300">{active.last_answer || 'Waiting for the first checkpoint…'}</pre></div><div className="mt-4"><p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-zinc-500">Operation ledger</p>{active.operations?.length ? <div className="space-y-2">{active.operations.map((operation) => <div key={operation.id} className="rounded border border-zinc-800 p-2"><div className="flex justify-between gap-2 font-mono text-xs text-zinc-300"><span>{operation.tool}</span><span className="text-zinc-500">{operation.state}</span></div>{operation.result && <pre className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap text-[11px] text-zinc-500">{operation.result}</pre>}</div>)}</div> : <p className="text-xs text-zinc-600">No tool operations yet.</p>}</div></> : <p className="text-sm text-red-400">Task could not be loaded.</p>}</section></div>
  </div>
}
