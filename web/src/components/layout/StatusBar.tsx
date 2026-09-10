import { useSessionStore } from '@/stores/session'
import { cn } from '@/lib/utils'

const CONNECTION_LABEL = {
  connecting: 'connecting…',
  reconnecting: 'reconnecting…',
  failed: 'disconnected',
  open: '',
} as const

export function StatusBar() {
  const { status, isStreaming, connection } = useSessionStore()
  if (!status) return null
  const offline = connection !== 'open'

  const modeColors: Record<string, string> = {
    'readonly': 'bg-yellow-500/10 text-yellow-600 dark:text-yellow-400',
    'full-auto': 'bg-green-500/10 text-green-600 dark:text-green-400',
    'plan-only': 'bg-purple-500/10 text-purple-600 dark:text-purple-400',
  }

  return (
    <div className="flex h-7 items-center gap-3 border-t border-zinc-200 dark:border-zinc-800/60 bg-white dark:bg-zinc-950 px-3 text-[11px] text-zinc-400 dark:text-zinc-500">
      <span
        className={cn('flex items-center gap-1.5', offline && 'font-medium text-red-600 dark:text-red-400')}
        role={offline ? 'status' : undefined}
        aria-live={offline ? 'polite' : undefined}
      >
        <span className={cn(
          'h-1.5 w-1.5 rounded-full',
          offline
            ? connection === 'failed' ? 'bg-red-500' : 'animate-pulse bg-amber-500'
            : isStreaming ? 'animate-pulse bg-brand-cyan' : 'bg-zinc-400 dark:bg-zinc-400',
        )} />
        {offline ? CONNECTION_LABEL[connection] : isStreaming ? 'running' : 'ready'}
      </span>
      <span className="text-zinc-300 dark:text-zinc-700">·</span>
      <span className="truncate">{status.provider}:{status.model}</span>
      <span className="text-zinc-300 dark:text-zinc-700">·</span>
      <span>{status.context_pct}% ctx</span>
      <span className="text-zinc-300 dark:text-zinc-700">·</span>
      <span className={cn('rounded px-1.5 py-0.5 font-medium', modeColors[status.permission_mode])}>
        {status.permission_mode}
      </span>
      <span className="text-zinc-300 dark:text-zinc-700">·</span>
      <span className="truncate">{status.session_name || 'ephemeral'}</span>
      {status.last_usage && (
        <>
          <span className="text-zinc-300 dark:text-zinc-700">·</span>
          <span>{status.last_usage.seconds?.toFixed(1)}s ↑{status.last_usage.tokens}</span>
        </>
      )}
      {status.rate_limit_status?.pct_remaining != null && (
        <>
          <span className="text-zinc-300 dark:text-zinc-700">·</span>
          <span>⚡{status.rate_limit_status.pct_remaining}%</span>
        </>
      )}
      {status.rate_limit_status?.balance && (
        <>
          <span className="text-zinc-300 dark:text-zinc-700">·</span>
          <span>⚡${status.rate_limit_status.balance.amount.toFixed(2)}</span>
        </>
      )}
    </div>
  )
}