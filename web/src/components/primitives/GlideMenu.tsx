import { useRef, useState, useEffect, type ReactNode } from 'react'
import { cn } from '@/lib/utils'

/* ─────────────────────────────────────────────────────────
 * GLIDE MENU — Beautiful UI primitive
 * A vertical menu where a highlight pill glides between
 * hovered/focused rows using FLIP-style transforms.
 * ───────────────────────────────────────────────────────── */

interface GlideMenuProps {
  children: ReactNode
  className?: string
  highlightClassName?: string
}

export function GlideMenu({ children, className, highlightClassName }: GlideMenuProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [highlight, setHighlight] = useState<{ top: number; height: number } | null>(null)
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const updateHighlight = (target: HTMLElement | null) => {
      if (!target) {
        setVisible(false)
        return
      }
      const containerRect = container.getBoundingClientRect()
      const targetRect = target.getBoundingClientRect()
      setHighlight({
        top: targetRect.top - containerRect.top,
        height: targetRect.height,
      })
      setVisible(true)
    }

    const handleOver = (e: PointerEvent) => {
      const row = (e.target as Element)?.closest('[data-menu-row]') as HTMLElement | null
      updateHighlight(row)
    }
    const handleLeave = () => setVisible(false)
    const handleFocusIn = (e: FocusEvent) => {
      const row = (e.target as Element)?.closest('[data-menu-row]') as HTMLElement | null
      updateHighlight(row)
    }

    container.addEventListener('pointerover', handleOver)
    container.addEventListener('pointerleave', handleLeave)
    container.addEventListener('focusin', handleFocusIn)

    return () => {
      container.removeEventListener('pointerover', handleOver)
      container.removeEventListener('pointerleave', handleLeave)
      container.removeEventListener('focusin', handleFocusIn)
    }
  }, [])

  return (
    <div ref={containerRef} className={cn('relative', className)}>
      {/* Gliding highlight pill */}
      {highlight && (
        <span
          aria-hidden
          className={cn(
            'pointer-events-none absolute left-0 right-0 z-0 rounded-lg bg-zinc-100 dark:bg-zinc-800/50',
            highlightClassName,
          )}
          style={{
            top: highlight.top,
            height: highlight.height,
            opacity: visible ? 1 : 0,
            transition: 'top 200ms cubic-bezier(0.22, 1, 0.36, 1), height 200ms cubic-bezier(0.22, 1, 0.36, 1), opacity 150ms ease-out',
          }}
        />
      )}
      {children}
    </div>
  )
}

export default GlideMenu