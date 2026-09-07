import { useEffect, useState } from 'react'
import { ChevronDown, ChevronUp, Clock, ShieldCheck, ShieldX } from 'lucide-react'
import { useUIStore } from '@/stores/ui'
import { useWebSocket } from '@/hooks/useWebSocket'
import { Button } from '@/components/atoms/Button'

/** One approval card; the arrow exposes the less common approval scopes. */
export function ApprovalCard() {
  const { approvalPending, setApprovalPending } = useUIStore()
  const { send } = useWebSocket()
  const [menuOpen, setMenuOpen] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    setMenuOpen(false)
    setError('')
  }, [approvalPending?.id])

  if (!approvalPending) return null

  const respond = (approved: boolean, sessionScope: boolean) => {
    const delivered = send({ type: 'approval', approved, session_scope: sessionScope, id: approvalPending.id })
    if (!delivered) {
      setError('Approval could not be sent. Keep this card open and try again when connected.')
      return
    }
    setApprovalPending(null)
  }

  return (
    <div className="mx-auto max-w-3xl px-6 py-3">
      <div className="w-full max-w-80">
        <div className="relative overflow-visible rounded-xl border border-brand-border/40 bg-white shadow-card dark:bg-zinc-900/50" style={{ animation: 'fade-up 380ms cubic-bezier(0.23,1,0.32,1) both' }}>
          <div className="primitive-card-pad">
            <div className="mb-2 flex items-center gap-2"><Clock className="h-4 w-4 text-brand-cyan" /><span className="text-[13px] font-semibold text-zinc-900 dark:text-zinc-100">Approval Required</span></div>
            <div className="text-[14px] font-medium text-zinc-900 dark:text-zinc-100">Allow {approvalPending.toolName}?</div>
            <div className="mt-1 text-[12px] font-mono text-brand-primary">{approvalPending.changeType}</div>
            <pre className="mt-2 max-h-28 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-zinc-100 p-2 font-mono text-[11px] text-zinc-600 dark:bg-zinc-950 dark:text-zinc-400">{approvalPending.description}</pre>
            {error && <p role="alert" className="mt-2 text-[11px] text-red-500 dark:text-red-400">{error}</p>}
          </div>
          <div className="primitive-card-footer flex items-center justify-between gap-3 border-t border-zinc-200 dark:border-zinc-800/60">
            <Button variant="ghost" size="sm" onClick={() => respond(false, false)}>Skip</Button>
            <div className="relative flex items-center">
              <Button variant="accent" size="sm" onClick={() => respond(true, false)}><ShieldCheck className="mr-1.5 h-3.5 w-3.5" />Approve</Button>
              <button type="button" aria-label="More approval options" aria-expanded={menuOpen} aria-haspopup="menu" onClick={() => setMenuOpen((open) => !open)} className="ml-1 flex h-8 w-7 items-center justify-center rounded-md text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900 dark:hover:bg-zinc-800 dark:hover:text-zinc-100">
                {menuOpen ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
              </button>
              {menuOpen && <div role="menu" className="absolute bottom-10 right-0 z-20 min-w-44 rounded-lg border border-brand-border/50 bg-white p-1 shadow-card dark:bg-zinc-900">
                <button type="button" role="menuitem" onClick={() => respond(true, false)} className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-xs text-zinc-700 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800"><ShieldCheck className="h-3.5 w-3.5 text-green-500" />Approve once</button>
                <button type="button" role="menuitem" onClick={() => respond(true, true)} className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-xs text-zinc-700 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800"><ShieldCheck className="h-3.5 w-3.5 text-green-500" />Approve for session</button>
                <button type="button" role="menuitem" onClick={() => respond(false, false)} className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-xs text-zinc-700 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800"><ShieldX className="h-3.5 w-3.5 text-red-500" />Deny</button>
              </div>}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
