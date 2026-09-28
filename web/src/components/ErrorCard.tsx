/**
 * The one error surface every page uses.
 *
 * It always answers the four questions the audit graded against — what went
 * wrong, why, what to do next, and whether retrying is safe — and it carries
 * role="alert" so a screen reader is told a load failed instead of the page
 * silently rendering an empty list.
 */
import { AlertTriangle, RotateCcw, WifiOff, ShieldAlert, SearchX, Ban } from 'lucide-react'
import { asApiError, type ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'

const ICONS = {
  offline: WifiOff,
  timeout: WifiOff,
  auth: ShieldAlert,
  notFound: SearchX,
  conflict: Ban,
  invalid: Ban,
  unavailable: AlertTriangle,
  server: AlertTriangle,
  malformed: AlertTriangle,
  unknown: AlertTriangle,
} as const

interface ErrorCardProps {
  /** Anything React Query or a mutation threw. */
  error: unknown
  /** What the page was doing, e.g. "load tools" — used when the server gave no message. */
  action?: string
  /** Wire this to `query.refetch` / `mutation.reset` to offer a real retry. */
  onRetry?: () => void
  /** `inline` sits inside a panel; `block` stands alone in the content area. */
  variant?: 'block' | 'inline'
  className?: string
}

export function ErrorCard({
  error, action = 'complete that request', onRetry, variant = 'block', className,
}: ErrorCardProps) {
  if (!error) return null
  const details: ApiError = asApiError(error, action)
  const Icon = ICONS[details.kind] ?? AlertTriangle

  return (
    <div
      role="alert"
      aria-live="assertive"
      className={cn(
        'flex gap-3 rounded-xl border border-red-500/30 bg-red-500/5 text-sm',
        variant === 'block' ? 'p-4' : 'p-3',
        className,
      )}
    >
      <Icon className="mt-0.5 h-4 w-4 shrink-0 text-red-500 dark:text-red-400" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="font-medium text-red-700 dark:text-red-300">{details.message}</p>

        {details.cause && (
          <p className="mt-1 text-xs text-zinc-600 dark:text-zinc-400">{details.cause}</p>
        )}

        <p className="mt-2 text-[13px] text-zinc-700 dark:text-zinc-300">{details.nextAction}</p>

        <div className="mt-3 flex flex-wrap items-center gap-3">
          {onRetry && details.retrySafe && (
            <button
              type="button"
              onClick={onRetry}
              className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-300 px-2.5 py-1.5 text-xs font-medium text-zinc-700 transition-colors hover:border-red-500/50 hover:text-red-700 dark:border-zinc-700 dark:text-zinc-200 dark:hover:border-red-400/50 dark:hover:text-red-300"
            >
              <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
              Try again
            </button>
          )}
          <p className="text-xs text-zinc-500 dark:text-zinc-500">
            {details.retrySafe
              ? 'Retrying is safe — nothing was changed.'
              : 'Fix the input before resubmitting; do not retry as-is.'}
          </p>
        </div>
      </div>
    </div>
  )
}

/**
 * Distinguishes "this list is genuinely empty" from "we could not load it".
 * Pass the query's error and it renders the error instead of the empty copy —
 * the Schedules page rendered a 500 as "No scheduled tasks." for want of this.
 */
export function EmptyOrError({
  error, isLoading, isEmpty, emptyText, action, onRetry, children,
}: {
  error: unknown
  isLoading?: boolean
  isEmpty: boolean
  emptyText: string
  action?: string
  onRetry?: () => void
  children?: React.ReactNode
}) {
  if (error) {
    return <ErrorCard error={error} action={action} onRetry={onRetry} variant="inline" className="m-4" />
  }
  if (isLoading) return null
  if (isEmpty) {
    return <p className="p-8 text-center text-sm text-zinc-500 dark:text-zinc-500">{emptyText}</p>
  }
  return <>{children}</>
}
