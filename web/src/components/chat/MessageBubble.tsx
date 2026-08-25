import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { cn } from '@/lib/utils'
import type { ChatMessage } from '@/types/api'

export function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user'
  return (
    <div className={cn('flex gap-3 px-4 py-2.5', isUser ? 'justify-end' : 'justify-start')}>
      {!isUser && (
        <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-brand-border/20 bg-gradient-to-br from-brand-primary/15 to-brand-cyan/5">
          <span className="bg-gradient-to-br from-brand-cyan to-brand-primary bg-clip-text text-[10px] font-bold tracking-tighter text-transparent">
            8088
          </span>
        </div>
      )}
      <div className={cn('max-w-3xl rounded-2xl px-4 py-2.5', isUser
        ? 'rounded-tr-sm bg-brand-primary/15 text-zinc-100'
        : 'rounded-tl-sm bg-zinc-900/60 text-zinc-200'
      )}>
        <div className="mb-1 text-xs font-medium text-zinc-500">{isUser ? 'You' : 'Agent8088'}</div>
        <div className="prose prose-invert prose-sm max-w-none">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              code({ className, children, ...props }) {
                const match = /language-(\w+)/.exec(className || '')
                const isInline = !className
                return isInline ? (
                  <code className="rounded bg-zinc-800 px-1 py-0.5 font-mono text-xs" {...props}>
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