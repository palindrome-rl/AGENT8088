import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CodeBlock } from './CodeBlock'

import type { ChatMessage } from '@/types/api'

export function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user'

  if (isUser) {
    // User messages — right-aligned bubble with slide-in animation
    return (
      <div className="user-msg-enter w-full py-3">
        <div className="mx-auto flex max-w-3xl justify-end px-6">
          <div className="max-w-[80%] rounded-xl rounded-tr-sm bg-brand-primary/10 dark:bg-brand-primary/10 px-4 py-2.5 text-[14px] leading-relaxed text-zinc-900 dark:text-zinc-100">
            {message.content}
          </div>
        </div>
      </div>
    )
  }

  // Assistant messages — flat full-width with border-b
  return (
    <div className="msg-enter w-full border-b border-zinc-200 dark:border-zinc-800/40">
      <div className="mx-auto max-w-3xl px-6 py-4">
        <div className="mb-1 text-[11px] font-medium text-zinc-400 dark:text-zinc-500">Agent8088</div>
        <div className="prose prose-sm max-w-none text-[14px] leading-relaxed text-zinc-800 dark:text-zinc-200">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              code({ className, children, ...props }) {
                const match = /language-(\w+)/.exec(className || '')
                const isInline = !className
                return isInline ? (
                  <code className="rounded bg-zinc-100 dark:bg-zinc-800 px-1 py-0.5 font-mono text-[13px] text-zinc-700 dark:text-zinc-300" {...props}>
                    {children}
                  </code>
                ) : (
                  <CodeBlock
                    code={String(children).replace(/\n$/, '')}
                    language={match?.[1] || 'text'}
                  />
                )
              },
            }}
          >
            {message.content}
          </ReactMarkdown>
        </div>
      </div>
    </div>
  )
}