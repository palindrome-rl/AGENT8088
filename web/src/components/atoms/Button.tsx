import { type ButtonHTMLAttributes, forwardRef } from 'react'
import { cn } from '@/lib/utils'

/* ─────────────────────────────────────────────────────────
 * BUTTON — Beautiful UI atom
 * Two variants: ghost (subtle, quiet) and accent (dark, primary).
 * Three sizes: sm, md, lg.
 * ───────────────────────────────────────────────────────── */

type Variant = 'ghost' | 'accent'
type Size = 'sm' | 'md' | 'lg'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
}

const sizeClasses: Record<Size, string> = {
  sm: 'h-7 px-2.5 text-[12.5px] gap-1',
  md: 'h-9 px-3.5 text-[13px] gap-1.5',
  lg: 'h-11 px-5 text-[14px] gap-2',
}

const variantClasses: Record<Variant, string> = {
  ghost: cn(
    'text-zinc-500 dark:text-zinc-400',
    'hover:bg-zinc-100 dark:hover:bg-zinc-800/50',
    'hover:text-zinc-800 dark:hover:text-zinc-200',
    'active:scale-[0.97]',
  ),
  accent: cn(
    'bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900',
    'hover:bg-zinc-800 dark:hover:bg-zinc-200',
    'active:scale-[0.97]',
    'shadow-sm',
  ),
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = 'ghost', size = 'md', className, ...props }, ref) => (
    <button
      ref={ref}
      className={cn(
        'inline-flex items-center justify-center rounded-lg font-medium transition-all duration-150',
        'focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary/40',
        'disabled:pointer-events-none disabled:opacity-40',
        sizeClasses[size],
        variantClasses[variant],
        className,
      )}
      {...props}
    />
  ),
)

Button.displayName = 'Button'