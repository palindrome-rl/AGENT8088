import { useQuery } from '@tanstack/react-query'
import type { CommandInfo } from '@/types/api'

export function useCommandCatalog() {
  return useQuery({
    queryKey: ['commands'],
    queryFn: async () => {
      const response = await fetch('/api/commands')
      if (!response.ok) throw new Error(`Could not load commands (${response.status})`)
      return response.json() as Promise<CommandInfo[]>
    },
    staleTime: Infinity,
  })
}
