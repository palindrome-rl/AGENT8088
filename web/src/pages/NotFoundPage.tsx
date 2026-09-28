/**
 * The catch-all route.
 *
 * App.tsx had no `path="*"`, and because AppLayout is the parent route element
 * an unmatched child rendered nothing at all — a typo or a stale bookmark gave
 * a completely blank document with no sidebar and no way back.
 */
import { Link, useLocation } from 'react-router-dom'
import { Compass, ArrowLeft } from 'lucide-react'

export default function NotFoundPage() {
  const { pathname } = useLocation()
  return (
    <div className="mx-auto flex max-w-xl flex-col items-start gap-4 p-6 sm:p-10">
      <div className="flex items-center gap-2.5">
        <Compass className="h-5 w-5 text-brand-cyan" aria-hidden="true" />
        <h1 className="text-lg font-semibold text-zinc-900 dark:text-zinc-100">
          That page does not exist
        </h1>
      </div>

      <div role="alert" className="w-full rounded-xl border border-zinc-200 bg-zinc-50 p-4 dark:border-zinc-800 dark:bg-zinc-900/50">
        <p className="text-sm text-zinc-700 dark:text-zinc-300">
          Nothing is routed at{' '}
          <code className="rounded bg-zinc-200 px-1.5 py-0.5 font-mono text-[13px] text-zinc-800 dark:bg-zinc-800 dark:text-zinc-200">
            {pathname}
          </code>
          .
        </p>
        <p className="mt-2 text-[13px] text-zinc-600 dark:text-zinc-400">
          The link may be out of date, or the address may have a typo. Your session is
          untouched — nothing was lost.
        </p>
      </div>

      <Link
        to="/"
        className="inline-flex items-center gap-1.5 rounded-lg bg-brand-primary px-3 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90"
      >
        <ArrowLeft className="h-4 w-4" aria-hidden="true" />
        Back to chat
      </Link>
    </div>
  )
}
