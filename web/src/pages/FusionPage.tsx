import { useEffect, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Bot, CheckCircle2, Loader2, Plus, Sparkles, Trash2, Trophy, XCircle } from 'lucide-react'
import type { ConfigInfo, FusionConfig, FusionResult } from '@/types/api'

type Seat = { provider: string; model: string }

async function getJSON<T>(url: string): Promise<T> {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return response.json() as Promise<T>
}

async function postJSON<T>(url: string, body: unknown): Promise<T> {
  const response = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  const data = await response.json() as T & { error?: string }
  if (data.error) throw new Error(data.error)
  return data
}

function parseSeat(value: string): Seat {
  const [provider, ...model] = value.split(':')
  return { provider, model: model.join(':') }
}

export default function FusionPage() {
  const [question, setQuestion] = useState('')
  const [seats, setSeats] = useState<Seat[]>([])
  const [judge, setJudge] = useState<Seat>({ provider: '', model: '' })
  const [maxPanel, setMaxPanel] = useState(6)
  const [result, setResult] = useState<FusionResult | null>(null)
  const configQuery = useQuery({ queryKey: ['config'], queryFn: () => getJSON<ConfigInfo>('/api/config') })
  const fusionQuery = useQuery({ queryKey: ['fusion-config'], queryFn: () => getJSON<FusionConfig>('/api/fusion/config') })

  useEffect(() => {
    if (!fusionQuery.data) return
    setSeats(fusionQuery.data.panel.map(parseSeat))
    setJudge({ provider: fusionQuery.data.judge_provider, model: fusionQuery.data.judge_model })
    setMaxPanel(fusionQuery.data.max_panel)
  }, [fusionQuery.data])

  const providers = Object.entries(configQuery.data?.providers ?? {})
  const defaultSeat = (): Seat => ({ provider: providers[0]?.[0] ?? '', model: providers[0]?.[1]?.model ?? '' })
  const save = useMutation({
    mutationFn: () => postJSON<FusionConfig>('/api/fusion/config', {
      panel: seats.filter((seat) => seat.provider).map((seat) => `${seat.provider}${seat.model ? `:${seat.model}` : ''}`),
      judge_provider: judge.provider, judge_model: judge.model, max_panel: maxPanel,
    }),
    onSuccess: () => void fusionQuery.refetch(),
  })
  const run = useMutation({
    mutationFn: () => postJSON<FusionResult>('/api/fusion/run', {
      query: question,
      panel: seats.filter((seat) => seat.provider).map((seat) => `${seat.provider}${seat.model ? `:${seat.model}` : ''}`),
      judge_provider: judge.provider, judge_model: judge.model,
    }),
    onSuccess: setResult,
  })

  const updateSeat = (index: number, field: keyof Seat, value: string) => {
    setSeats((current) => current.map((seat, i) => i === index ? { ...seat, [field]: value } : seat))
  }

  if (configQuery.isLoading || fusionQuery.isLoading) return <div className="flex justify-center p-20"><Loader2 className="h-6 w-6 animate-spin text-brand-cyan" /></div>
  const error = configQuery.error ?? fusionQuery.error ?? save.error ?? run.error

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-4 sm:p-6">
      <div className="flex items-center gap-2.5">
        <Sparkles className="h-5 w-5 text-brand-cyan" />
        <div><h1 className="text-lg font-semibold text-zinc-100">Fusion</h1><p className="text-xs text-zinc-500">Parallel answers, then a blind verdict.</p></div>
      </div>
      {error && <div className="flex gap-2 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-400"><XCircle className="h-4 w-4 shrink-0" />{(error as Error).message}</div>}
      <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4 sm:p-5">
        <div className="mb-4 flex items-center justify-between"><h2 className="text-sm font-semibold text-zinc-200">Panel</h2><label className="text-xs text-zinc-500">Max seats <input aria-label="Maximum panel seats" type="number" min="1" max="8" value={maxPanel} onChange={(e) => setMaxPanel(Math.max(1, Number(e.target.value)))} className="ml-2 w-14 rounded border border-zinc-700 bg-zinc-950 px-1.5 py-1 text-zinc-200" /></label></div>
        <div className="space-y-2">
          {seats.map((seat, index) => <div key={index} className="grid grid-cols-[1fr_1fr_auto] gap-2">
            <select aria-label={`Panel provider ${index + 1}`} value={seat.provider} onChange={(e) => { const provider = e.target.value; updateSeat(index, 'provider', provider); updateSeat(index, 'model', (configQuery.data?.providers[provider]?.model ?? '')) }} className="min-w-0 rounded-lg border border-zinc-800 bg-zinc-950 px-2 py-2 text-sm text-zinc-200"><option value="">Provider…</option>{providers.map(([name]) => <option key={name} value={name}>{name}</option>)}</select>
            <input aria-label={`Panel model ${index + 1}`} value={seat.model} onChange={(e) => updateSeat(index, 'model', e.target.value)} placeholder="model (default if blank)" className="min-w-0 rounded-lg border border-zinc-800 bg-zinc-950 px-2 py-2 text-sm text-zinc-200 placeholder:text-zinc-600" />
            <button aria-label={`Remove panel member ${index + 1}`} onClick={() => setSeats((current) => current.filter((_, i) => i !== index))} className="rounded-lg border border-zinc-800 px-2 text-zinc-500 hover:text-red-400"><Trash2 className="h-4 w-4" /></button>
          </div>)}
        </div>
        <button onClick={() => setSeats((current) => current.length < maxPanel ? [...current, defaultSeat()] : current)} disabled={seats.length >= maxPanel} className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300 hover:border-brand-primary disabled:opacity-40"><Plus className="h-3.5 w-3.5" />Add member</button>
        <div className="mt-5 border-t border-zinc-800 pt-4"><p className="mb-2 text-xs font-medium text-zinc-400">Blind judge <span className="font-normal text-zinc-600">(blank uses the active model)</span></p><div className="grid grid-cols-2 gap-2"><select aria-label="Judge provider" value={judge.provider} onChange={(e) => setJudge({ provider: e.target.value, model: configQuery.data?.providers[e.target.value]?.model ?? '' })} className="rounded-lg border border-zinc-800 bg-zinc-950 px-2 py-2 text-sm text-zinc-200"><option value="">Active provider</option>{providers.map(([name]) => <option key={name} value={name}>{name}</option>)}</select><input aria-label="Judge model" value={judge.model} onChange={(e) => setJudge((current) => ({ ...current, model: e.target.value }))} placeholder="model" className="rounded-lg border border-zinc-800 bg-zinc-950 px-2 py-2 text-sm text-zinc-200 placeholder:text-zinc-600" /></div></div>
        <button onClick={() => save.mutate()} disabled={save.isPending} className="mt-4 inline-flex items-center gap-1.5 rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300 hover:border-brand-primary disabled:opacity-40">{save.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}Save panel</button>
      </section>
      <section className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4 sm:p-5"><label className="mb-2 block text-sm font-semibold text-zinc-200">Question</label><textarea value={question} onChange={(e) => setQuestion(e.target.value)} rows={3} placeholder="Ask the panel…" className="w-full resize-y rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-brand-primary/50 focus:outline-none" /><button onClick={() => run.mutate()} disabled={!question.trim() || run.isPending} className="mt-3 inline-flex items-center gap-2 rounded-lg bg-brand-primary px-3 py-2 text-sm font-medium text-white disabled:opacity-40">{run.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Bot className="h-4 w-4" />}{run.isPending ? 'Panel is answering…' : 'Run Fusion'}</button></section>
      {result && <section className="space-y-3"><div className="rounded-xl border border-brand-primary/40 bg-brand-primary/5 p-4"><div className="mb-2 flex items-center gap-2 text-brand-cyan"><Trophy className="h-4 w-4" /><h2 className="text-sm font-semibold">Winner</h2></div><pre className="whitespace-pre-wrap text-sm leading-relaxed text-zinc-200">{result.winner_answer || result.judge_error || 'No winner returned.'}</pre>{result.verdict && <p className="mt-3 border-t border-zinc-800 pt-3 text-xs text-zinc-400">{result.verdict}</p>}<p className="mt-3 text-xs text-zinc-500">Total: {result.total_input_tokens.toLocaleString()} input · {result.total_output_tokens.toLocaleString()} output{result.total_cost_usd != null ? ` · $${result.total_cost_usd.toFixed(4)} estimated` : ''}</p></div><div className="grid gap-3 md:grid-cols-2">{result.results.map((item, index) => <article key={`${item.provider}-${index}`} className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4"><div className="mb-2 flex items-center justify-between gap-2"><span className="font-mono text-xs text-brand-cyan">{item.provider}:{item.model}</span><span className={item.error ? 'text-xs text-red-400' : 'text-xs text-emerald-400'}>{item.error || `${item.elapsed_s.toFixed(1)}s`}</span></div><pre className="max-h-64 overflow-auto whitespace-pre-wrap text-xs leading-relaxed text-zinc-300">{item.text || item.error}</pre><p className="mt-2 text-[11px] text-zinc-500">{item.input_tokens.toLocaleString()} input · {item.output_tokens.toLocaleString()} output · {item.elapsed_s.toFixed(1)}s</p></article>)}</div></section>}
    </div>
  )
}
