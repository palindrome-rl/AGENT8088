import { useState } from 'react'
import { ChevronDown, ChevronRight, Wrench, Check, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'

/* ─────────────────────────────────────────────────────────
 * TOOL CHIPS — Beautiful UI style
 * Compact expandable rows: icon + label + chip,
 * hover reveals chevron, click expands detail.
 * ───────────────────────────────────────────────────────── */

interface ToolChipProps {
  name: string
  status: 'running' | 'done'
  result?: string
}

export function ToolChip({ name, status, result }: ToolChipProps) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="w-full">
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
        className="group flex w-full items-center gap-2 rounded-lg px-1.5 py-1 text-left transition-colors duration-100 hover:bg-zinc-100 dark:hover:bg-zinc-800/40"
      >
        {/* Icon — switches between status icon and chevron on hover/expand */}
        <span className="relative flex h-4 w-4 shrink-0 items-center justify-center text-zinc-400 dark:text-zinc-500">
          {expanded ? (
            <ChevronDown className="h-3 w-3 transition-opacity duration-150" />
          ) : (
            <>
              {status === 'running' ? (
                <Loader2 className="h-3 w-3 animate-spin text-brand-cyan" />
              ) : (
                <Check className="h-3 w-3 text-green-500 opacity-0 transition-opacity duration-100 group-hover:opacity-0" style={{ opacity: 1 }} />
              )}
              <ChevronRight className="absolute h-3 w-3 opacity-0 transition-opacity duration-100 group-hover:opacity-100" />
            </>
          )}
        </span>

        {/* Label */}
        <span className="shrink-0 text-[12.5px] font-medium text-zinc-700 dark:text-zinc-200">
          {name}
        </span>

        {/* Chip — truncated mono text */}
        {result && (
          <span
            className="inline-flex h-5 min-w-0 flex-1 cursor-pointer items-center truncate rounded bg-zinc-100 dark:bg-zinc-800/60 px-1.5 font-mono text-[11px] text-zinc-500 dark:text-zinc-400 shadow-sm transition-colors duration-100 hover:bg-zinc-200 dark:hover:bg-zinc-800"
          >
            {result.slice(0, 80)}
          </span>
        )}
      </button>

      {/* Expanded detail */}
      <div
        className="grid transition-[grid-template-rows,opacity] duration-300"
        style={{
          gridTemplateRows: expanded ? '1fr' : '0fr',
          opacity: expanded ? 1 : 0,
          transitionTimingFunction: 'cubic-bezier(0.23, 1, 0.32, 1)',
        }}
      >
        <div className="min-h-0 overflow-hidden">
          <div className="mt-0.5 mb-1 ml-2 flex flex-col gap-0.5 border-l border-zinc-200 dark:border-zinc-800 py-0.5 pl-3.5">
            {result && result.split('\n').slice(0, 10).map((line, i) => (
              <span
                key={i}
                className={cn(
                  'truncate text-[11px] leading-relaxed font-mono',
                  line.startsWith('+') ? 'text-green-500' :
                  line.startsWith('-') ? 'text-red-400' :
                  'text-zinc-500 dark:text-zinc-400'
                )}
              >
                {line}
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}