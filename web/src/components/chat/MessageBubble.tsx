import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { cn } from '@/lib/utils'
import type { ChatMessage } from '@/types/api'

export function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user'

  return (
    <div className={cn('w-full border-b border-zinc-800/40', isUser ? 'bg-zinc-900/20' : 'bg-transparent')}>
      <div className="mx-auto max-w-3xl px-6 py-4">
        <div className="mb-1 text-[11px] font-medium text-zinc-500">
          {isUser ? 'You' : 'Agent8088'}
        </div>
        <div className="prose prose-invert prose-sm max-w-none text-[14px] leading-relaxed text-zinc-200">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              code({ className, children, ...props }) {
                const match = /language-(\w+)/.exec(className || '')
                const isInline = !className
                return isInline ? (
                  <code className="rounded bg-zinc-800 px-1 py-0.5 font-mono text-[13px] text-zinc-300" {...props}>
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