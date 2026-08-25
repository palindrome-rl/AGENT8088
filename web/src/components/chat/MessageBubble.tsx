import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { cn } from '@/lib/utils'
import type { ChatMessage } from '@/types/api'

export function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user'
  return (
    <div className={cn('flex gap-2.5 px-4 py-2', isUser ? 'justify-end' : 'justify-start')}>
      {!isUser && (
        <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-lg border border-brand-border/20 bg-brand-primary/10">
          <span className="text-[9px] font-bold tracking-tighter text-brand-cyan">
            8088
          </span>
        </div>
      )}
      <div className={cn('max-w-3xl rounded-xl px-3.5 py-2 text-[13px]', isUser
        ? 'rounded-tr-sm bg-brand-primary/10 text-zinc-100'
        : 'rounded-tl-sm bg-zinc-900/40 text-zinc-200'
      )}>
        <div className="mb-0.5 text-[11px] text-zinc-500">{isUser ? 'You' : 'Agent8088'}</div>
        <div className="prose prose-invert prose-sm max-w-none">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              code({ className, children, ...props }) {
                const match = /language-(\w+)/.exec(className || '')
                const isInline = !className
                return isInline ? (
                  <code className="rounded bg-zinc-800 px-1 py-0.5 font-mono text-xs text-zinc-300" {...props}>
                    {children}
                  </code>
                ) : (
                  <SyntaxHighlighter
                    language={match?.[1] || 'text'}
                    style={vscDarkPlus}
                    customStyle={{ margin: 0, borderRadius: '0.5rem', fontSize: '0.8rem' }}
                  >
                    {String(children).replace(/\n$/, '')}
                  </SyntaxHighlighter>
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