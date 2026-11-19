import { useEffect, useState } from 'react'
import { AlertTriangle, Check, Copy } from 'lucide-react'
import { useSessionStore } from '@/stores/session'
import { cn } from '@/lib/utils'
import type { CapabilityRow } from '@/types/api'

/* ─────────────────────────────────────────────────────────
 * CAPABILITY BADGE — "⚠ 2 limited" in the content header
 * Reads status.capabilities (the server's degradation
 * registry). /api/status refetches and the `capabilities`
 * websocket event both land in the session store, so the
 * badge updates without polling. Hidden when all is ok.
 * ───────────────────────────────────────────────────────── */

function CopyFix({ fix }: { fix: string }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    void navigator.clipboard?.writeText(fix).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    }).catch(() => undefined)
  }
  return (
    <div className="mt-1 flex items-start gap-1">
      <code className="min-w-0 flex-1 break-all rounded bg-zinc-100 px-1.5 py-0.5 font-mono text-[11px] text-zinc-700 dark:bg-zinc-800 dark:text-zinc-200">{fix}</code>
      <button
        type="button"
        aria-label="Copy fix"
        title="Copy"
        onClick={copy}
        className="shrink-0 rounded p-0.5 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
      >
        {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
      </button>
    </div>
  )
}

function CapabilityItem({ row }: { row: CapabilityRow }) {
  const unavailable = row.state === 'unavailable'
  const showPreferred = row.preferred && row.preferred !== row.active
  return (
    <li className="border-t border-zinc-100 py-2 first:border-t-0 dark:border-zinc-800">
      <div className="flex items-baseline gap-1.5">
        <span className={cn('text-xs font-medium', unavailable ? 'text-red-600 dark:text-red-400' : 'text-amber-600 dark:text-amber-400')}>
          {row.label}
        </span>
        <span className="truncate text-[11px] text-zinc-500">
          {unavailable && !row.active ? 'unavailable' : row.active || row.state}
          {showPreferred && <> (wanted {row.preferred})</>}
        </span>
      </div>
      {row.reason && <p className="mt-0.5 text-[11px] text-zinc-600 dark:text-zinc-400">{row.reason}</p>}
      {row.impact && <p className="mt-0.5 text-[11px] text-zinc-500">Impact: {row.impact}</p>}
      {row.fix && <CopyFix fix={row.fix} />}
    </li>
  )
}

export function CapabilityBadge() {
  const status = useSessionStore((state) => state.status)
  const [open, setOpen] = useState(false)
  const limited = (status?.capabilities ?? []).filter((row) => row.state !== 'ok')

  useEffect(() => {
    if (!open) return
    const close = (event: PointerEvent) => {
      if (!(event.target as Element)?.closest('[data-capability-menu]')) setOpen(false)
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', close)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('pointerdown', close)
      document.removeEventListener('keydown', escape)
    }
  }, [open])

  useEffect(() => {
    if (limited.length === 0) setOpen(false)
  }, [limited.length])

  if (limited.length === 0) return null
  const anyUnavailable = limited.some((row) => row.state === 'unavailable')

  return (
    <div data-capability-menu className="relative ml-auto">
      <button
        type="button"
        aria-expanded={open}
        aria-label={`${limited.length} capabilities limited`}
        onClick={() => setOpen((value) => !value)}
        title={limited.map((row) => `${row.label}: ${row.active || row.state}`).join('\n')}
        className={cn(
          'flex h-7 shrink-0 items-center gap-1 whitespace-nowrap rounded-md border px-2 text-[11px] font-medium transition-colors',
          anyUnavailable
            ? 'border-red-500/30 bg-red-500/10 text-red-600 hover:bg-red-500/15 dark:text-red-400'
            : 'border-amber-500/30 bg-amber-500/10 text-amber-600 hover:bg-amber-500/15 dark:text-amber-400',
        )}
      >
        <AlertTriangle className="h-3.5 w-3.5" />
        {limited.length} limited
      </button>
      {open && (
        <div className="absolute right-0 top-full z-30 mt-2 w-80 max-w-[calc(100vw-2rem)] rounded-xl border border-zinc-200 bg-white p-3 shadow-xl shadow-black/10 dark:border-zinc-800 dark:bg-zinc-900 dark:shadow-black/40">
          <p className="mb-1 text-xs font-medium text-zinc-800 dark:text-zinc-100">Running in a reduced mode</p>
          <ul className="max-h-80 overflow-auto">
            {limited.map((row) => <CapabilityItem key={row.name} row={row} />)}
          </ul>
          <p className="mt-1 text-[11px] text-zinc-400">Details: /doctor</p>
        </div>
      )}
    </div>
  )
}
