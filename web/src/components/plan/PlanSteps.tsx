import { useSessionStore } from '@/stores/session'
import { cn } from '@/lib/utils'

const PLAN_ICONS: Record<string, string> = {
  pending: '○',
  running: '◐',
  done: '✓',
  failed: '✗',
}

const PLAN_COLORS: Record<string, string> = {
  pending: 'text-zinc-500',
  running: 'text-brand-cyan animate-pulse',
  done: 'text-green-500',
  failed: 'text-red-500',
}

export function PlanSteps() {
  const { planSteps } = useSessionStore()
  if (!planSteps.length) return null

  return (
    <div className="mx-4 my-2 space-y-1">
      <div className="mb-2 text-xs font-semibold text-zinc-400">Plan Execution</div>
      {planSteps
        .sort((a, b) => a.index - b.index)
        .map((step) => (
          <div
            key={step.index}
            className="flex items-start gap-2 rounded-lg border border-zinc-800 bg-zinc-900/50 px-3 py-2"
          >
            <span className={cn('font-mono text-sm', PLAN_COLORS[step.status])}>
              {PLAN_ICONS[step.status]}
            </span>
            <div className="flex-1 min-w-0">
              <div className="flex items-baseline gap-2">
                <span className="text-xs text-zinc-600">[{step.index}]</span>
                <span className="font-mono text-xs font-medium text-zinc-300">{step.toolName}</span>
              </div>
              <div className="text-sm text-zinc-400 truncate">{step.stepText}</div>
              {step.result && (
                <pre className="mt-1 max-h-24 overflow-auto rounded bg-zinc-950 p-1.5 font-mono text-xs text-zinc-500">
                  {step.result.slice(0, 500)}
                </pre>
              )}
            </div>
          </div>
        ))}
    </div>
  )
}