import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Stethoscope, Loader2, AlertCircle, Wrench, Download,
  CheckCircle2, XCircle, Activity, Network, KeyRound, Settings as SettingsIcon,
  Shield, Search, Terminal,
} from 'lucide-react'
import type { DoctorCheck } from '@/types/api'
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

interface FixResponse {
  ok?: boolean
  error?: string
}

// ---- Health indicator ----

type HealthState = 'ok' | 'warn' | 'fail' | 'unknown'

function classifyHealth(value: string): HealthState {
  const v = value.toLowerCase()
  if (v.includes('ok') || v.includes('ready') || v.includes('set') || v.includes('found') || v.includes('available on demand')) {
    if (v.includes('available on demand')) return 'warn'
    return 'ok'
  }
  if (v.includes('broken') || v.includes('missing') || v.includes('fail') || v.includes('error') || v.includes('not required')) return 'fail'
  if (v.includes('provider-managed') || v.includes('unknown')) return 'unknown'
  return 'warn'
}

function HealthDot({ state }: { state: HealthState }) {
  const colors: Record<HealthState, string> = {
    ok: 'bg-green-500',
    warn: 'bg-yellow-500',
    fail: 'bg-red-500',
    unknown: 'bg-zinc-500',
  }
  return <span className={cn('h-2.5 w-2.5 rounded-full', colors[state])} />
}

function HealthIcon({ state }: { state: HealthState }) {
  switch (state) {
    case 'ok':
      return <CheckCircle2 className="h-4 w-4 text-green-400" />
    case 'warn':
      return <AlertCircle className="h-4 w-4 text-yellow-400" />
    case 'fail':
      return <XCircle className="h-4 w-4 text-red-400" />
    case 'unknown':
      return <Activity className="h-4 w-4 text-zinc-500" />
  }
}

// ---- Check Row ----

const checkMeta: { key: keyof DoctorCheck; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { key: 'reachability', label: 'Reachability', icon: Network },
  { key: 'authentication', label: 'Authentication', icon: KeyRound },
  { key: 'configuration', label: 'Configuration', icon: SettingsIcon },
  { key: 'sandbox', label: 'Sandbox', icon: Shield },
  { key: 'capabilities', label: 'Capabilities', icon: Activity },
  { key: 'web_search', label: 'Web Search', icon: Search },
  { key: 'cli_anything', label: 'CLI Anything', icon: Terminal },
]

function CheckRow({ label, value, icon: Icon }: {
  label: string
  value: string
  icon: React.ComponentType<{ className?: string }>
}) {
  const state = classifyHealth(value)
  return (
    <tr className="border-b border-zinc-800/60 transition-colors hover:bg-zinc-800/20 last:border-0">
      <td className="px-4 py-3">
        <div className="flex items-center gap-2.5">
          <Icon className="h-4 w-4 text-zinc-500" />
          <span className="text-sm text-zinc-300">{label}</span>
        </div>
      </td>
      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          <HealthDot state={state} />
          <span className={cn('font-mono text-xs', state === 'fail' ? 'text-red-400' : state === 'ok' ? 'text-zinc-200' : 'text-zinc-400')}>
            {value}
          </span>
        </div>
      </td>
      <td className="px-4 py-3 text-right">
        <HealthIcon state={state} />
      </td>
    </tr>
  )
}

// ---- Summary cards ----

function SummaryCard({ label, value, icon: Icon, color }: {
  label: string
  value: string
  icon: React.ComponentType<{ className?: string }>
  color: string
}) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
      <div className="mb-2 flex items-center gap-2">
        <Icon className={cn('h-4 w-4', color)} />
        <span className="text-xs text-zinc-500">{label}</span>
      </div>
      <p className="truncate font-mono text-sm text-zinc-200">{value}</p>
    </div>
  )
}

// ---- Main Page ----

export default function DoctorPage() {
  const queryClient = useQueryClient()

  // Doctor health check
  const doctorQuery = useQuery({
    queryKey: ['doctor'],
    queryFn: () => fetchJSON<DoctorCheck>('/api/doctor'),
  })

  // Fix mutation
  const fixMutation = useMutation({
    mutationFn: () => postJSON<FixResponse>('/api/doctor/fix', { fix: true }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['doctor'] }),
  })

  // Dump download (not a query — triggered by button)
  const handleDump = async () => {
    try {
      const res = await fetch('/api/dump')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const text = await res.text()
      const blob = new Blob([text], { type: 'text/plain' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `agent8088-dump-${new Date().toISOString().slice(0, 19).replace(/[:.]/g, '-')}.txt`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (err) {
      console.error('Dump failed:', err)
    }
  }

  const data = doctorQuery.data

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Stethoscope className="h-6 w-6 text-brand-cyan" />
          <div>
            <h1 className="text-lg font-semibold text-zinc-100">Doctor</h1>
            <p className="text-xs text-zinc-500">Health diagnostics & repair</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => fixMutation.mutate()}
            disabled={fixMutation.isPending}
            className="flex items-center gap-1.5 rounded-lg bg-brand-primary px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-primary/80 disabled:opacity-40"
          >
            {fixMutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Wrench className="h-4 w-4" />
            )}
            Run Fix
          </button>
          <button
            onClick={handleDump}
            className="flex items-center gap-1.5 rounded-lg border border-zinc-700 bg-zinc-800/50 px-3 py-2 text-sm text-zinc-300 transition-colors hover:border-brand-primary/40 hover:text-brand-cyan"
          >
            <Download className="h-4 w-4" />
            Dump
          </button>
        </div>
      </div>

      {/* Fix feedback */}
      {fixMutation.data?.error && (
        <div className="flex items-center gap-2 rounded-lg border border-red-900/50 bg-red-950/30 p-3 text-xs text-red-400">
          <AlertCircle className="h-3.5 w-3.5 shrink-0" /> Fix failed: {fixMutation.data.error}
        </div>
      )}
      {fixMutation.isSuccess && !fixMutation.data?.error && (
        <div className="flex items-center gap-2 rounded-lg border border-green-900/50 bg-green-950/20 p-3 text-xs text-green-400">
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0" /> Fix completed — re-running diagnostics
        </div>
      )}

      {/* Loading */}
      {doctorQuery.isLoading && (
        <div className="flex items-center gap-2 rounded-xl border border-zinc-800 bg-zinc-900/50 p-8 text-sm text-zinc-400">
          <Loader2 className="h-4 w-4 animate-spin text-brand-cyan" /> Running diagnostics…
        </div>
      )}

      {/* Error */}
      {doctorQuery.isError && !doctorQuery.isLoading && (
        <div className="flex items-center gap-2 rounded-xl border border-red-900/50 bg-red-950/30 p-6 text-sm text-red-400">
          <AlertCircle className="h-4 w-4 shrink-0" /> Failed to run diagnostics
        </div>
      )}

      {/* Content */}
      {data && !doctorQuery.isLoading && !doctorQuery.isError && (
        <>
          {/* Summary cards */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <SummaryCard
              label="Model"
              value={data.model}
              icon={Activity}
              color="text-brand-cyan"
            />
            <SummaryCard
              label="Endpoint"
              value={data.endpoint}
              icon={Network}
              color="text-zinc-400"
            />
            <SummaryCard
              label="Sandbox"
              value={data.sandbox}
              icon={Shield}
              color="text-zinc-400"
            />
          </div>

          {/* Health check table */}
          <div className="overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900/50">
            <div className="border-b border-zinc-800 px-5 py-3">
              <h3 className="flex items-center gap-2 text-sm font-semibold text-zinc-200">
                <Stethoscope className="h-4 w-4 text-brand-cyan" />
                Health Checks
              </h3>
            </div>
            <table className="w-full">
              <thead>
                <tr className="border-b border-zinc-800 text-left text-xs uppercase tracking-wider text-zinc-600">
                  <th className="px-4 py-3 font-medium">Check</th>
                  <th className="px-4 py-3 font-medium">Result</th>
                  <th className="px-4 py-3 text-right font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {checkMeta.map(({ key, label, icon }) => (
                  <CheckRow
                    key={key}
                    label={label}
                    value={data[key] ?? 'unknown'}
                    icon={icon}
                  />
                ))}
              </tbody>
            </table>
          </div>

          {/* Overall status */}
          <OverallStatus data={data} />
        </>
      )}
    </div>
  )
}

// ---- Overall status banner ----

function OverallStatus({ data }: { data: DoctorCheck }) {
  const states = checkMeta.map(({ key }) => classifyHealth(data[key] ?? 'unknown'))
  const hasFail = states.includes('fail')
  const hasWarn = states.includes('warn')
  const allOk = states.every((s) => s === 'ok')

  const bannerClass = allOk
    ? 'border-green-900/50 bg-green-950/20 text-green-400'
    : hasFail
      ? 'border-red-900/50 bg-red-950/30 text-red-400'
      : hasWarn
        ? 'border-yellow-900/50 bg-yellow-950/20 text-yellow-400'
        : 'border-zinc-800 bg-zinc-900/50 text-zinc-400'

  const Icon = allOk ? CheckCircle2 : hasFail ? XCircle : AlertCircle
  const message = allOk
    ? 'All checks passed — system is healthy'
    : hasFail
      ? 'One or more checks failed — run Fix or review configuration'
      : 'Some checks need attention — review warnings below'

  return (
    <div className={cn('flex items-center gap-3 rounded-xl border p-4', bannerClass)}>
      <Icon className="h-5 w-5 shrink-0" />
      <div>
        <p className="text-sm font-medium">{message}</p>
        <p className="text-xs opacity-70">
          {states.filter((s) => s === 'ok').length} ok · {' '}
          {states.filter((s) => s === 'warn').length} warnings · {' '}
          {states.filter((s) => s === 'fail').length} failures
        </p>
      </div>
    </div>
  )
}