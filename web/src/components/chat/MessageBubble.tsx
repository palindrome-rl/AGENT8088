import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CodeBlock } from './CodeBlock'
import { scrubMarkup } from '@/lib/scrub'
import { FileText } from 'lucide-react'

import type { ChatMessage } from '@/types/api'

export function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user'

  if (isUser) {
    // User messages — right-aligned bubble with slide-in animation
    return (
      <div className="user-msg-enter mx-auto flex max-w-3xl justify-end px-6 py-2">
        <div className="w-fit max-w-[85%] rounded-2xl rounded-tr-md border border-brand-primary/25 bg-brand-primary/15 px-4 py-3 text-[14px] leading-relaxed text-zinc-900 shadow-sm dark:border-brand-primary/30 dark:bg-brand-primary/20 dark:text-zinc-100">
          {message.content}
          {message.attachments?.length ? (
            <div className="mt-2 flex flex-wrap gap-1.5 border-t border-brand-primary/20 pt-2">
              {message.attachments.map((attachment) => (
                <span key={attachment.id || attachment.name} className="inline-flex max-w-full items-center gap-1 rounded-md bg-white/45 px-2 py-1 text-[11px] text-zinc-700 dark:bg-black/20 dark:text-zinc-200">
                  <FileText className="h-3 w-3 shrink-0" />
                  <span className="max-w-44 truncate">{attachment.name}</span>
                  {attachment.size != null && <span className="shrink-0 opacity-65">{formatBytes(attachment.size)}</span>}
                </span>
              ))}
            </div>
          ) : null}
        </div>
      </div>
    )
  }

  // Assistant messages — left-aligned bubble to match user messages
  return (
    <div className="msg-enter mx-auto flex max-w-3xl justify-start px-6 py-2">
      <div className="min-w-0 max-w-[85%] rounded-2xl rounded-tl-md border border-zinc-200 bg-zinc-100/90 px-4 py-3 shadow-sm dark:border-zinc-800 dark:bg-zinc-900/90">
        <div className="mb-1.5 text-[11px] font-medium text-zinc-500 dark:text-zinc-400">Agent8088</div>
        {message.format === 'terminal' ? (
          <pre className="max-h-[60vh] max-w-full overflow-auto whitespace-pre rounded-lg border border-zinc-200 bg-zinc-50 p-3 font-mono text-[12px] leading-relaxed text-zinc-800 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-200">
            {scrubMarkup(message.content)}
          </pre>
        ) : <div className="chat-markdown min-w-0 max-w-full overflow-hidden text-[14px] leading-relaxed text-zinc-800 dark:text-zinc-200 [&>p:first-child]:mt-0 [&>p:last-child]:mb-0 [&_pre]:max-w-full [&_table]:block [&_table]:max-w-full [&_table]:overflow-x-auto">
          {/* Scrub tool-call protocol: history messages are stored raw */}
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
            {scrubMarkup(message.content)}
          </ReactMarkdown>
        </div>}
      </div>
    </div>
  )
}

function formatBytes(size: number) {
  return size < 1024 * 1024 ? `${Math.max(1, Math.round(size / 1024))} KB` : `${(size / 1024 / 1024).toFixed(1)} MB`
}
