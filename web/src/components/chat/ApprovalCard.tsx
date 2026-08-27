import {
  useEffect, useLayoutEffect, useRef, useState, type CSSProperties,
} from 'react'
import { ShieldCheck, ShieldX, Clock, X, ChevronUp, ChevronDown, Check } from 'lucide-react'
import { useUIStore } from '@/stores/ui'
import { useWebSocket } from '@/hooks/useWebSocket'
import { Button } from '@/components/atoms/Button'
import GlideMenu from '@/components/primitives/GlideMenu'
import { cn } from '@/lib/utils'

/* ─────────────────────────────────────────────────────────
 * APPROVAL CARD (human-in-the-loop) — Beautiful UI style
 *
 * One question at a time. The stack slides vertically as you
 * move between questions (the card's height animates to fit),
 * the step counter rolls like an odometer, and the footer uses
 * pill actions — a quiet Skip and a dark Continue with a ⏎.
 * Single-choice answers auto-advance; multi-select waits.
 *
 * Adapted for Agent8088: questions are the approval decision
 * (Approve once / Approve for session / Deny), preceded by a
 * description step showing what the tool wants to do.
 * ───────────────────────────────────────────────────────── */

const ROLL_MS = 400
const SLIDE = '360ms cubic-bezier(0.22, 1, 0.36, 1)'

/* odometer digits — each character that changes rolls up (or down) */
function RollingDigits({ value }: { value: string }) {
  const prevRef = useRef(value)
  const [oldVal, setOldVal] = useState(value)
  const [newVal, setNewVal] = useState(value)
  const [rolling, setRolling] = useState(false)
  const [shifted, setShifted] = useState(false)
  const [dir, setDir] = useState<'up' | 'down'>('up')

  useEffect(() => {
    if (prevRef.current === value) return
    const from = prevRef.current
    prevRef.current = value
    const fromN = parseInt(from, 10)
    const toN = parseInt(value, 10)
    setDir(Number.isFinite(fromN) && Number.isFinite(toN) && toN < fromN ? 'down' : 'up')
    setOldVal(from)
    setNewVal(value)
    setRolling(true)
    setShifted(false)

    let raf2 = 0
    const raf1 = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(() => setShifted(true))
    })
    const done = setTimeout(() => {
      setRolling(false)
      setOldVal(value)
      setShifted(false)
    }, ROLL_MS)

    return () => {
      cancelAnimationFrame(raf1)
      cancelAnimationFrame(raf2)
      clearTimeout(done)
    }
  }, [value])

  const chars = rolling ? newVal : oldVal

  return (
    <>
      {Array.from({ length: chars.length }, (_, i) => {
        const o = oldVal[i] ?? ''
        const n = chars[i] ?? ''
        if (!rolling || o === n) {
          return <span key={`${i}-${n}`}>{n}</span>
        }
        const top = dir === 'down' ? n : o
        const bottom = dir === 'down' ? o : n
        const restY = dir === 'down' ? '0' : '-1em'
        const startY = dir === 'down' ? '-1em' : '0'
        return (
          <span
            key={`${i}-${o}-${n}-${dir}`}
            style={{ display: 'inline-block', position: 'relative', overflow: 'hidden', height: '1em', lineHeight: '1em', verticalAlign: '-0.05em' }}
          >
            <span
              style={{
                display: 'flex',
                flexDirection: 'column',
                transition: 'transform 350ms cubic-bezier(0.4, 0, 0.2, 1)',
                transform: `translateY(${shifted ? restY : startY})`,
              }}
            >
              <span style={{ height: '1em', lineHeight: '1em' }}>{top}</span>
              <span style={{ height: '1em', lineHeight: '1em' }}>{bottom}</span>
            </span>
          </span>
        )
      })}
    </>
  )
}

export function ApprovalCard() {
  const { approvalPending, setApprovalPending } = useUIStore()
  const { send } = useWebSocket()

  const [qi, setQi] = useState(0)
  const [answers, setAnswers] = useState<Record<number, number[]>>({})
  const [sent, setSent] = useState(false)
  const [sentLabel, setSentLabel] = useState('')

  const advanceTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const stepRefs = useRef<(HTMLDivElement | null)[]>([])
  const measured = useRef(false)
  const [viewportH, setViewportH] = useState<number | undefined>(undefined)
  const [trackY, setTrackY] = useState(0)
  const [animate, setAnimate] = useState(false)
  const [ready, setReady] = useState(false)

  // Build the "questions" from the actual escalation data
  const steps = approvalPending
    ? [
        // Step 1: Description — what the tool wants to do
        {
          kind: 'info' as const,
          q: `Tool: ${approvalPending.toolName}`,
          detail: approvalPending.description,
        },
        // Step 2: Decision — approve once / approve for session / deny
        {
          kind: 'choice' as const,
          q: 'Allow this action?',
          type: 'radio' as const,
          options: [
            { label: 'Approve once', icon: 'check-once' as const },
            { label: 'Approve for session', icon: 'check-session' as const },
            { label: 'Deny', icon: 'deny' as const },
          ],
        },
      ]
    : []

  const last = qi === steps.length - 1
  const selected = answers[qi] ?? []
  const hasAnswer = selected.length > 0

  const sync = (withAnim: boolean) => {
    const item = stepRefs.current[qi]
    if (!item) return
    const reduce = typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
    setViewportH(item.offsetHeight)
    setTrackY(item.offsetTop)
    setAnimate(withAnim && !reduce)
  }

  useLayoutEffect(() => {
    if (!approvalPending) return
    const withAnim = measured.current
    measured.current = true
    sync(withAnim)
    setReady(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qi, approvalPending, sent])

  useEffect(() => {
    if (!approvalPending) return
    const id = requestAnimationFrame(() => sync(measured.current))
    return () => cancelAnimationFrame(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qi, approvalPending])

  useEffect(() => () => { if (advanceTimer.current) clearTimeout(advanceTimer.current); }, [])

  // Reset when a new escalation arrives
  useEffect(() => {
    if (approvalPending) {
      setQi(0)
      setAnswers({})
      setSent(false)
      setSentLabel('')
      measured.current = false
      setReady(false)
    }
  }, [approvalPending?.id])

  if (!approvalPending) return null

  const goTo = (next: number) => {
    if (advanceTimer.current) clearTimeout(advanceTimer.current)
    setQi(Math.min(Math.max(next, 0), steps.length - 1))
  }

  const respond = (approved: boolean, sessionScope: boolean) => {
    if (advanceTimer.current) clearTimeout(advanceTimer.current)
    send({ type: 'approval', approved, session_scope: sessionScope, id: approvalPending.id })
    setSentLabel(approved ? (sessionScope ? 'Approved for session' : 'Approved') : 'Denied')
    setSent(true)
    setApprovalPending(null)
  }

  const advance = () => {
    if (last) {
      // Execute the selected decision
      const choice = answers[1]?.[0] ?? -1
      if (choice === 0) respond(true, false)
      else if (choice === 1) respond(true, true)
      else if (choice === 2) respond(false, false)
    } else {
      goTo(qi + 1)
    }
  }

  const toggle = (index: number) => {
    setAnswers((current) => ({ ...current, [qi]: [index] }))
    // Auto-advance after a brief delay for radio
    if (advanceTimer.current) clearTimeout(advanceTimer.current)
    advanceTimer.current = setTimeout(() => {
      if (last) {
        const choice = index
        if (choice === 0) respond(true, false)
        else if (choice === 1) respond(true, true)
        else if (choice === 2) respond(false, false)
      } else {
        setQi((current) => Math.min(steps.length - 1, current + 1))
      }
    }, 480)
  }

  if (sent) {
    return (
      <div className="mx-auto max-w-3xl px-6 py-3">
        <div
          className="flex items-center gap-3"
          style={{ animation: 'pop-in 260ms cubic-bezier(0.23,1,0.32,1) both' }}
        >
          <span
            className={cn(
              'inline-flex items-center gap-1.5 rounded-full py-1 pr-2.5 pl-1 text-[12.5px] font-medium',
              sentLabel === 'Denied'
                ? 'bg-red-500/15 text-red-400'
                : 'bg-green-500/15 text-green-400',
            )}
          >
            <span
              className={cn(
                'flex size-4.5 items-center justify-center rounded-full text-white',
                sentLabel === 'Denied' ? 'bg-red-500' : 'bg-green-500',
              )}
              style={{ width: 18, height: 18 }}
            >
              {sentLabel === 'Denied' ? (
                <X className="h-2.5 w-2.5" strokeWidth={3} />
              ) : (
                <Check className="h-2.5 w-2.5" strokeWidth={3} />
              )}
            </span>
            {sentLabel}
          </span>
        </div>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-3xl px-6 py-3">
      <div className="w-full max-w-80">
        <div
          className="relative overflow-hidden rounded-xl border border-brand-border/40 bg-white dark:bg-zinc-900/50 shadow-card"
          style={{ animation: 'fade-up 380ms cubic-bezier(0.23,1,0.32,1) both' }}
        >
          {/* header */}
          <div className="primitive-card-pad">
            {/* Tool name + change type */}
            <div className="mb-2 flex items-center gap-2">
              <Clock className="h-4 w-4 text-brand-cyan" />
              <span className="text-[13px] font-semibold text-zinc-900 dark:text-zinc-100">
                Approval Required
              </span>
            </div>

            {/* Sliding question viewport */}
            <div
              className="overflow-hidden"
              style={{ height: viewportH, transition: animate ? `height ${SLIDE}` : undefined }}
              aria-live="polite"
            >
              <div
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 26,
                  transform: `translate3d(0, ${-trackY}px, 0)`,
                  transition: animate ? `transform ${SLIDE}` : undefined,
                  willChange: 'transform',
                }}
              >
                {steps.map((step, sIdx) => {
                  const active = sIdx === qi
                  if (!ready && !active) return null
                  const picked = answers[sIdx] ?? []
                  const stepStyle: CSSProperties = {
                    opacity: active ? 1 : 0,
                    transition: animate ? `opacity ${SLIDE}` : undefined,
                    pointerEvents: active ? undefined : 'none',
                  }
                  return (
                    <div
                      key={sIdx}
                      ref={(el) => { stepRefs.current[sIdx] = el }}
                      aria-hidden={active ? undefined : true}
                      style={stepStyle}
                    >
                      {step.kind === 'info' ? (
                        <div>
                          <div className="pr-7 text-[14px] font-medium text-zinc-900 dark:text-zinc-100">
                            {step.q}
                          </div>
                          <div className="mt-1 text-[12px] font-mono text-brand-primary">
                            {approvalPending.changeType}
                          </div>
                          <pre className="mt-2 max-h-28 overflow-auto rounded-lg bg-zinc-100 dark:bg-zinc-950 p-2 font-mono text-[11px] text-zinc-600 dark:text-zinc-400 whitespace-pre-wrap break-all">
                            {step.detail}
                          </pre>
                        </div>
                      ) : (
                        <div>
                          <div className="pr-7 text-[14px] font-medium text-zinc-900 dark:text-zinc-100">
                            {step.q}
                          </div>
                          <GlideMenu
                            className="mt-2.5 flex flex-col gap-1"
                            highlightClassName="bg-zinc-100 dark:bg-zinc-800/50"
                          >
                            {step.options.map((option, i) => {
                              const on = picked.includes(i)
                              const isDeny = i === 2
                              return (
                                <button
                                  key={option.label}
                                  type="button"
                                  data-menu-row
                                  aria-pressed={on}
                                  tabIndex={active ? 0 : -1}
                                  onClick={() => { if (active) toggle(i) }}
                                  className="relative z-10 flex items-center gap-1.5 rounded-lg pl-1 pr-2 py-1.5 text-left transition-colors duration-100"
                                >
                                  <span
                                    className={cn(
                                      'flex size-4 shrink-0 items-center justify-center rounded-full transition-colors duration-200',
                                      on
                                        ? isDeny
                                          ? 'bg-red-500 text-white'
                                          : 'bg-green-500 text-white'
                                        : 'shadow-[inset_0_0_0_1.5px_var(--brand-border,#0077B6)] text-transparent',
                                    )}
                                  >
                                    {isDeny ? (
                                      <ShieldX className="h-2.5 w-2.5" strokeWidth={2.5} />
                                    ) : (
                                      <ShieldCheck className="h-2.5 w-2.5" strokeWidth={2.5} />
                                    )}
                                  </span>
                                  <span
                                    className={cn(
                                      'text-[13px] leading-none transition-colors duration-200',
                                      on
                                        ? isDeny
                                          ? 'text-red-500 dark:text-red-400'
                                          : 'text-green-600 dark:text-green-400'
                                        : 'text-zinc-600 dark:text-zinc-400',
                                    )}
                                  >
                                    {option.label}
                                  </span>
                                </button>
                              )
                            })}
                          </GlideMenu>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          </div>

          {/* footer — step nav (rolling counter) + pill actions */}
          <div className="primitive-card-footer flex items-center justify-between gap-3 border-t border-zinc-200 dark:border-zinc-800/60">
            {/* Step counter with rolling digits */}
            <div className="flex items-center gap-1 text-zinc-400 dark:text-zinc-500">
              <button
                type="button"
                aria-label="Previous step"
                disabled={qi <= 0}
                onClick={() => goTo(qi - 1)}
                className="flex size-[18px] items-center justify-center rounded-[5px] transition-colors duration-100 enabled:hover:text-zinc-200 disabled:opacity-30"
              >
                <ChevronUp className="h-3.5 w-3.5" />
              </button>
              <span
                className="inline-flex items-center text-[12px] font-medium tabular-nums"
                style={{ letterSpacing: '-0.1px', lineHeight: 1 }}
              >
                <RollingDigits value={`${qi + 1} / ${steps.length}`} />
              </span>
              <button
                type="button"
                aria-label="Next step"
                disabled={last}
                onClick={() => goTo(qi + 1)}
                className="flex size-[18px] items-center justify-center rounded-[5px] transition-colors duration-100 enabled:hover:text-zinc-200 disabled:opacity-30"
              >
                <ChevronDown className="h-3.5 w-3.5" />
              </button>
            </div>

            {/* Pill actions */}
            <div className="-mr-0.5 flex items-center gap-1.5">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => respond(false, false)}
              >
                Skip
              </Button>
              <Button
                variant="accent"
                size="sm"
                disabled={!hasAnswer && qi === steps.length - 1}
                onClick={advance}
              >
                {last ? 'Send' : 'Continue'}
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}