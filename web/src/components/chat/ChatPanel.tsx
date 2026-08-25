import { useRef, useEffect } from 'react'
import { useSessionStore } from '@/stores/session'
import { MessageBubble } from './MessageBubble'
import { ToolChip } from './ToolChip'
import { ThinkingTrace } from './ThinkingTrace'
import { StreamingText } from './StreamingText'
import { ApprovalCard } from './ApprovalCard'
import { PromptBar } from './PromptBar'

export function ChatPanel() {
  const { messages, toolEvents, isStreaming } = useSessionStore()
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, isStreaming, toolEvents])

  const isEmpty = messages.length === 0 && !isStreaming

  return (
    <div className="flex h-full flex-col">
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        {isEmpty && (
          <div className="flex h-full flex-col items-center justify-center px-6">
            {/* Logo */}
            <div className="mb-6">
              <div className="pixel-grid-loader flex h-16 w-16 items-center justify-center rounded-2xl border border-brand-border/20">
                <span className="text-xl font-bold tracking-tighter text-brand-cyan">
                  8088
                </span>
              </div>
            </div>

            {/* Title */}
            <h1 className="mb-1 text-lg font-semibold tracking-tight text-zinc-100">
              Agent8088
            </h1>
            <p className="mb-6 text-[13px] text-zinc-500">
              Your local AI assistant
            </p>

            {/* Suggestions */}
            <div className="grid w-full max-w-xl grid-cols-2 gap-2">
              <SuggestionCard title="Ask anything" subtitle="Research, write code, analyze" />
              <SuggestionCard title="Run a command" subtitle="Type / for all commands" />
              <SuggestionCard title="Plan a task" subtitle="Use /plan to propose & execute" />
              <SuggestionCard title="Browse tools" subtitle="32 tools across 14 modes" />
            </div>

            {/* Hint */}
            <div className="mt-6 flex items-center gap-1.5 text-[11px] text-zinc-600">
              <kbd className="rounded border border-zinc-700 bg-zinc-900 px-1.5 py-0.5 font-mono text-zinc-400">
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
          <div className="px-4 py-2">
            <ThinkingTrace />
            {toolEvents.map((tool, i) => (
              <ToolChip key={i} name={tool.name} status={tool.status} result={tool.result} />
            ))}
            <StreamingText />
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
    <div className="flex items-center gap-2.5 rounded-xl border border-zinc-800/60 bg-zinc-900/30 px-3 py-2.5 transition-colors hover:border-zinc-700 hover:bg-zinc-900/50">
      <div className="min-w-0">
        <div className="truncate text-[13px] font-medium text-zinc-200">{title}</div>
        <div className="truncate text-[11px] text-zinc-500">{subtitle}</div>
      </div>
    </div>
  )
}