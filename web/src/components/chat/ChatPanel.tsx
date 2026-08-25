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

  return (
    <div className="flex h-full flex-col">
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        {messages.length === 0 && !isStreaming && (
          <div className="flex h-full items-center justify-center text-zinc-600">
            <div className="text-center">
              <div className="mb-2 text-4xl">8088</div>
              <div className="text-sm">Send a message to start chatting with Agent8088</div>
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