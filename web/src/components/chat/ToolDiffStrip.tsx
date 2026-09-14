import { useState } from 'react'
import { createPortal } from 'react-dom'

import type { ToolDiffPayload } from '@/types/api'

/* ─────────────────────────────────────────────────────────
 * TOOL DIFF STRIP
 * Every file a run changed, as one chip each, below the
 * tool rows under a divider. Hover or focus a chip to see
 * the diff: green added, red removed, neutral context.
 * ───────────────────────────────────────────────────────── */

const PREVIEW_WIDTH = 288 // w-72, kept in sync so clamping matches the render
const ROW_HEIGHT = 19
const CHROME_HEIGHT = 38

/** One chip per file. A file written twice shows its combined counts and the
 *  most recent body, which is what "what did this run do to this file" means. */
function collapse(diffs: ToolDiffPayload[]): ToolDiffPayload[] {
  const byFile = new Map<string, ToolDiffPayload>()
  for (const diff of diffs) {
    if (!diff?.file) continue
    const seen = byFile.get(diff.file)
    byFile.set(diff.file, seen
      ? { ...diff, added: seen.added + diff.added, removed: seen.removed + diff.removed }
      : diff)
  }
  return [...byFile.values()]
}

export function ToolDiffStrip({ diffs }: { diffs: ToolDiffPayload[] }) {
  const files = collapse(diffs)
  /* Rendered in a body portal so animated or translated reply wrappers cannot
   * redefine the fixed-position coordinate system. */
  const [preview, setPreview] = useState<{
    file: string
    x: number
    top?: number
    bottom?: number
  } | null>(null)

  if (files.length === 0) return null

  const open = (diff: ToolDiffPayload) => (event: React.SyntheticEvent) => {
    const rect = (event.currentTarget as Element).getBoundingClientRect()
    const height = CHROME_HEIGHT + diff.lines.length * ROW_HEIGHT
    const fitsBelow = rect.bottom + 6 + height <= window.innerHeight - 12
    setPreview({
      file: diff.file,
      x: Math.max(12, Math.min(rect.left, window.innerWidth - PREVIEW_WIDTH - 12)),
      ...(fitsBelow ? { top: rect.bottom + 6 } : { bottom: window.innerHeight - rect.top + 6 }),
    })
  }
  const close = (file: string) => () =>
    setPreview((current) => (current?.file === file ? null : current))

  const shown = files.find((diff) => diff.file === preview?.file)

  return (
    <div
      data-testid="tool-diff-strip"
      className="mt-2.5 flex max-w-full flex-wrap gap-1.5 border-t border-line pt-2.5"
    >
      {files.map((diff, index) => (
        <button
          key={diff.file}
          type="button"
          aria-expanded={preview?.file === diff.file}
          aria-label={`Show diff for ${diff.file}`}
          onMouseEnter={open(diff)}
          onMouseLeave={close(diff.file)}
          onFocus={open(diff)}
          onBlur={close(diff.file)}
          className="inline-flex h-7 max-w-full items-center gap-2 rounded-chip bg-surface px-2
            font-mono text-[11.5px] text-ink shadow-btn transition-colors duration-100 hover:bg-hover"
          style={{ animation: `pop-in 250ms cubic-bezier(0.23,1,0.32,1) ${index * 80}ms both` }}
        >
          <span className="min-w-0 truncate">{diff.file}</span>
          <span className="shrink-0 text-green tabular-nums">+{diff.added}</span>
          {diff.removed > 0 && (
            <span className="shrink-0 text-red tabular-nums">−{diff.removed}</span>
          )}
        </button>
      ))}

      {preview && shown && typeof document !== 'undefined' && createPortal(
        <div
          data-testid="tool-diff-preview"
          className="fixed z-50 w-72 overflow-hidden rounded-[10px] bg-surface shadow-overlay"
          style={{
            left: preview.x,
            top: preview.top,
            bottom: preview.bottom,
            animation: 'pop-in 160ms cubic-bezier(0.23,1,0.32,1) both',
            transformOrigin: preview.top === undefined ? 'bottom left' : 'top left',
          }}
        >
          <div className="flex items-center justify-between border-b border-line px-2.5 py-1.5 font-mono text-[11px]">
            <span className="min-w-0 truncate text-ink-2">{shown.file}</span>
            <span className="shrink-0 tabular-nums">
              <span className="text-green">+{shown.added}</span>
              {shown.removed > 0 && <span className="text-red"> −{shown.removed}</span>}
            </span>
          </div>
          <div className="py-1 font-mono text-[11px] leading-[1.8]">
            {shown.lines.map((line, index) => (
              <div
                key={index}
                className={`flex gap-2 whitespace-pre px-2.5 ${
                  line.tone === 'add'
                    ? 'bg-green-tint text-green'
                    : line.tone === 'del'
                      ? 'bg-red-tint text-red'
                      : 'text-ink-2'
                }`}
              >
                <span className="w-3 shrink-0 select-none">
                  {line.tone === 'add' ? '+' : line.tone === 'del' ? '−' : ' '}
                </span>
                <span className="min-w-0 truncate">{line.text}</span>
              </div>
            ))}
            {shown.truncated && (
              <div className="px-2.5 pt-1 text-ink-3">… diff truncated</div>
            )}
          </div>
        </div>,
        document.body,
      )}
    </div>
  )
}
