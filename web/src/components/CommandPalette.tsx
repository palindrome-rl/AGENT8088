import { useUIStore } from '@/stores/ui'
import { Search } from 'lucide-react'

export function CommandPalette() {
  const { commandPaletteOpen, setCommandPaletteOpen } = useUIStore()
  if (!commandPaletteOpen) return null
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 pt-20"
         onClick={() => setCommandPaletteOpen(false)}>
      <div className="w-full max-w-xl rounded-xl border border-zinc-200 bg-white p-4 shadow-2xl dark:border-zinc-700 dark:bg-zinc-900"
           onClick={(e) => e.stopPropagation()}>
        <div className="mb-2 text-xs font-medium text-zinc-500 dark:text-zinc-400">Search commands</div>
        <div className="flex items-center gap-2 text-zinc-400">
          <Search className="h-4 w-4" />
          <input
            autoFocus
            placeholder="Search tools, skills..."
            className="w-full bg-transparent text-zinc-900 outline-none placeholder:text-zinc-500 dark:text-zinc-100 dark:placeholder:text-zinc-600"
          />
        </div>
      </div>
    </div>
  )
}
