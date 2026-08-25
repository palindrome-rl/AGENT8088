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
            {/* Logo block */}
            <div className="relative mb-8">
              <div className="absolute inset-0 animate-pulse rounded-2xl bg-brand-primary/10 blur-2xl" />
              <div className="relative flex h-20 w-20 items-center justify-center rounded-2xl border border-brand-border/30 bg-gradient-to-br from-brand-primary/10 to-brand-cyan/5">
                <span className="bg-gradient-to-br from-brand-cyan to-brand-primary bg-clip-text text-2xl font-bold tracking-tighter text-transparent">
                  8088
                </span>
              </div>
            </div>

            {/* Title */}
            <h1 className="mb-1.5 text-2xl font-semibold tracking-tight text-zinc-100">
              Agent8088
            </h1>
            <p className="mb-8 text-sm text-zinc-500">
              Your local AI assistant
            </p>

            {/* Quick-start suggestions */}
            <div className="grid w-full max-w-2xl grid-cols-1 gap-2 sm:grid-cols-2">
              <SuggestionCard
                title="Ask anything"
                subtitle="Research, write code, or analyze files"
                icon="💬"
              />
              <SuggestionCard
                title="Run a command"
                subtitle="Type / to see all available commands"
                icon="⌨️"
              />
              <SuggestionCard
                title="Plan a task"
                subtitle="Use /plan to propose and execute a plan"
                icon="📋"
              />
              <SuggestionCard
                title="Browse tools"
                subtitle="32 tools across 14 modes at your disposal"
                icon="🔧"
              />
            </div>

            {/* Bottom hint */}
            <div className="mt-8 flex items-center gap-2 text-xs text-zinc-600">
              <kbd className="rounded border border-zinc-700 bg-zinc-900 px-1.5 py-0.5 font-mono text-zinc-400">
                ⌘K
              </kbd>
              <span>to open the command palette</span>
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

function SuggestionCard({ title, subtitle, icon }: { title: string; subtitle: string; icon: string }) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-zinc-800 bg-zinc-900/40 p-3 transition-colors hover:border-brand-border/40 hover:bg-zinc-900/70">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-zinc-800/60 text-lg">
        {icon}
      </div>
      <div className="min-w-0">
        <div className="truncate text-sm font-medium text-zinc-200">{title}</div>
        <div className="truncate text-xs text-zinc-500">{subtitle}</div>
      </div>
    </div>
  )
}