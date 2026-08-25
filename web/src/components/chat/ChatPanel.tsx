import { useRef, useEffect } from 'react'
import { useSessionStore } from '@/stores/session'
import { MessageBubble } from './MessageBubble'
import { ToolChip } from './ToolChip'
import { ThinkingTrace } from './ThinkingTrace'
import { StreamingText } from './StreamingText'
import { ApprovalCard } from './ApprovalCard'
import { PromptBar } from './PromptBar'

export function ChatPanel() {
  const { messages, toolEvents, isStreaming, streamingText } = useSessionStore()
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, isStreaming, toolEvents, streamingText])

  const isEmpty = messages.length === 0 && !isStreaming

  return (
    <div className="flex h-full flex-col">
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        {isEmpty && (
          <div className="flex h-full flex-col items-center justify-center px-6">
            {/* Logo */}
            <div className="mb-5">
              <div className="pixel-grid-loader flex h-14 w-14 items-center justify-center rounded-2xl border border-brand-border/20">
                <span className="text-lg font-bold tracking-tighter text-brand-cyan">
                  8088
                </span>
              </div>
            </div>

            <h1 className="mb-1 text-base font-semibold tracking-tight text-zinc-900 dark:text-zinc-100 light:text-zinc-900">
              Agent8088
            </h1>
            <p className="mb-5 text-[13px] text-zinc-500 dark:text-zinc-500 light:text-zinc-500">
              Your local AI assistant
            </p>

            {/* Suggestions */}
            <div className="grid w-full max-w-md grid-cols-2 gap-2">
              <SuggestionCard title="Ask anything" subtitle="Research, write code, analyze" />
              <SuggestionCard title="Run a command" subtitle="Type / for all commands" />
              <SuggestionCard title="Plan a task" subtitle="Use /plan to propose & execute" />
              <SuggestionCard title="Browse tools" subtitle="32 tools across 14 modes" />
            </div>

            <div className="mt-5 flex items-center gap-1.5 text-[11px] text-zinc-600 dark:text-zinc-600 light:text-zinc-400">
              <kbd className="rounded border border-zinc-700 dark:border-zinc-700 light:border-zinc-300 bg-zinc-900 dark:bg-zinc-900 light:bg-zinc-100 px-1.5 py-0.5 font-mono text-zinc-400 dark:text-zinc-400 light:text-zinc-500">
                ⌘K
              </kbd>
              <span>command palette</span>
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <MessageBubble key={i} message={msg} />
        ))}
        {isStreaming && (
          <div className="mx-auto max-w-3xl px-6 py-4">
            <ThinkingTrace />
            {toolEvents.map((tool, i) => (
              <ToolChip key={i} name={tool.name} status={tool.status} result={tool.result} />
            ))}
            {streamingText && (
              <div className="stream-cursor text-[14px] leading-relaxed text-zinc-200 dark:text-zinc-200 light:text-zinc-800">
                {streamingText}
              </div>
            )}
          </div>
        )}
        <ApprovalCard />
      </div>
      <PromptBar />
    </div>
  )
}

function SuggestionCard({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="overflow-hidden rounded-lg border border-zinc-800/60 dark:border-zinc-800/60 light:border-zinc-200 bg-zinc-900/30 dark:bg-zinc-900/30 light:bg-white px-3 py-2 transition-colors hover:border-zinc-700 dark:hover:border-zinc-700 light:hover:border-zinc-300 hover:bg-zinc-900/50 dark:hover:bg-zinc-900/50 light:hover:bg-zinc-50">
      <div className="truncate text-[13px] font-medium text-zinc-200 dark:text-zinc-200 light:text-zinc-800">{title}</div>
      <div className="truncate text-[11px] text-zinc-500 dark:text-zinc-500 light:text-zinc-400">{subtitle}</div>
    </div>
  )
}