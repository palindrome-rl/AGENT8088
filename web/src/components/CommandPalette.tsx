import { useUIStore } from '@/stores/ui'
import { Search } from 'lucide-react'

export function CommandPalette() {
  const { commandPaletteOpen, setCommandPaletteOpen } = useUIStore()
  if (!commandPaletteOpen) return null
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 pt-20"
         onClick={() => setCommandPaletteOpen(false)}>
      <div className="w-full max-w-xl rounded-xl border border-zinc-700 bg-zinc-900 p-4 shadow-2xl"
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2 text-zinc-400">
          <Search className="h-4 w-4" />
          <input
            autoFocus
            placeholder="Search commands, tools, skills..."
            className="w-full bg-transparent text-zinc-100 outline-none placeholder:text-zinc-600"
          />
        </div>
      </div>
    </div>
  )
}