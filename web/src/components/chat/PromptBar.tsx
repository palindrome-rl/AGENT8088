import { useMemo, useState, useRef, useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Send, Square, Plus, Mic, Clipboard, ImagePlus, Terminal, Paperclip, Trash2, AlertCircle, Loader2, ChevronDown, Shield, Pencil, X, ListPlus } from 'lucide-react'
import { useSessionStore } from '@/stores/session'
import { useWebSocket, drainPromptQueue } from '@/hooks/useWebSocket'
import { useCommandCatalog } from '@/lib/commands'
import { useUIStore } from '@/stores/ui'
import { cn } from '@/lib/utils'
import { ImageUpload } from './ImageUpload'
import { Otto } from './Otto'

/* ─────────────────────────────────────────────────────────
 * PROMPT BAR — Beautiful UI style
 * Composer with @ sources, / commands, model picker,
 * dictation, and send. Pop-in menus, gliding highlight.
 * ───────────────────────────────────────────────────────── */

const sessionStopWords = new Set(['a', 'an', 'and', 'are', 'for', 'from', 'how', 'i', 'in', 'is', 'it', 'me', 'of', 'on', 'please', 'the', 'to', 'with'])

function topicSessionName(text: string) {
  const words = text.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim().split(/\s+/).filter(Boolean)
  const meaningful = words.filter((word) => !sessionStopWords.has(word))
  return (meaningful.length ? meaningful : words).slice(0, 7).join('-').slice(0, 64) || 'new-chat'
}

function parseToken(draft: string): { kind: 'at' | 'slash'; query: string; start: number } | null {
  const match = /(^|\s)([@/])([\w-]*)$/.exec(draft)
  if (!match) return null
  return {
    kind: match[2] === '@' ? 'at' : 'slash',
    query: match[3].toLowerCase(),
    start: match.index + match[1].length,
  }
}

type Attachment = { localId: string; file: File; id?: string; state: 'uploading' | 'ready' | 'error'; error?: string; warning?: string }

const permissionModes = [
  { value: 'plan-only', label: 'Plan', description: 'Research and propose before running' },
  { value: 'readonly', label: 'Read-only', description: 'Ask before any change' },
  { value: 'full-auto', label: 'Full auto', description: 'Run with configured permissions' },
] as const

export function PromptBar() {
  const [text, setText] = useState('')
  const [dismissed, setDismissed] = useState(false)
  const [plusOpen, setPlusOpen] = useState(false)
  const [showImageUpload, setShowImageUpload] = useState(false)
  const [pasteImage, setPasteImage] = useState<string | null>(null)
  const [listening, setListening] = useState(false)
  const [sessionError, setSessionError] = useState('')
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [modeOpen, setModeOpen] = useState(false)
  const [modeChanging, setModeChanging] = useState(false)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const { isStreaming, status, addMessage, setSessionName, setStatus, setRawLoading, setRawResult,
    promptQueue, queuePaused, enqueuePrompt, updateQueuedPrompt, removeQueuedPrompt, clearQueue,
    setQueuePaused } = useSessionStore()
  const { send: wsSend } = useWebSocket()
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingText, setEditingText] = useState('')
  const { data: commands = [] } = useCommandCatalog()
  const { rawPanelOpen, toggleRawPanel, setRawPanelOpen } = useUIStore()
  const queryClient = useQueryClient()

  const token = dismissed ? null : parseToken(text)
  const menu = plusOpen ? 'at' : token?.kind ?? null
  const query = plusOpen ? '' : token?.query ?? ''

  const rows = useMemo(() => menu === 'slash'
    ? commands.flatMap(command => [command.name, ...command.aliases])
      .filter(command => command && command.startsWith(query)).slice(0, 8)
    : [], [commands, menu, query])

  useEffect(() => {
    const insertCommand = (event: Event) => {
      setText((event as CustomEvent<string>).detail)
      setDismissed(true)
      requestAnimationFrame(() => inputRef.current?.focus())
    }
    window.addEventListener('agent8088:insert-command', insertCommand)
    return () => window.removeEventListener('agent8088:insert-command', insertCommand)
  }, [])

  // Auto-grow textarea
  useEffect(() => {
    const input = inputRef.current
    if (!input) return

    input.style.height = '0px'
    const h = Math.min(Math.max(input.scrollHeight, 28), 100)
    input.style.height = `${h}px`
  }, [text])

  // Close menus on outside click
  useEffect(() => {
    if (!menu && !plusOpen && !modeOpen) return
    const close = (e: PointerEvent) => {
      if (!(e.target as Element)?.closest('[data-promptbar]')) {
        setPlusOpen(false)
        setModeOpen(false)
        setDismissed(true)
      }
    }
    document.addEventListener('pointerdown', close)
    return () => document.removeEventListener('pointerdown', close)
  }, [menu, plusOpen, modeOpen])

  const sessionCreation = useRef<Promise<void> | null>(null)
  const createSession = async (topic = '') => {
    if (useSessionStore.getState().sessionName) return

    const statusResponse = await fetch('/api/status')
    if (!statusResponse.ok) throw new Error(`Could not read session status (${statusResponse.status})`)
    const status = await statusResponse.json() as { session_name?: string }
    if (status.session_name) {
      setSessionName(status.session_name)
      return
    }

    const baseName = topicSessionName(topic)
    for (let attempt = 0; attempt < 10; attempt += 1) {
      const name = attempt === 0 ? baseName : `${baseName}-${attempt + 1}`
      const response = await fetch('/api/sessions/new', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      })
      const result = await response.json().catch(() => ({})) as { error?: string; name?: string }
      if (response.status === 409 || result.error?.startsWith('session exists:')) continue
      if (!response.ok || result.error) throw new Error(result.error ?? `Could not create session (${response.status})`)
      setSessionName(result.name || name)
      void queryClient.invalidateQueries({ queryKey: ['sessions'] })
      return
    }
    throw new Error('Could not create a unique session name')
  }

  const ensureSession = (topic = '') => {
    if (!sessionCreation.current) {
      sessionCreation.current = createSession(topic).finally(() => { sessionCreation.current = null })
    }
    return sessionCreation.current
  }

  const uploadAttachment = async (attachment: Attachment) => {
    try {
      await ensureSession(attachment.file.name)
      const response = await fetch('/api/attachments', {
        method: 'POST', headers: { 'X-Filename': attachment.file.name }, body: attachment.file,
      })
      const result = await response.json() as { id?: string; error?: string; warning?: string }
      if (!response.ok || !result.id) throw new Error(result.error ?? 'Upload failed')
      setAttachments((items) => items.map((item) => item.localId === attachment.localId
        ? { ...item, id: result.id, state: 'ready', error: undefined, warning: result.warning } : item))
    } catch (error) {
      setAttachments((items) => items.map((item) => item.localId === attachment.localId
        ? { ...item, state: 'error', error: error instanceof Error ? error.message : 'Upload failed' } : item))
    }
  }

  const addFiles = (files: FileList | null) => {
    if (!files) return
    const selected = Array.from(files).slice(0, Math.max(0, 5 - attachments.length))
    for (const file of selected) {
      const attachment = { localId: crypto.randomUUID(), file, state: 'uploading' as const }
      setAttachments((items) => [...items, attachment])
      void uploadAttachment(attachment)
    }
  }

  const removeAttachment = async (attachment: Attachment) => {
    setAttachments((items) => items.filter((item) => item.localId !== attachment.localId))
    if (attachment.id) await fetch(`/api/attachments/${attachment.id}`, { method: 'DELETE' })
  }

  const setPermissionMode = async (mode: (typeof permissionModes)[number]['value']) => {
    setModeOpen(false)
    if (mode === status?.permission_mode) return
    setModeChanging(true)
    setSessionError('')
    try {
      const response = await fetch('/api/mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
      })
      const result = await response.json() as { mode?: typeof mode; error?: string }
      if (!response.ok || !result.mode) throw new Error(result.error ?? 'Could not change permission mode')
      if (status) setStatus({ ...status, permission_mode: result.mode })
    } catch (error) {
      setSessionError(error instanceof Error ? error.message : 'Could not change permission mode')
    } finally {
      setModeChanging(false)
    }
  }

  const startEditing = (id: string, current: string) => {
    setEditingId(id)
    setEditingText(current)
  }

  const commitEditing = () => {
    if (!editingId) return
    const next = editingText.trim()
    // An emptied prompt is a deletion — keeping a blank row that sends nothing
    // would be worse than either outcome.
    if (next) updateQueuedPrompt(editingId, next)
    else removeQueuedPrompt(editingId)
    setEditingId(null)
    setEditingText('')
  }

  const cancelEditing = () => {
    setEditingId(null)
    setEditingText('')
  }

  const resumeQueue = () => {
    setQueuePaused(false)
    drainPromptQueue()
  }

  const resetComposer = () => {
    setText('')
    setDismissed(false)
    setPlusOpen(false)
    setShowImageUpload(false)
    setPasteImage(null)
    setAttachments((items) => items.filter((attachment) => attachment.state === 'error'))
  }

  const handleSend = async () => {
    const trimmed = text.trim()
    const readyAttachments = attachments.filter((attachment) => attachment.state === 'ready' && attachment.id)
    if ((!trimmed && readyAttachments.length === 0) || attachments.some((attachment) => attachment.state === 'uploading')) return
    setSessionError('')

    // A turn is already running and the backend refuses a second one, so the
    // prompt waits in the queue instead of being dropped. The session is
    // created now rather than at dispatch time, which keeps the dispatcher
    // (in the socket handler) free of async work.
    if (isStreaming) {
      const queuedText = trimmed || `Please review ${readyAttachments.map((attachment) => attachment.file.name).join(', ')}.`
      if (!queuedText.startsWith('/')) {
        try {
          await ensureSession(queuedText)
        } catch (error) {
          setSessionError(error instanceof Error ? error.message : 'Could not create a session')
          return
        }
      }
      enqueuePrompt({
        kind: queuedText.startsWith('/') ? 'command' : 'chat',
        text: queuedText,
        attachmentIds: readyAttachments.flatMap((attachment) => attachment.id ? [attachment.id] : []),
        attachments: readyAttachments.map(({ id, file }) => ({ id, name: file.name, type: file.type, size: file.size })),
      })
      resetComposer()
      return
    }
    if (trimmed.startsWith('/')) {
      const [cmd, ...rest] = trimmed.slice(1).split(' ')
      if (cmd.toLowerCase() === 'raw') {
        setRawResult(null)
        setRawLoading(true)
        setRawPanelOpen(true)
      }
      wsSend({ type: 'command', command: cmd, args: rest.join(' ') })
    } else {
      const message = trimmed || `Please review ${readyAttachments.map((attachment) => attachment.file.name).join(', ')}.`
      try {
        await ensureSession(message)
      } catch (error) {
        setSessionError(error instanceof Error ? error.message : 'Could not create a session')
        return
      }
      addMessage({
        role: 'user', content: message,
        attachments: readyAttachments.map(({ id, file }) => ({ id, name: file.name, type: file.type, size: file.size })),
      })
      wsSend({ type: 'chat', text: message, attachments: readyAttachments.flatMap((attachment) => attachment.id ? [attachment.id] : []) })
    }
    resetComposer()
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (menu && rows.length > 0) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault()
        return
      }
      const exactCommand = rows.includes(text.trim().slice(1).toLowerCase())
      if (((e.key === 'Enter' && !e.shiftKey && !exactCommand) || e.key === 'Tab')) {
        e.preventDefault()
        const cmd = rows[0]
        setText(`/${cmd} `)
        setDismissed(true)
        setPlusOpen(false)
        inputRef.current?.focus()
        return
      }
    }
    if (e.key === 'Escape') {
      setDismissed(true)
      setPlusOpen(false)
      return
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void handleSend()
    }
  }

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setText(e.target.value)
    setSessionError('')
    setDismissed(false)
    setPlusOpen(false)
  }

  const canSend = text.trim().length > 0 || attachments.some((attachment) => attachment.state === 'ready')

  return (
    <div
      data-promptbar
      className="relative pb-4 pt-2"
    >
      {sessionError && (
        <div role="alert" className="mx-auto mb-2 max-w-2xl px-4 text-xs text-red-500 dark:text-red-400">
          {sessionError}
        </div>
      )}

      {/* ── prompt queue ───────────────────────────────── */}
      {promptQueue.length > 0 && (
        <div className="mx-auto mb-2 max-w-2xl px-4">
          <div className="overflow-hidden rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900">
            <div className="flex items-center justify-between border-b border-zinc-200 dark:border-zinc-800/60 px-3 py-1.5">
              <span className="text-[11px] text-zinc-500 dark:text-zinc-400">
                Queued · {promptQueue.length}
                {queuePaused && ' · paused'}
              </span>
              <div className="flex items-center gap-1">
                {queuePaused && (
                  <button
                    type="button"
                    aria-label="Resume queue"
                    onClick={resumeQueue}
                    className="rounded-md px-2 py-0.5 text-[11px] text-brand-cyan transition-colors hover:bg-zinc-100 dark:hover:bg-zinc-800/60"
                  >
                    Resume
                  </button>
                )}
                <button
                  type="button"
                  aria-label="Clear queue"
                  onClick={clearQueue}
                  className="rounded-md px-2 py-0.5 text-[11px] text-zinc-500 transition-colors hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800/60"
                >
                  Clear
                </button>
              </div>
            </div>
            {queuePaused && (
              <p role="status" className="px-3 py-1.5 text-[11px] text-amber-600 dark:text-amber-400">
                The last turn stopped before finishing, so the queue is holding.
                Resume to run the next prompt.
              </p>
            )}
            <ul>
              {promptQueue.map((queued, index) => (
                <li
                  key={queued.id}
                  className="flex items-start gap-2 border-b border-zinc-100 px-3 py-1.5 last:border-b-0 dark:border-zinc-800/40"
                >
                  <span className="mt-0.5 w-4 shrink-0 text-[11px] text-zinc-400 dark:text-zinc-500">
                    {index + 1}
                  </span>
                  {editingId === queued.id ? (
                    <textarea
                      autoFocus
                      aria-label="Edit queued prompt"
                      value={editingText}
                      onChange={(e) => setEditingText(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && !e.shiftKey) {
                          e.preventDefault()
                          commitEditing()
                        } else if (e.key === 'Escape') {
                          e.preventDefault()
                          cancelEditing()
                        }
                      }}
                      onBlur={commitEditing}
                      rows={1}
                      className="min-h-[24px] flex-1 resize-none bg-transparent text-[13px] text-zinc-800 outline-none dark:text-zinc-100"
                    />
                  ) : (
                    <span
                      className={cn('flex-1 whitespace-pre-wrap break-words text-[13px]',
                        queued.kind === 'command'
                          ? 'font-mono text-brand-cyan'
                          : 'text-zinc-700 dark:text-zinc-200')}
                    >
                      {queued.text}
                    </span>
                  )}
                  {editingId !== queued.id && (
                    <div className="flex shrink-0 items-center gap-0.5">
                      <button
                        type="button"
                        aria-label={`Edit queued prompt ${index + 1}`}
                        onClick={() => startEditing(queued.id, queued.text)}
                        className="rounded-md p-1 text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800/60 dark:hover:text-zinc-200"
                      >
                        <Pencil className="h-3 w-3" />
                      </button>
                      <button
                        type="button"
                        aria-label={`Remove queued prompt ${index + 1}`}
                        onClick={() => removeQueuedPrompt(queued.id)}
                        className="rounded-md p-1 text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-red-500 dark:hover:bg-zinc-800/60"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
      {/* ── / command menu ─────────────────────────────── */}
      {menu === 'slash' && rows.length > 0 && (
        <div
          className="absolute bottom-full left-1/2 mb-2 w-full max-w-2xl -translate-x-1/2 overflow-hidden rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 shadow-2xl shadow-black/10 dark:shadow-black/40"
          style={{ animation: 'pop-in 180ms cubic-bezier(0.23,1,0.32,1) both', transformOrigin: 'bottom center' }}
        >
          <div className="border-b border-zinc-200 dark:border-zinc-800/60 px-3 py-1 text-[11px] text-zinc-400 dark:text-zinc-500">
            Commands
          </div>
          {rows.map((cmd, i) => (
            <button
              key={cmd}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => { setText(`/${cmd} `); setDismissed(true); setPlusOpen(false); inputRef.current?.focus() }}
              className="block w-full px-3 py-1.5 text-left text-[13px] transition-colors hover:bg-zinc-100 dark:hover:bg-zinc-800/50"
              style={{ animation: `fade-up 200ms cubic-bezier(0.23,1,0.32,1) ${i * 40}ms both` }}
            >
              <span className="font-mono text-brand-cyan">/{cmd}</span>
            </button>
          ))}
        </div>
      )}

      {/* ── @ source menu (plus button) ─────────────────── */}
      {plusOpen && (
        <div
          className="absolute bottom-full left-1/2 mb-2 w-full max-w-2xl -translate-x-1/2 overflow-hidden rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 shadow-2xl shadow-black/10 dark:shadow-black/40"
          style={{ animation: 'pop-in 180ms cubic-bezier(0.23,1,0.32,1) both', transformOrigin: 'bottom center' }}
        >
          <div className="border-b border-zinc-200 dark:border-zinc-800/60 px-3 py-1 text-[11px] text-zinc-400 dark:text-zinc-500">
            Sources
          </div>
          <div className="p-1">
            {[
              { label: 'Web search', Icon: Plus, action: () => { setText('/search '); inputRef.current?.focus() } },
              { label: 'Memory recall', Icon: Plus, action: () => { setText('/memory '); inputRef.current?.focus() } },
              { label: 'Attach files', Icon: Paperclip, action: () => fileInputRef.current?.click() },
              { label: 'Image analysis', Icon: ImagePlus, action: () => { setShowImageUpload(true) } },
            ].map(({ label, Icon, action }, i) => (
              <button
                key={label}
                type="button"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => {
                  action()
                  setDismissed(true)
                  setPlusOpen(false)
                }}
                className="flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-zinc-100 dark:hover:bg-zinc-800/50"
                style={{ animation: `fade-up 200ms cubic-bezier(0.23,1,0.32,1) ${i * 60}ms both` }}
              >
                <Icon className="h-3.5 w-3.5 text-zinc-400" />
                <span className="text-[12.5px] font-medium text-zinc-700 dark:text-zinc-200">{label}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* ── composer ───────────────────────────────────── */}
      <div className="mx-auto w-full max-w-2xl">
        <input ref={fileInputRef} type="file" multiple className="hidden" accept=".txt,.md,.csv,.json,.pdf,.docx,.xlsx,.pptx,image/png,image/jpeg,image/gif,image/webp" onChange={(event) => { addFiles(event.target.files); event.currentTarget.value = '' }} />
        {/* ── image upload panel ──────────────────────── */}
        {showImageUpload && (
          <ImageUpload
            initialImage={pasteImage}
            onClose={() => { setShowImageUpload(false); setPasteImage(null) }}
          />
        )}
        {attachments.length > 0 && <div className="mb-2 flex flex-wrap gap-1.5 px-1">
          {attachments.map((attachment) => <div key={attachment.localId} className="flex max-w-full items-center gap-1 rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-[11px] text-zinc-300">
            {attachment.state === 'uploading' ? <Loader2 className="h-3 w-3 animate-spin text-brand-cyan" /> : attachment.state === 'error' ? <AlertCircle className="h-3 w-3 text-red-400" /> : <Paperclip className="h-3 w-3 text-brand-cyan" />}
            <span className="max-w-40 truncate">{attachment.file.name}</span>
            <span role="status" className="text-zinc-400">{attachment.state === 'uploading' ? 'Uploading / extracting' : attachment.state === 'ready' ? 'Ready' : 'Upload failed'}</span>
            {attachment.warning && <span title={attachment.warning} className="text-amber-400">Needs attention: {attachment.warning}</span>}
            {attachment.state === 'error' && <button type="button" title={attachment.error} onClick={() => void uploadAttachment({ ...attachment, state: 'uploading' })} className="text-red-300 hover:text-red-100">Retry</button>}
            <button type="button" aria-label={`Remove ${attachment.file.name}`} onClick={() => void removeAttachment(attachment)} className="text-zinc-500 hover:text-zinc-200"><Trash2 className="h-3 w-3" /></button>
          </div>)}
        </div>}
        <div
          className={cn(
            'relative flex flex-col overflow-visible border bg-white dark:bg-zinc-900/50 transition-[border-color] duration-150',
            'border-zinc-300 dark:border-zinc-800 focus-within:border-brand-primary/40',
            'rounded-[14px] p-1.5 gap-1.5',
          )}
        >
          <div className="flex items-end gap-1.5">
            {/* Plus button — attachments */}
            <button
              type="button"
              aria-label="Add attachments and sources"
              aria-expanded={plusOpen}
              onClick={() => { setPlusOpen(!plusOpen); setDismissed(true); inputRef.current?.focus() }}
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-zinc-400 transition-all hover:bg-zinc-100 dark:hover:bg-zinc-800/50 hover:text-zinc-700 dark:hover:text-zinc-200 active:scale-95"
            >
              <Plus className="h-4 w-4" />
            </button>

            <div className="relative shrink-0">
              <button
                type="button"
                aria-label="Permission mode"
                aria-expanded={modeOpen}
                disabled={modeChanging || isStreaming}
                onClick={() => setModeOpen((open) => !open)}
                className="flex h-7 items-center gap-1 rounded-lg px-2 text-[11px] font-medium text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-800 disabled:opacity-40 dark:text-zinc-400 dark:hover:bg-zinc-800/50 dark:hover:text-zinc-100"
              >
                <Shield className="h-3.5 w-3.5" />
                <span>{permissionModes.find((mode) => mode.value === status?.permission_mode)?.label ?? 'Mode'}</span>
                {modeChanging ? <Loader2 className="h-3 w-3 animate-spin" /> : <ChevronDown className="h-3 w-3" />}
              </button>
              {modeOpen && (
                <div className="absolute bottom-full left-0 z-10 mb-2 w-56 overflow-hidden rounded-xl border border-zinc-200 bg-white p-1 shadow-xl shadow-black/10 dark:border-zinc-800 dark:bg-zinc-900 dark:shadow-black/40">
                  {permissionModes.map((mode) => (
                    <button
                      key={mode.value}
                      type="button"
                      onClick={() => void setPermissionMode(mode.value)}
                      className={cn(
                        'block w-full rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-zinc-100 dark:hover:bg-zinc-800/50',
                        status?.permission_mode === mode.value && 'bg-zinc-100 dark:bg-zinc-800/50',
                      )}
                    >
                      <span className="block text-xs font-medium text-zinc-800 dark:text-zinc-100">{mode.label}</span>
                      <span className="block text-[10px] text-zinc-500 dark:text-zinc-400">{mode.description}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* Textarea */}
            <textarea
              ref={inputRef}
              rows={1}
              value={text}
              onChange={handleChange}
              onKeyDown={handleKeyDown}
              placeholder={listening ? 'Listening…' : 'Send a message…'}
              aria-label="Prompt"
              className={cn(
                'prompt-textarea min-w-0 flex-1 resize-none bg-transparent text-[14px] text-zinc-900 dark:text-zinc-100 placeholder:text-zinc-400 dark:placeholder:text-zinc-600 focus:outline-none',
                'px-1 py-1.5',
                '[overflow-wrap:anywhere]',
              )}
              style={{ minHeight: '28px' }}
            />

            {/* Otto — mascot & status light, bottom-right of the composer */}
            <Otto className="self-end mb-0.5" />

            {/* Controls row */}
            <div className="flex shrink-0 items-center gap-1">
              {/* Raw model call toggle */}
              <button
                type="button"
                aria-label="Raw model call"
                aria-expanded={rawPanelOpen}
                title="Raw model call"
                onClick={toggleRawPanel}
                className={cn(
                  'flex h-7 w-7 items-center justify-center rounded-lg transition-all active:scale-95',
                  rawPanelOpen
                    ? 'bg-brand-primary/15 text-brand-cyan'
                    : 'text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800/50 hover:text-zinc-700 dark:hover:text-zinc-200',
                )}
              >
                <Terminal className="h-4 w-4" />
              </button>

              {/* Paste image from clipboard */}
              <button
                type="button"
                aria-label="Paste image from clipboard"
                onClick={async () => {
                  try {
                    const items = await navigator.clipboard.read()
                    for (const item of items) {
                      for (const type of item.types) {
                        if (type.startsWith('image/')) {
                          const blob = await item.getType(type)
                          const reader = new FileReader()
                          reader.onload = () => {
                            setPasteImage(reader.result as string)
                            setShowImageUpload(true)
                          }
                          reader.readAsDataURL(blob)
                          return
                        }
                      }
                    }
                    // No image in clipboard — open panel for manual paste
                    setShowImageUpload(true)
                  } catch {
                    // Clipboard API unavailable — open panel for manual paste
                    setShowImageUpload(true)
                  }
                }}
                className="flex h-7 w-7 items-center justify-center rounded-lg text-zinc-400 transition-all hover:bg-zinc-100 dark:hover:bg-zinc-800/50 hover:text-zinc-700 dark:hover:text-zinc-200 active:scale-95"
              >
                <Clipboard className="h-4 w-4" />
              </button>

              {/* Dictation */}
              <button
                type="button"
                aria-label={listening ? 'Stop dictation' : 'Start dictation'}
                onClick={() => setListening(!listening)}
                className={cn(
                  'flex h-7 w-7 items-center justify-center rounded-lg transition-all active:scale-95',
                  listening
                    ? 'bg-brand-primary/15 text-brand-cyan'
                    : 'text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800/50 hover:text-zinc-700 dark:hover:text-zinc-200',
                )}
              >
                {listening ? (
                  <span className="flex h-3 items-center gap-[2px]">
                    {[0, 1, 2].map(i => (
                      <span
                        key={i}
                        className="w-[2px] rounded-full bg-current"
                        style={{ height: '100%', animation: `eq-bounce 900ms ease-in-out ${i * 150}ms infinite` }}
                      />
                    ))}
                  </span>
                ) : (
                  <Mic className="h-4 w-4" />
                )}
              </button>

              {/* Queue — only while a turn is running, where Send is Stop and
                  Enter would otherwise be the sole way to queue a prompt. */}
              {isStreaming && canSend && (
                <button
                  type="button"
                  aria-label="Add to queue"
                  title="Add to queue"
                  onClick={() => void handleSend()}
                  className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-zinc-200 text-zinc-600 transition-all duration-200 hover:bg-zinc-300 active:scale-95 dark:bg-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-700"
                >
                  <ListPlus className="h-4 w-4" />
                </button>
              )}

              {/* Send — tactile square */}
              <button
                type="button"
                // The same button becomes the stop control while a turn runs;
                // a fixed "Send" label told screen-reader users they were
                // sending when they were interrupting.
                aria-label={isStreaming ? 'Stop generating' : 'Send'}
                disabled={!canSend && !isStreaming}
                onClick={isStreaming ? () => wsSend({ type: 'interrupt' }) : handleSend}
                className={cn(
                  'flex h-7 w-7 shrink-0 items-center justify-center rounded-lg transition-all duration-200 enabled:active:scale-95',
                  isStreaming
                    ? 'bg-red-600/15 text-red-400 hover:bg-red-600/25'
                    : canSend
                      ? 'bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900'
                      : 'bg-zinc-200 text-zinc-400 dark:bg-zinc-800 dark:text-zinc-600',
                )}
              >
                {isStreaming ? <Square className="h-3 w-3" /> : <Send className="h-4 w-4" strokeWidth={2.4} />}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
