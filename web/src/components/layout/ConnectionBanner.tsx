/**
 * Tells the user when the app is not actually talking to Agent8088.
 *
 * Before this the socket could be dead while the status bar still read
 * "ready", and a message typed into a dead socket was dropped silently — so
 * the two states that most need announcing were the two the UI never showed.
 */
import { WifiOff, RefreshCw } from 'lucide-react'
import { useSessionStore } from '@/stores/session'

export function ConnectionBanner() {
  const { connection, sendFailure } = useSessionStore()
  if (connection === 'open' && !sendFailure) return null
  if (connection === 'connecting' && !sendFailure) return null

  const reconnecting = connection === 'reconnecting'
  return (
    <div
      role="alert"
      aria-live="assertive"
      className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-amber-500/30 bg-amber-500/10 px-4 py-2 text-[13px] text-amber-800 dark:text-amber-200"
    >
      {reconnecting
        ? <RefreshCw className="h-3.5 w-3.5 shrink-0 animate-spin" aria-hidden="true" />
        : <WifiOff className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />}
      <span className="font-medium">
        {connection === 'failed'
          ? 'Disconnected from Agent8088.'
          : reconnecting
            ? 'Connection lost — reconnecting…'
            : 'Not connected.'}
      </span>
      <span className="text-amber-700/90 dark:text-amber-200/80">
        {sendFailure
          || (connection === 'failed'
            ? 'Check that the Agent8088 server is still running, then reload this page.'
            : 'Anything you send now is queued and delivered when the connection is back.')}
      </span>
    </div>
  )
}
