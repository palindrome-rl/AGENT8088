import { Fragment, useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import {
  Wrench, Play, X, Loader2, CheckCircle2, XCircle, ChevronDown, ChevronRight,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import type { ToolSpec } from '@/types/api'

/* ------------------------------------------------------------------ */
/* API helpers                                                         */
/* ------------------------------------------------------------------ */

async function fetchTools(): Promise<ToolSpec[]> {
  const res = await fetch('/api/tools')
  if (!res.ok) throw new Error(`Failed to load tools (${res.status})`)
  return res.json() as Promise<ToolSpec[]>
}

type ToolRun = { name: string; result: unknown; approval_required?: boolean; approval_id?: string }
async function invokeTool(name: string, args: Record<string, unknown>): Promise<ToolRun> {
  const res = await fetch(`/api/tool/${encodeURIComponent(name)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(args),
  })
  if (!res.ok) throw new Error(`Invocation failed (${res.status})`)
  return res.json() as Promise<ToolRun>
}

async function approveTool(id: string, approved: boolean): Promise<ToolRun> {
  const res = await fetch(`/api/tool/approval/${id}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ approved }) })
  if (!res.ok) throw new Error(`Approval failed (${res.status})`)
  return res.json() as Promise<ToolRun>
}

/* ------------------------------------------------------------------ */
/* Small presentational helpers                                        */
/* ------------------------------------------------------------------ */

function ModeBadge({ mode }: { mode: string }) {
  const styles: Record<string, string> = {
    local: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
    remote: 'bg-sky-500/15 text-sky-400 border-sky-500/30',
    mcp: 'bg-violet-500/15 text-violet-400 border-violet-500/30',
    builtin: 'bg-zinc-500/15 text-zinc-300 border-zinc-500/30',
  }
  return (
    <span className={cn(
      'inline-block rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide',
      styles[mode] ?? 'bg-zinc-700/30 text-zinc-400 border-zinc-700',
    )}>
      {mode || '—'}
    </span>
  )
}

function EnabledDot({ enabled }: { enabled: boolean }) {
  return (
    <span className="flex items-center gap-1.5 text-xs">
      <span className={cn('h-2 w-2 rounded-full', enabled ? 'bg-emerald-500' : 'bg-zinc-600')} />
      {enabled ? 'on' : 'off'}
    </span>
  )
}

/* ------------------------------------------------------------------ */
/* Tool invoker modal                                                  */
/* ------------------------------------------------------------------ */

interface InvokerModalProps {
  tool: ToolSpec
  onClose: () => void
}

function InvokerModal({ tool, onClose }: InvokerModalProps) {
  const allArgs = [...(tool.args ?? []), ...(tool.optional ?? [])]
  const [argValues, setArgValues] = useState<Record<string, string>>({})

  const mutation = useMutation({
    mutationFn: (args: Record<string, unknown>) => invokeTool(tool.name, args),
  })
  const approval = useMutation({ mutationFn: ({ id, approved }: { id: string; approved: boolean }) => approveTool(id, approved) })

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const parsed: Record<string, unknown> = {}
    for (const key of allArgs) {
      const raw = argValues[key] ?? ''
      if (raw === '') continue
      // best-effort JSON parse, fall back to string
      try {
        parsed[key] = JSON.parse(raw)
      } catch {
        parsed[key] = raw
      }
    }
    mutation.mutate(parsed)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
         onClick={onClose}>
      <div className="w-full max-w-lg rounded-xl border border-zinc-800 bg-zinc-950 shadow-2xl"
           onClick={(e) => e.stopPropagation()}>
        {/* header */}
        <div className="flex items-center justify-between border-b border-zinc-800 px-5 py-3">
          <div className="flex items-center gap-2">
            <Wrench className="h-4 w-4 text-brand-cyan" />
            <h3 className="text-sm font-semibold text-zinc-100">{tool.name}</h3>
            <ModeBadge mode={tool.mode} />
          </div>
          <button type="button" aria-label="Close tool invoker" onClick={onClose} className="text-zinc-500 hover:text-zinc-200">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* description */}
        {tool.description && (
          <p className="px-5 py-3 text-xs leading-relaxed text-zinc-400">{tool.description}</p>
        )}

        {/* form */}
        <form onSubmit={handleSubmit} className="space-y-3 px-5 py-4">
          {allArgs.length === 0 ? (
            <p className="text-xs text-zinc-500">This tool takes no arguments.</p>
          ) : (
            allArgs.map((arg) => {
              const isOptional = (tool.optional ?? []).includes(arg)
              const typeHint = tool.arg_types?.[arg] ?? 'string'
              return (
                <div key={arg}>
                  <label className="mb-1 flex items-center gap-1.5 text-xs font-medium text-zinc-300">
                    <span>{arg}</span>
                    <span className="text-[10px] text-zinc-600">:{typeHint}</span>
                    {isOptional && <span className="text-[10px] text-zinc-600">(optional)</span>}
                  </label>
                  <input
                    value={argValues[arg] ?? ''}
                    onChange={(e) => setArgValues((s) => ({ ...s, [arg]: e.target.value }))}
                    placeholder={`Enter ${arg}…`}
                    className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-1.5 text-sm text-zinc-100 outline-none transition-colors placeholder:text-zinc-600 focus:border-brand-primary"
                  />
                </div>
              )
            })
          )}

          {/* result area */}
          {mutation.isPending && (
            <div className="flex items-center gap-2 rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-xs text-zinc-400">
              <Loader2 className="h-3.5 w-3.5 animate-spin text-brand-cyan" />
              Running {tool.name}…
            </div>
          )}
          {mutation.isError && (
            <div className="flex items-start gap-2 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-400">
              <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span className="break-all">{(mutation.error as Error)?.message ?? 'Invocation failed'}</span>
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
              {mutation.data?.approval_required && mutation.data.approval_id && <div className="mt-2 flex gap-2"><button type="button" onClick={() => approval.mutate({ id: mutation.data!.approval_id!, approved: true })} className="rounded bg-amber-500/20 px-2 py-1 text-xs text-amber-300">Approve & run</button><button type="button" onClick={() => approval.mutate({ id: mutation.data!.approval_id!, approved: false })} className="rounded border border-zinc-700 px-2 py-1 text-xs text-zinc-400">Cancel</button></div>}
              {approval.data && <pre className="mt-2 max-h-48 overflow-auto text-[11px] text-zinc-300">{typeof approval.data.result === 'string' ? approval.data.result : JSON.stringify(approval.data.result, null, 2)}</pre>}
            </div>
          )}

          {/* actions */}
          <div className="flex justify-end gap-2 pt-1">
            <button type="button" onClick={onClose}
              className="rounded-md border border-zinc-800 px-3 py-1.5 text-xs text-zinc-400 hover:bg-zinc-800/50">
              Close
            </button>
            <button type="submit" disabled={mutation.isPending}
              className="flex items-center gap-1.5 rounded-md bg-brand-primary px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-brand-primary/90 disabled:opacity-50">
              <Play className="h-3.5 w-3.5" />
              Run
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

export default function ToolsPage() {
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [invokerTool, setInvokerTool] = useState<ToolSpec | null>(null)
  const [filter, setFilter] = useState('')

  const { data: tools, isLoading, isError, error } = useQuery({
    queryKey: ['tools'],
    queryFn: fetchTools,
  })

  function toggle(name: string) {
    setExpanded((s) => {
      const next = new Set(s)
      next.has(name) ? next.delete(name) : next.add(name)
      return next
    })
  }

  const filtered = (tools ?? []).filter((t) =>
    !filter || t.name.toLowerCase().includes(filter.toLowerCase()) || t.mode.toLowerCase().includes(filter.toLowerCase()) || t.category?.toLowerCase().includes(filter.toLowerCase()),
  )

  return (
    <div className="mx-auto max-w-6xl p-6">
      {/* header */}
      <div className="mb-6 flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <Wrench className="h-5 w-5 text-brand-cyan" />
          <h1 className="text-lg font-semibold text-zinc-100">Tools</h1>
          {tools && (
            <span className="rounded-full bg-zinc-800 px-2 py-0.5 text-[11px] text-zinc-400">
              {tools.length}
            </span>
          )}
        </div>
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter…"
          className="w-48 rounded-md border border-zinc-800 bg-zinc-900 px-3 py-1.5 text-sm text-zinc-100 outline-none transition-colors placeholder:text-zinc-600 focus:border-brand-primary"
        />
      </div>

      {/* loading */}
      {isLoading && (
        <div className="flex items-center justify-center py-20">
          <Loader2 className="h-6 w-6 animate-spin text-brand-cyan" />
        </div>
      )}

      {/* error */}
      {isError && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-400">
          {(error as Error)?.message ?? 'Failed to load tools.'}
        </div>
      )}

      {/* table */}
      {tools && filtered.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-zinc-800">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-800 bg-zinc-900/50 text-left text-xs uppercase tracking-wide text-zinc-500">
                <th className="w-8 px-3 py-2.5" />
                <th className="px-3 py-2.5 font-medium">Name</th>
                <th className="px-3 py-2.5 font-medium">Mode</th>
                <th className="px-3 py-2.5 font-medium">Group</th>
                <th className="px-3 py-2.5 font-medium">Args</th>
                <th className="px-3 py-2.5 font-medium">Description</th>
                <th className="px-3 py-2.5 font-medium">Enabled</th>
                <th className="px-3 py-2.5" />
              </tr>
            </thead>
            <tbody>
              {filtered.map((tool) => {
                const isOpen = expanded.has(tool.name)
                return (
                  <Fragment key={tool.name}>
                    <tr className="border-b border-zinc-800/50 transition-colors hover:bg-zinc-900/30">
                      <td className="px-3 py-2.5">
                        <button
                          type="button"
                          aria-label={`${isOpen ? 'Collapse' : 'Expand'} ${tool.name}`}
                          onClick={() => toggle(tool.name)}
                          className="text-zinc-500 hover:text-zinc-200"
                        >
                          {isOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                        </button>
                      </td>
                      <td className="px-3 py-2.5 font-mono text-[13px] text-zinc-100">{tool.name}</td>
                      <td className="px-3 py-2.5"><ModeBadge mode={tool.mode} /></td>
                      <td className="px-3 py-2.5 text-xs text-zinc-500">{tool.category || 'other'}</td>
                      <td className="px-3 py-2.5 text-xs text-zinc-400">
                        {(tool.args ?? []).length > 0 ? (tool.args ?? []).join(', ') : '—'}
                      </td>
                      <td className="max-w-xs truncate px-3 py-2.5 text-xs text-zinc-400" title={tool.description}>
                        {tool.description || '—'}
                      </td>
                      <td className="px-3 py-2.5"><EnabledDot enabled={tool.enabled} /></td>
                      <td className="px-3 py-2.5">
                        <button
                          type="button"
                          onClick={() => setInvokerTool(tool)}
                          className="flex items-center gap-1 rounded border border-zinc-800 px-2 py-1 text-[11px] text-zinc-300 transition-colors hover:border-brand-primary hover:text-brand-cyan"
                        >
                          <Play className="h-3 w-3" /> Run
                        </button>
                      </td>
                    </tr>
                    {isOpen && (
                      <tr className="bg-zinc-950">
                        <td colSpan={8} className="px-3 py-3">
                          <div className="ml-6 space-y-2 border-l-2 border-brand-primary/30 pl-4 text-xs">
                            <div className="flex gap-2">
                              <span className="w-20 shrink-0 text-zinc-500">Timeout</span>
                              <span className="text-zinc-300">{tool.timeout}s</span>
                            </div>
                            {(tool.args ?? []).length > 0 && (
                              <div className="flex gap-2">
                                <span className="w-20 shrink-0 text-zinc-500">Required</span>
                                <span className="font-mono text-zinc-300">{(tool.args ?? []).join(', ')}</span>
                              </div>
                            )}
                            {(tool.optional ?? []).length > 0 && (
                              <div className="flex gap-2">
                                <span className="w-20 shrink-0 text-zinc-500">Optional</span>
                                <span className="font-mono text-zinc-300">{(tool.optional ?? []).join(', ')}</span>
                              </div>
                            )}
                            {tool.arg_types && Object.keys(tool.arg_types).length > 0 && (
                              <div className="flex gap-2">
                                <span className="w-20 shrink-0 text-zinc-500">Types</span>
                                <span className="font-mono text-zinc-300">
                                  {Object.entries(tool.arg_types).map(([k, v]) => `${k}:${v}`).join(', ')}
                                </span>
                              </div>
                            )}
                            {tool.path_arg && (
                              <div className="flex gap-2">
                                <span className="w-20 shrink-0 text-zinc-500">Path arg</span>
                                <span className="font-mono text-zinc-300">{tool.path_arg}</span>
                              </div>
                            )}
                            {tool.content_arg && (
                              <div className="flex gap-2">
                                <span className="w-20 shrink-0 text-zinc-500">Content arg</span>
                                <span className="font-mono text-zinc-300">{tool.content_arg}</span>
                              </div>
                            )}
                            <div className="flex gap-2">
                              <span className="w-20 shrink-0 text-zinc-500">Full desc</span>
                              <span className="leading-relaxed text-zinc-400">{tool.description || '—'}</span>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* empty */}
      {tools && filtered.length === 0 && (
        <div className="py-20 text-center text-sm text-zinc-500">
          {filter ? `No tools match "${filter}".` : 'No tools registered.'}
        </div>
      )}

      {/* invoker modal */}
      {invokerTool && <InvokerModal tool={invokerTool} onClose={() => setInvokerTool(null)} />}
    </div>
  )
}
