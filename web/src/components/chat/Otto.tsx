import { useMemo } from 'react'
import { useSessionStore } from '@/stores/session'
import { useUIStore } from '@/stores/ui'
import { cn } from '@/lib/utils'

/* ─────────────────────────────────────────────────────────
 * OTTO — the Agent8088 mascot. One flat-color SVG octopus
 * living at the composer's bottom-right, next to Send.
 * It doubles as a living status light: 8 arms for the agent's
 * parallel tool calls, and the animation is driven entirely
 * by a data-otto attribute + CSS keyframes (no JS animation,
 * no re-render churn beyond state flips).
 * ───────────────────────────────────────────────────────── */

export type OttoState =
  | 'idle'
  | 'thinking'
  | 'tool'
  | 'writing'
  | 'approval'
  | 'error'
  | 'offline'

/** Low-frequency store slices only — never token-level fields,
 *  so Otto does not re-render on every streamed delta. */
export function useOttoState(): OttoState {
  const connection = useSessionStore((s) => s.connection)
  const sendFailure = useSessionStore((s) => s.sendFailure)
  const isStreaming = useSessionStore((s) => s.isStreaming)
  const activity = useSessionStore((s) => s.activity)
  const toolRunning = useSessionStore((s) => s.toolEvents.some((e) => e.status === 'running'))
  const approvalPending = useUIStore((s) => s.approvalPending)
  const planApprovalPending = useUIStore((s) => s.planApprovalPending)

  return useMemo(() => {
    // Dead socket is the loudest fact — everything else can wait.
    if (connection === 'failed' || connection === 'reconnecting') return 'offline'
    if (approvalPending || planApprovalPending) return 'approval'
    if (sendFailure) return 'error'
    if (!isStreaming) return 'idle'
    if (toolRunning) return 'tool'
    if (activity === 'Writing answer') return 'writing'
    return 'thinking'
  }, [connection, sendFailure, isStreaming, activity, toolRunning, approvalPending, planApprovalPending])
}

/** Six visible arms (the head hides two — cuteness over anatomy). */
const ARMS = [
  'M14 30 q-4 4 -7 5',
  'M17 33 q-2.5 4 -5.5 6.5',
  'M21 35 q-1 3.5 -2.5 6.5',
  'M27 35 q1 3.5 2.5 6',
  'M31 33 q2.5 4 5.5 6.5',
  'M34 30 q4 4 7 5',
]

function Mouth({ state }: { state: OttoState }) {
  const stroke = { fill: 'none', stroke: '#131316', strokeWidth: 1.6, strokeLinecap: 'round' as const }
  switch (state) {
    case 'thinking':
      return <ellipse cx="24" cy="30.8" rx="1.7" ry="1.9" fill="#131316" opacity="0.85" />
    case 'tool':
      return <path d="M21.6 30.8 q2.4 1.5 4.8 0" {...stroke} />
    case 'approval':
      return <path d="M19.8 29.9 q4.2 3.2 8.4 0" {...stroke} />
    case 'error':
      return <path d="M21.2 31.4 q2.8 -2 5.6 0" {...stroke} />
    case 'offline':
      return <path d="M21.6 30.8 h4.8" {...stroke} />
    default:
      return <path d="M21 30.4 q3 2.3 6 0" {...stroke} />
  }
}

export function Otto({ className }: { className?: string }) {
  const state = useOttoState()
  return (
    <svg
      viewBox="0 0 48 48"
      data-otto={state}
      aria-hidden="true"
      className={cn('otto h-7 w-7 shrink-0 pointer-events-none select-none', className)}
    >
      <g className="otto-root">
        {/* arms — under the body, staggered wiggle via CSS delay */}
        {ARMS.map((d, i) => (
          <path
            key={i}
            d={d}
            fill="none"
            stroke="var(--brand-border)"
            strokeWidth="2.6"
            strokeLinecap="round"
            className={cn('otto-arm', i === ARMS.length - 1 && 'otto-arm-front')}
            style={{ animationDelay: `${i * 130}ms` }}
          />
        ))}

        {/* head */}
        <ellipse cx="24" cy="24" rx="15" ry="13.5" fill="var(--brand-primary)" />
        {/* flat mantle sheen — solid stroke, no gradient */}
        <path
          d="M13.5 18.5 q5.5 -6.5 12.5 -5.5"
          fill="none"
          stroke="var(--brand-cyan)"
          strokeWidth="2.4"
          strokeLinecap="round"
          opacity="0.45"
        />

        {/* eyes — blink via per-eye scaleY so the squash is local */}
        <g className="otto-eye">
          <circle cx="18.4" cy="23" r="4.5" fill="#fff" />
          <circle className="otto-pupil" cx="18.4" cy="23.2" r="2.25" fill="#131316" />
          <circle cx="19.3" cy="22.3" r="0.7" fill="#fff" />
        </g>
        <g className="otto-eye">
          <circle cx="29.6" cy="23" r="4.5" fill="#fff" />
          <circle className="otto-pupil" cx="29.6" cy="23.2" r="2.25" fill="#131316" />
          <circle cx="30.5" cy="22.3" r="0.7" fill="#fff" />
        </g>

        {/* blush */}
        <ellipse cx="14.2" cy="27.8" rx="2.1" ry="1.1" fill="var(--brand-cyan)" opacity="0.4" />
        <ellipse cx="33.8" cy="27.8" rx="2.1" ry="1.1" fill="var(--brand-cyan)" opacity="0.4" />

        <Mouth state={state} />
      </g>
    </svg>
  )
}