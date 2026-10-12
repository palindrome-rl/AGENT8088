import { Check, X, FileText } from 'lucide-react'
import { useUIStore } from '@/stores/ui'
import { useWebSocket } from '@/hooks/useWebSocket'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useEffect, useState } from 'react'

export function PlanApprovalCard() {
  const { planApprovalPending, setPlanApprovalPending } = useUIStore()
  const { send } = useWebSocket()
  const [error, setError] = useState('')

  useEffect(() => { setError('') }, [planApprovalPending?.id])

  if (!planApprovalPending) return null

  const handleResponse = (mode: string) => {
    const delivered = send({ type: 'plan_approval', mode, id: planApprovalPending.id })
    if (!delivered) {
      setError('Approval could not be sent. Keep this card open and try again when connected.')
      return
    }
    setPlanApprovalPending(null)
  }

  // Same frame as ApprovalCard: the chat column, and light/dark surfaces. It was
  // dark-only and wider than the chat; `prose` did nothing (no typography
  // plugin), so the plan's headings and numbered steps rendered flat.
  return (
    <div className="mx-auto max-w-3xl px-6 py-3">
      <div className="rounded-xl border border-brand-border/40 bg-white p-4 shadow-card dark:bg-zinc-900/50" style={{ animation: 'fade-up 380ms cubic-bezier(0.23,1,0.32,1) both' }}>
        <div className="mb-3 flex items-center gap-2">
          <FileText className="h-5 w-5 text-brand-cyan" />
          <h3 className="text-[13px] font-semibold text-zinc-900 dark:text-zinc-100">Plan Proposal</h3>
        </div>
        <div className="mb-4 max-h-96 overflow-auto rounded-lg bg-zinc-100 p-3 dark:bg-zinc-950">
          <div className="chat-markdown min-w-0 max-w-full text-[13px] leading-relaxed text-zinc-700 dark:text-zinc-300 [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {planApprovalPending.plan}
            </ReactMarkdown>
          </div>
        </div>
        {error && <p role="alert" className="mb-3 text-xs text-red-600 dark:text-red-400">{error}</p>}
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => handleResponse('readonly')}
            className="flex items-center gap-1.5 rounded-lg bg-yellow-500/15 px-3 py-1.5 text-sm text-yellow-700 hover:bg-yellow-500/25 dark:bg-yellow-600/20 dark:text-yellow-400 dark:hover:bg-yellow-600/30"
          >
            <Check className="h-4 w-4" /> Run in readonly
          </button>
          <button
            onClick={() => handleResponse('full-auto')}
            className="flex items-center gap-1.5 rounded-lg bg-green-500/15 px-3 py-1.5 text-sm text-green-700 hover:bg-green-500/25 dark:bg-green-600/20 dark:text-green-400 dark:hover:bg-green-600/30"
          >
            <Check className="h-4 w-4" /> Run in full-auto
          </button>
          <button
            onClick={() => handleResponse('')}
            className="flex items-center gap-1.5 rounded-lg bg-red-500/15 px-3 py-1.5 text-sm text-red-700 hover:bg-red-500/25 dark:bg-red-600/20 dark:text-red-400 dark:hover:bg-red-600/30"
          >
            <X className="h-4 w-4" /> Decline
          </button>
        </div>
      </div>
    </div>
  )
}
