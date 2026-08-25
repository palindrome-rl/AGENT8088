import { useRef, useEffect, useState } from 'react'
import { useSessionStore } from '@/stores/session'
import { MessageBubble } from './MessageBubble'
import { ToolChip } from './ToolChip'
import { ThinkingTrace } from './ThinkingTrace'
import { ApprovalCard } from './ApprovalCard'
import { PromptBar } from './PromptBar'
import { useUIStore } from '@/stores/ui'

export function ChatPanel() {
  const { messages, toolEvents, isStreaming, streamingText } = useSessionStore()
  const { theme } = useUIStore()
  const scrollRef = useRef<HTMLDivElement>(null)
  const [showLoader, setShowLoader] = useState(false)

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, isStreaming, toolEvents, streamingText])

  // Show loader after 300ms of streaming with no text yet (agent is "thinking")
  useEffect(() => {
    if (isStreaming && !streamingText) {
      const t = setTimeout(() => setShowLoader(true), 300)
      return () => clearTimeout(t)
    }
    setShowLoader(false)
  }, [isStreaming, streamingText])

  const isEmpty = messages.length === 0 && !isStreaming

  return (
    <div className="flex h-full flex-col">
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        {isEmpty && (
          <div className="flex h-full flex-col items-center justify-center px-6">
            {/* Logo image */}
            <div className="mb-4">
              <img src="/logo.png" alt="Agent8088" className="h-10 w-auto" style={{ mixBlendMode: theme === 'dark' ? 'screen' : 'normal' }} />
            </div>

            <p className="mb-5 text-[13px] text-zinc-500 dark:text-zinc-500">
              Your local AI assistant
            </p>

            {/* Suggestions */}
            <div className="grid w-full max-w-md grid-cols-2 gap-2">
              <SuggestionCard title="Ask anything" subtitle="Research, write code, analyze" />
              <SuggestionCard title="Run a command" subtitle="Type / for all commands" />
              <SuggestionCard title="Plan a task" subtitle="Use /plan to propose & execute" />
              <SuggestionCard title="Browse tools" subtitle="32 tools across 14 modes" />
            </div>

            <div className="mt-5 flex items-center gap-1.5 text-[11px] text-zinc-400 dark:text-zinc-600">
              <kbd className="rounded border border-zinc-300 dark:border-zinc-700 bg-zinc-100 dark:bg-zinc-900 px-1.5 py-0.5 font-mono text-zinc-500 dark:text-zinc-400">
                ⌘K
              </kbd>
              <span>command palette</span>
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <MessageBubble key={i} message={msg} />
        ))}

        {/* Loading state — Beautiful UI pixel-grid loader while agent thinks */}
        {isStreaming && showLoader && !streamingText && (
          <div className="msg-enter mx-auto max-w-3xl px-6 py-4">
            <PixelLoader theme={theme} />
          </div>
        )}

        {/* Streaming response with fade-in */}
        {isStreaming && streamingText && (
          <div className="msg-enter mx-auto max-w-3xl px-6 py-4">
            <ThinkingTrace />
            {toolEvents.map((tool, i) => (
              <ToolChip key={i} name={tool.name} status={tool.status} result={tool.result} />
            ))}
            <div className="stream-cursor text-[14px] leading-relaxed text-zinc-800 dark:text-zinc-200">
              {streamingText}
            </div>
          </div>
        )}
        <ApprovalCard />
      </div>
      <PromptBar />
    </div>
  )
}

/** Beautiful UI pixel-grid loader — 3x3 grid with chevron wavefront */
function PixelLoader({ theme }: { theme: string }) {
  const [ds, setDs] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setDs(d => d + 1), 100)
    return () => clearInterval(t)
  }, [])
  const elapsed = (ds / 10).toFixed(1) + 's'

  const chevron = [0, 90, 180, 90, 180, 270, 180, 270, 360]
  const cellColor = theme === 'dark' ? '#e4e4e7' : '#18181b'

  return (
    <div className="flex items-center gap-2.5" role="status">
      <span className="grid shrink-0 grid-cols-3 gap-[1.5px]">
        {chevron.map((delay, i) => (
          <span
            key={i}
            className="h-1 w-1 rounded-[1px]"
            style={{
              backgroundColor: cellColor,
              opacity: 0.15,
              animation: `pixel-on 650ms ease-in-out ${delay}ms infinite`,
            }}
          />
        ))}
      </span>
      <span
        className="text-[13px] font-medium"
        style={{
          color: theme === 'dark' ? '#71717a' : '#a1a1aa',
          backgroundImage: `linear-gradient(90deg, ${theme === 'dark' ? '#71717a' : '#a1a1aa'} 35%, ${theme === 'dark' ? '#e4e4e7' : '#18181b'} 50%, ${theme === 'dark' ? '#71717a' : '#a1a1aa'} 65%)`,
          backgroundSize: '200% 100%',
          WebkitBackgroundClip: 'text',
          backgroundClip: 'text',
          WebkitTextFillColor: 'transparent',
          animation: 'shimmer-text 1.4s linear infinite',
        }}
      >
        Churning
      </span>
      <span className="font-mono text-[12px] text-zinc-400 dark:text-zinc-500 tabular-nums">
        {elapsed}
      </span>
    </div>
  )
}

function SuggestionCard({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-800/60 bg-white dark:bg-zinc-900/30 px-3 py-2 transition-colors hover:border-zinc-300 dark:hover:border-zinc-700 hover:bg-zinc-50 dark:hover:bg-zinc-900/50">
      <div className="truncate text-[13px] font-medium text-zinc-800 dark:text-zinc-200">{title}</div>
      <div className="truncate text-[11px] text-zinc-400 dark:text-zinc-500">{subtitle}</div>
    </div>
  )
}