import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, Search, Square, Wrench } from 'lucide-react'

type SearchStatus = { selected: string; active_chain: string; providers: Array<{ name: string; available: boolean; badge: string; hint: string }>; docker_available: boolean; ssrf_guidance: string }

async function getStatus(): Promise<SearchStatus> { const r = await fetch('/api/search'); if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() as Promise<SearchStatus> }
async function post(path: string, body: unknown) { const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); return r.json() as Promise<{ error?: string; confirmation_required?: boolean; message?: string }> }

export default function SearchPage() {
  const client = useQueryClient()
  const status = useQuery({ queryKey: ['search'], queryFn: getStatus })
  const action = useMutation({ mutationFn: async ({ path, body }: { path: string; body: unknown }) => {
    const result = await post(path, body)
    if (result.confirmation_required && window.confirm(result.message || 'Continue?')) return post(path, { ...(body as object), confirmed: true })
    if (result.error) throw new Error(result.error)
    return result
  }, onSuccess: () => client.invalidateQueries({ queryKey: ['search'] }) })
  const data = status.data
  return <div className="mx-auto max-w-4xl space-y-5 p-4 sm:p-6">
    <div className="flex items-center gap-2.5"><Search className="h-5 w-5 text-brand-cyan" /><div><h1 className="text-lg font-semibold text-zinc-100">Web Search</h1><p className="text-xs text-zinc-500">Uses the engine search chain and its existing egress/SSRF guards.</p></div></div>
    {status.isLoading ? <Loader2 className="mx-auto mt-20 h-6 w-6 animate-spin text-brand-cyan" /> : status.error ? <p className="text-red-400">{(status.error as Error).message}</p> : data && <>
      <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4"><p className="text-xs uppercase tracking-wide text-zinc-500">Active chain</p><p className="mt-1 font-mono text-sm text-brand-cyan">{data.active_chain}</p><p className="mt-3 text-xs text-zinc-500">{data.ssrf_guidance}</p></section>
      <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4"><div className="mb-3 flex items-center justify-between"><h2 className="text-sm font-semibold text-zinc-200">Providers</h2><span className="text-xs text-zinc-500">selected: {data.selected}</span></div><div className="space-y-2">{data.providers.map((provider) => <div key={provider.name} className="flex flex-wrap items-center gap-2 rounded-lg border border-zinc-800 p-3"><span className={`h-2 w-2 rounded-full ${provider.available ? 'bg-emerald-400' : 'bg-zinc-600'}`} /><span className="font-mono text-sm text-zinc-200">{provider.name}</span><span className="min-w-0 flex-1 text-xs text-zinc-500">{provider.hint}</span><button onClick={() => action.mutate({ path: '/api/search/use', body: { provider: provider.name } })} className="rounded border border-zinc-700 px-2 py-1 text-xs text-zinc-300 hover:border-brand-primary">Use</button></div>)}</div></section>
      <div className="flex flex-wrap gap-2"><button disabled={!data.docker_available || action.isPending} onClick={() => action.mutate({ path: '/api/search/setup', body: {} })} className="inline-flex items-center gap-1.5 rounded-lg bg-brand-primary px-3 py-2 text-sm text-white disabled:opacity-40">{action.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wrench className="h-4 w-4" />}Provision SearXNG</button><button disabled={action.isPending} onClick={() => action.mutate({ path: '/api/search/stop', body: {} })} className="inline-flex items-center gap-1.5 rounded-lg border border-red-500/30 px-3 py-2 text-sm text-red-400"><Square className="h-4 w-4" />Stop SearXNG</button></div>
    </>}
  </div>
}
