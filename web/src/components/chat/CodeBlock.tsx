import { useState, useCallback } from 'react'

/* ─────────────────────────────────────────────────────────
 * CODE BLOCK — Beautiful UI style
 * Line-numbered listing with copy button, syntax coloring,
 * and dark/light mode support.
 * ───────────────────────────────────────────────────────── */

const KEYWORDS = new Set([
  'import', 'from', 'export', 'default', 'async', 'function', 'const', 'let',
  'var', 'await', 'return', 'if', 'else', 'for', 'while', 'new', 'throw',
  'try', 'catch', 'null', 'true', 'false', 'undefined', 'class', 'extends',
  'super', 'this', 'typeof', 'instanceof', 'in', 'of', 'delete', 'void',
  'def', 'self', 'None', 'True', 'False', 'and', 'or', 'not', 'with', 'as',
  'lambda', 'yield', 'raise', 'except', 'finally', 'elif', 'print',
])

const TOKEN = /("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`[^`]*`|\b\d+(?:\.\d+)?\b|\b(?:import|from|export|default|async|function|const|let|var|await|return|if|else|for|while|new|throw|try|catch|null|true|false|undefined|class|extends|super|this|typeof|instanceof|in|of|delete|void|def|self|None|True|False|and|or|not|with|as|lambda|yield|raise|except|finally|elif|print)\b|[A-Za-z_$][\w$]*(?=\s*\())/g

function highlight(text: string) {
  const nodes: React.ReactNode[] = []
  let last = 0
  let k = 0
  for (const m of text.matchAll(TOKEN)) {
    const idx = m.index ?? 0
    const t = m[0]
    if (idx > last) nodes.push(<span key={k++}>{text.slice(last, idx)}</span>)
    let color: string
    let weight: number | undefined
    if (/^["'`]/.test(t) || /^\d/.test(t)) {
      color = '#e67e22' // orange — string/number
    } else if (KEYWORDS.has(t)) {
      color = '#237dd7' // brand primary — keyword
    } else {
      color = '#71717a' // zinc-500 — function call
      weight = 500
    }
    nodes.push(<span key={k++} style={{ color, fontWeight: weight }}>{t}</span>)
    last = idx + t.length
  }
  if (last < text.length) nodes.push(<span key={k++}>{text.slice(last)}</span>)
  return nodes
}

interface CodeBlockProps {
  code: string
  language?: string
}

export function CodeBlock({ code, language = 'text' }: CodeBlockProps) {
  const [copied, setCopied] = useState(false)
  const lines = code.split('\n')

  const copy = useCallback(() => {
    navigator.clipboard.writeText(code).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }, [code])

  return (
    <div className="my-2 overflow-hidden rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 shadow-sm">
      {/* Header — language label + copy button */}
      <div className="flex h-9 items-center gap-2 border-b border-zinc-200 dark:border-zinc-800 px-3 text-[12px]">
        <span className="font-mono text-zinc-500 dark:text-zinc-400">{language}</span>
        <button
          type="button"
          aria-label="Copy code"
          onClick={copy}
          className={`ml-auto flex h-6 items-center gap-1 rounded-md px-1.5 text-[12px] font-medium transition-colors duration-100 hover:bg-zinc-100 dark:hover:bg-zinc-800 ${
            copied ? 'text-green-500' : 'text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200'
          }`}
        >
          {copied ? (
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
              <path d="M20 6L9 17l-5-5" />
            </svg>
          ) : (
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="9" y="9" width="12" height="12" rx="2.5" />
              <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
            </svg>
          )}
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>

      {/* Body — line numbers + code */}
      <div className="relative py-2.5 font-mono text-[12.5px] leading-[1.65] text-zinc-700 dark:text-zinc-300">
        <span className="pointer-events-none absolute inset-y-0 left-8 w-px bg-zinc-200 dark:bg-zinc-800" />
        {lines.map((line, i) => (
          <div key={i} className="grid grid-cols-[28px_minmax(0,1fr)] items-start">
            <span className="select-none text-center text-[11px] text-zinc-400 dark:text-zinc-600">{i + 1}</span>
            <code className="pr-3 pl-1 break-words whitespace-pre-wrap text-zinc-700 dark:text-zinc-300">
              {highlight(line)}
            </code>
          </div>
        ))}
      </div>
    </div>
  )
}