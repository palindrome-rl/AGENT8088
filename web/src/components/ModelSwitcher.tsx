import { useEffect, useState } from 'react'
import { ChevronDown, Loader2 } from 'lucide-react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useSessionStore } from '@/stores/session'
import { apiFetch } from '@/lib/api'

/* ─────────────────────────────────────────────────────────
 * MODEL SWITCHER — content-pane header control
 * Sits at the top-left of the main pane, above the chat,
 * so the active model reads as a property of the
 * conversation rather than of the navigation rail.
 * ───────────────────────────────────────────────────────── */

type ProviderDetail = { label: string; base_url: string; default_model: string; api_key_env: string; has_key: boolean }
type ProvidersResponse = { configured: string[]; builtins: string[]; active: string; details: Record<string, ProviderDetail> }
type ModelsResponse = { models: string[]; stale?: boolean; offline?: boolean; reason?: string }
type ModelSwitchResponse = { ok: boolean; provider?: string; model?: string; error?: string }

export function ModelSwitcher() {
  const { status, setStatus, isStreaming } = useSessionStore()
  const queryClient = useQueryClient()
  const [menuOpen, setMenuOpen] = useState(false)
  const [selectedProvider, setSelectedProvider] = useState('')
  const [selectedModel, setSelectedModel] = useState('')
  const [error, setError] = useState('')
  const [changing, setChanging] = useState(false)

  const providersQuery = useQuery({
    queryKey: ['providers'],
    queryFn: () => apiFetch<ProvidersResponse>('/api/providers', { action: 'load providers' }),
  })
  const modelsQuery = useQuery({
    queryKey: ['models', selectedProvider],
    queryFn: () => apiFetch<ModelsResponse>(`/api/models/${encodeURIComponent(selectedProvider)}`, { action: 'load models' }),
    enabled: Boolean(selectedProvider),
  })

  useEffect(() => {
    if (!menuOpen) return
    const closeMenu = (event: PointerEvent) => {
      if (!(event.target as Element)?.closest('[data-model-menu]')) setMenuOpen(false)
    }
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMenuOpen(false)
    }
    document.addEventListener('pointerdown', closeMenu)
    document.addEventListener('keydown', handleEscape)
    return () => {
      document.removeEventListener('pointerdown', closeMenu)
      document.removeEventListener('keydown', handleEscape)
    }
  }, [menuOpen])

  const openMenu = () => {
    setError('')
    setSelectedProvider(status?.provider || providersQuery.data?.active || '')
    setSelectedModel(status?.model || '')
    setMenuOpen((open) => !open)
  }

  const switchModel = async () => {
    if (!selectedProvider || !selectedModel) return
    setChanging(true)
    setError('')
    try {
      const result = await apiFetch<ModelSwitchResponse>('/api/model/switch', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider: selectedProvider, model: selectedModel }),
        action: 'switch the model',
      })
      if (!result.ok) throw new Error(result.error || 'Could not switch the model')
      if (status) setStatus({ ...status, provider: result.provider || selectedProvider, model: result.model || selectedModel })
      setMenuOpen(false)
      void queryClient.invalidateQueries({ queryKey: ['providers'] })
      void queryClient.invalidateQueries({ queryKey: ['config'] })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not switch the model')
    } finally {
      setChanging(false)
    }
  }

  return (
    <div data-model-menu className="relative">
      <button
        type="button"
        aria-label="Change model"
        aria-expanded={menuOpen}
        disabled={isStreaming}
        onClick={openMenu}
        className="flex h-8 max-w-[280px] items-center gap-1.5 rounded-lg px-2 text-left text-[15px] font-medium text-zinc-800 transition-colors hover:bg-zinc-100 disabled:opacity-40 dark:text-zinc-100 dark:hover:bg-zinc-800/50"
        title={status ? `${status.provider}:${status.model}` : 'Change model'}
      >
        <span className="min-w-0 truncate">{status?.model || 'Choose model'}</span>
        <ChevronDown className="h-4 w-4 shrink-0 text-zinc-400 dark:text-zinc-500" />
      </button>
      {menuOpen && (
        <div className="absolute left-0 top-full z-30 mt-2 w-72 rounded-xl border border-zinc-200 bg-white p-3 shadow-xl shadow-black/10 dark:border-zinc-800 dark:bg-zinc-900 dark:shadow-black/40">
          <p className="mb-2 text-xs font-medium text-zinc-800 dark:text-zinc-100">Switch model</p>
          <label htmlFor="header-model-provider" className="mb-1 block text-[11px] text-zinc-500">Provider</label>
          <select id="header-model-provider" value={selectedProvider} onChange={(event) => { setSelectedProvider(event.target.value); setSelectedModel('') }} className="mb-2 w-full rounded-lg border border-zinc-200 bg-white px-2 py-1.5 text-xs text-zinc-800 outline-none focus:border-brand-primary dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100">
            <option value="">Select provider…</option>
            {[...new Set([...(providersQuery.data?.configured || []), ...(providersQuery.data?.builtins || [])])].sort().map((provider) => <option key={provider} value={provider}>{provider}</option>)}
          </select>
          {(() => {
            const detail = providersQuery.data?.details[selectedProvider]
            if (!detail?.api_key_env || detail.has_key) return null
            return (
              <p className="mb-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-600 dark:text-amber-400">
                No API key found for {selectedProvider}. Set <code className="font-mono">{detail.api_key_env}</code> (env var,
                or via <code className="font-mono">--setup</code>) and restart to use it.
              </p>
            )
          })()}
          <label htmlFor="header-model-name" className="mb-1 block text-[11px] text-zinc-500">Model</label>
          <input id="header-model-name" list="header-model-options" value={selectedModel} onChange={(event) => setSelectedModel(event.target.value)} placeholder={modelsQuery.isLoading ? 'Loading models…' : 'Select or type a model'} className="w-full rounded-lg border border-zinc-200 bg-white px-2 py-1.5 text-xs text-zinc-800 outline-none placeholder:text-zinc-400 focus:border-brand-primary dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-600" />
          <datalist id="header-model-options">{modelsQuery.data?.models.map((model) => <option key={model} value={model} />)}</datalist>
          {(modelsQuery.data?.stale || modelsQuery.data?.offline) && (
            <p className="mt-1 text-[11px] text-amber-600 dark:text-amber-400" title={modelsQuery.data.reason || undefined}>
              (offline list{modelsQuery.data.reason ? ` — ${modelsQuery.data.reason}` : ''})
            </p>
          )}
          {error && <p role="alert" className="mt-2 text-[11px] text-red-500 dark:text-red-400">{error}</p>}
          <button type="button" disabled={!selectedProvider || !selectedModel || changing} onClick={() => void switchModel()} className="mt-3 flex w-full items-center justify-center gap-1.5 rounded-lg bg-brand-primary px-2 py-1.5 text-xs font-medium text-white disabled:opacity-40">
            {changing && <Loader2 className="h-3.5 w-3.5 animate-spin" />}Switch model
          </button>
        </div>
      )}
    </div>
  )
}
