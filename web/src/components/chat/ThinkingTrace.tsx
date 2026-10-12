import { memo, useState, useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { scrubMarkup } from '@/lib/scrub'

/* ─────────────────────────────────────────────────────────
 * THINKING — Beautiful UI-style expandable trace
 *
 * Shows shimmering "Thinking..." while streaming, then settles
 * to "Thought for N seconds" with expandable reasoning.
 * One section per model step: finished steps render as Markdown,
 * the step still streaming as plain text (no half-open fences).
 * Live in ChatPanel; collapsed and static on a finished message.
 * ───────────────────────────────────────────────────────── */

export function ThinkingTrace({ reasoning, live = false }: { reasoning: string[]; live?: boolean }) {
  const [expanded, setExpanded] = useState(live)
  const [settled, setSettled] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const startTime = useRef<number>(0)

  // Track elapsed time while streaming
  useEffect(() => {
    if (live && reasoning.length > 0) {
      if (startTime.current === 0) startTime.current = Date.now()
      const t = setInterval(() => {
        setElapsed((Date.now() - startTime.current) / 1000)
      }, 100)
      return () => clearInterval(t)
    }
    if (!live && startTime.current > 0) {
      setSettled(true)
    }
  }, [live, reasoning.length])

  // Reset when new turn starts
  useEffect(() => {
    if (live && reasoning.length === 0) {
      startTime.current = 0
      setElapsed(0)
      setSettled(false)
      setExpanded(true)
    }
  }, [live, reasoning.length])

  if (!reasoning.length) return null

  const timeStr = elapsed < 60 ? `${elapsed.toFixed(1)}s` : `${Math.floor(elapsed / 60)}m ${(elapsed % 60).toFixed(1)}s`
  const working = live && !settled
  // A finished message has no timer, so it reports steps instead.
  const labelText = working ? 'Thinking'
    : startTime.current > 0 ? `Thought for ${timeStr}`
    : reasoning.length > 1 ? `Thinking · ${reasoning.length} steps` : 'Thinking'

  return (
    <div
      className="my-1 flex w-full flex-col"
      style={{ minHeight: working || expanded ? 60 : undefined, transition: 'min-height 400ms cubic-bezier(0.23,1,0.32,1)' }}
    >
      {/* Header button */}
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
        className="-mx-1.5 flex w-fit items-center gap-2 rounded-lg px-1.5 py-1 transition-colors duration-100 hover:bg-zinc-100 dark:hover:bg-zinc-800/50"
      >
        {/* Sparkle icon */}
        <svg width="16" height="16" viewBox="0 0 24 24" fill={working ? '#a1a1aa' : '#71717a'} className="dark:fill-zinc-400">
          <path d="M12 2l2.4 7.2L22 12l-7.6 2.8L12 22l-2.4-7.2L2 12l7.6-2.8z" />
        </svg>

        {/* Label — shimmer while working, fade-in when settled */}
        {working ? (
          <span
            className="bg-clip-text text-[13px] font-medium whitespace-nowrap text-transparent"
            style={{
              backgroundImage: 'linear-gradient(90deg, #a1a1aa 35%, #18181b 50%, #a1a1aa 65%)',
              backgroundSize: '200% 100%',
              animation: 'shimmer-text 1.4s linear infinite',
            }}
          >
            {labelText}
          </span>
        ) : (
          <span
            className="text-[13px] font-medium whitespace-nowrap text-zinc-500 dark:text-zinc-400"
            style={{ animation: 'fade-in 350ms ease-out both' }}
          >
            {labelText}
          </span>
        )}

        {/* Chevron */}
        <svg
          width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#a1a1aa" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"
          className="transition-transform duration-300"
          style={{ transform: expanded ? 'rotate(180deg)' : 'rotate(0)' }}
        >
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>

      {/* Expandable trace */}
      <div
        className="grid transition-[grid-template-rows,opacity] duration-400"
        style={{
          gridTemplateRows: expanded ? '1fr' : '0fr',
          opacity: expanded ? 1 : 0,
          transitionTimingFunction: 'cubic-bezier(0.23, 1, 0.32, 1)',
        }}
      >
        <div className="overflow-hidden">
          <div className="relative mt-1 ml-[5px] pl-4">
            {/* Vertical line */}
            <span
              aria-hidden
              className="absolute left-[3px] w-px bg-zinc-200 dark:bg-zinc-800"
              style={{ top: -8, height: '100%', transition: 'height 500ms cubic-bezier(0.23,1,0.32,1)' }}
            />
            <div className="flex flex-col gap-2 py-1">
              {reasoning.map((text, i) => {
                const current = working && i === reasoning.length - 1
                return (
                  <div
                    key={i}
                    className="rounded-md px-1.5 py-0.5"
                    style={{ animation: 'fade-up 320ms cubic-bezier(0.23,1,0.32,1) both' }}
                  >
                    {/* Step label */}
                    <div className="flex min-h-6 items-center gap-2 text-[11px] font-medium text-zinc-400 dark:text-zinc-500">
                      {current ? (
                        <span
                          className="size-3 shrink-0 rounded-full border-[1.5px] border-zinc-300 dark:border-zinc-700 border-t-brand-cyan"
                          style={{ animation: 'spin 700ms linear infinite' }}
                        />
                      ) : (
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#a1a1aa" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
                          <path d="M20 6L9 17l-5-5" />
                        </svg>
                      )}
                      Step {i + 1}
                    </div>
                    {current ? (
                      <div className="min-w-0 whitespace-pre-wrap break-words text-[12.5px] leading-relaxed text-zinc-600 dark:text-zinc-400">
                        {scrubMarkup(text)}
                      </div>
                    ) : (
                      <ReasoningMarkdown text={text} />
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

/** A finished step never changes, so memo keeps the live stream from
 *  re-parsing every earlier step on each delta. Raw HTML stays escaped. */
const ReasoningMarkdown = memo(function ReasoningMarkdown({ text }: { text: string }) {
  return (
    <div className="chat-markdown min-w-0 max-w-full overflow-hidden text-[12.5px] leading-relaxed text-zinc-600 dark:text-zinc-400 [&>*:first-child]:mt-0 [&>*:last-child]:mb-0 [&_pre]:max-w-full [&_pre]:overflow-x-auto [&_pre]:rounded [&_pre]:bg-zinc-100 [&_pre]:p-2 dark:[&_pre]:bg-zinc-800/60 [&_code]:font-mono">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{scrubMarkup(text)}</ReactMarkdown>
    </div>
  )
})
