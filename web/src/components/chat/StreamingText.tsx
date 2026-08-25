import { useSessionStore } from '@/stores/session'

export function StreamingText() {
  const { streamingText, isStreaming } = useSessionStore()
  if (!isStreaming && !streamingText) return null
  return (
    <div className="stream-cursor whitespace-pre-wrap text-zinc-200">
      {streamingText}
    </div>
  )
}