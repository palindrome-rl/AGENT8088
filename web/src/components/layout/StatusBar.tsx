import { useSessionStore } from '@/stores/session'
import { cn } from '@/lib/utils'

export function StatusBar() {
  const { status, isStreaming } = useSessionStore()
  if (!status) return null

  const modeColors: Record<string, string> = {
    'readonly': 'bg-yellow-500/20 text-yellow-400',
    'full-auto': 'bg-green-500/20 text-green-400',
    'plan-only': 'bg-purple-500/20 text-purple-400',
  }

  return (
    <div className="flex h-8 items-center gap-4 border-t border-zinc-800 bg-zinc-950 px-4 text-xs text-zinc-500">
      <span className="flex items-center gap-1.5">
        <span className={cn('h-2 w-2 rounded-full', isStreaming ? 'animate-pulse bg-brand-cyan' : 'bg-zinc-600')} />
        {isStreaming ? 'running' : 'ready'}
      </span>
      <span className="text-zinc-700">│</span>
      <span>{status.provider}:{status.model}</span>
      <span className="text-zinc-700">│</span>
      <span>{status.context_pct}% ctx</span>
      <span className="text-zinc-700">│</span>
      <span className={cn('rounded px-1.5 py-0.5 font-medium', modeColors[status.permission_mode])}>
        {status.permission_mode}
      </span>
      <span className="text-zinc-700">│</span>
      <span>{status.session_name || 'ephemeral'}</span>
      {status.last_usage && (
        <>
          <span className="text-zinc-700">│</span>
          <span>last {status.last_usage.seconds?.toFixed(1)}s ↑{status.last_usage.tokens}</span>
        </>
      )}
    </div>
  )
}