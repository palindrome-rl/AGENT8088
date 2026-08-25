import { useSessionStore } from '@/stores/session'
import { cn } from '@/lib/utils'

export function StatusBar() {
  const { status, isStreaming } = useSessionStore()
  if (!status) return null

  const modeColors: Record<string, string> = {
    'readonly': 'bg-yellow-500/10 text-yellow-400',
    'full-auto': 'bg-green-500/10 text-green-400',
    'plan-only': 'bg-purple-500/10 text-purple-400',
  }

  return (
    <div className="flex h-7 items-center gap-3 border-t border-zinc-800/60 bg-zinc-950 px-3 text-[11px] text-zinc-500">
      <span className="flex items-center gap-1.5">
        <span className={cn('h-1.5 w-1.5 rounded-full', isStreaming ? 'animate-pulse bg-brand-cyan' : 'bg-zinc-600')} />
        {isStreaming ? 'running' : 'ready'}
      </span>
      <span className="text-zinc-700">·</span>
      <span className="truncate">{status.provider}:{status.model}</span>
      <span className="text-zinc-700">·</span>
      <span>{status.context_pct}% ctx</span>
      <span className="text-zinc-700">·</span>
      <span className={cn('rounded px-1.5 py-0.5 font-medium', modeColors[status.permission_mode])}>
        {status.permission_mode}
      </span>
      <span className="text-zinc-700">·</span>
      <span className="truncate">{status.session_name || 'ephemeral'}</span>
      {status.last_usage && (
        <>
          <span className="text-zinc-700">·</span>
          <span>{status.last_usage.seconds?.toFixed(1)}s ↑{status.last_usage.tokens}</span>
        </>
      )}
    </div>
  )
}