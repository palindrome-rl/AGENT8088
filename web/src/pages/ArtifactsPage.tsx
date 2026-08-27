import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  FolderOpen, Folder, FileText, Image as ImageIcon, Loader2, AlertCircle,
  ChevronRight, Download, FileCode,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import type { ArtifactItem, ArtifactsListing } from '@/types/api'

/* ─────────────────────────────────────────────────────────
 * ARTIFACTS — browse the agent's artifacts/ folder
 * Hermes-style: image grid cards + file table, type tabs,
 * name filter, breadcrumb navigation. Flat design, zinc palette.
 * ───────────────────────────────────────────────────────── */

async function fetchArtifacts(rel: string): Promise<ArtifactsListing> {
  const res = await fetch(`/api/artifacts?rel=${encodeURIComponent(rel)}`)
  if (!res.ok) throw new Error(`Failed to load artifacts (${res.status})`)
  const data = await res.json() as ArtifactsListing & { error?: string }
  if (data.error) throw new Error(data.error)
  return data
}

async function fetchContent(rel: string): Promise<string> {
  const res = await fetch(`/api/artifacts/content?rel=${encodeURIComponent(rel)}`)
  if (!res.ok) throw new Error(`Failed to load preview (${res.status})`)
  const data = await res.json() as { content?: string; error?: string }
  if (data.error) throw new Error(data.error)
  return data.content ?? ''
}

const TYPE_TABS = [
  { key: 'all', label: 'All' },
  { key: 'images', label: 'Images' },
  { key: 'files', label: 'Files' },
] as const
type TypeTab = typeof TYPE_TABS[number]['key']

function fileUrl(path: string) {
  return `/api/artifacts/file?rel=${encodeURIComponent(path)}`
}

function formatSize(bytes: number | null): string {
  if (bytes === null || bytes === undefined) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatDate(ts: number): string {
  return new Date(ts * 1000).toLocaleDateString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

// ── Image card (Hermes-style grid cell) ──────────────────────

function ImageCard({ item }: { item: ArtifactItem }) {
  return (
    <a
      href={fileUrl(item.path)}
      target="_blank"
      rel="noreferrer"
      className="group block overflow-hidden rounded-lg border border-zinc-800 bg-zinc-900/40 transition-colors hover:border-brand-primary/40"
    >
      <div className="flex h-28 items-center justify-center overflow-hidden bg-zinc-950">
        <img
          src={fileUrl(item.path)}
          alt={item.name}
          loading="lazy"
          className="max-h-full max-w-full object-contain transition-transform duration-200 group-hover:scale-[1.03]"
        />
      </div>
      <div className="border-t border-zinc-800/70 px-2.5 py-1.5">
        <div className="truncate text-[12px] font-medium text-zinc-200" title={item.name}>
          {item.name}
        </div>
        <div className="mt-0.5 flex items-center gap-1 text-[10px] text-zinc-500">
          <ImageIcon className="h-2.5 w-2.5" /> IMAGE
        </div>
      </div>
    </a>
  )
}

// ── File/dir row ─────────────────────────────────────────────

function ArtifactRow({ item, onOpen, onPreview, previewOpen }: {
  item: ArtifactItem
  onOpen: (item: ArtifactItem) => void
  onPreview: (item: ArtifactItem) => void
  previewOpen: boolean
}) {
  const isDir = item.type === 'dir'
  const isText = item.type === 'text'
  const Icon = isDir ? Folder : isText ? FileCode : FileText

  return (
    <>
      <tr className="group border-b border-zinc-800/60 transition-colors hover:bg-zinc-800/20 last:border-0">
        <td className="px-4 py-2.5">
          <button
            type="button"
            onClick={() => (isDir || isText ? onOpen(item) : undefined)}
            className="flex w-full items-center gap-2.5 text-left"
            title={isDir ? `Open ${item.name}` : isText ? 'Toggle preview' : item.name}
          >
            <Icon className={cn(
              'h-4 w-4 shrink-0',
              isDir ? 'text-brand-cyan/80' : isText ? 'text-emerald-400/70' : 'text-zinc-500',
            )} />
            <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-zinc-100">
              {item.name}
              {isDir && <span className="text-zinc-600">/</span>}
            </span>
            {isDir && item.size !== null && (
              <span className="shrink-0 text-[10px] text-zinc-600">{item.size} items</span>
            )}
          </button>
        </td>
        <td className="px-4 py-2.5">
          <span className={cn(
            'rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide',
            item.type === 'dir' ? 'bg-sky-500/15 text-sky-400'
              : item.type === 'text' ? 'bg-emerald-500/15 text-emerald-400'
              : item.type === 'image' ? 'bg-violet-500/15 text-violet-400'
              : 'bg-zinc-700/30 text-zinc-400',
          )}>
            {item.type}
          </span>
        </td>
        <td className="px-4 py-2.5 text-xs text-zinc-500">{formatSize(item.size)}</td>
        <td className="px-4 py-2.5 text-xs text-zinc-500">{formatDate(item.modified)}</td>
        <td className="px-4 py-2.5 text-right">
          {!isDir && (
            <div className="flex items-center justify-end gap-1 opacity-0 transition-opacity group-hover:opacity-100">
              {isText && (
                <button
                  type="button"
                  onClick={() => onPreview(item)}
                  className="rounded border border-zinc-800 px-2 py-0.5 text-[10px] text-zinc-300 transition-colors hover:border-brand-primary/40 hover:text-brand-cyan"
                >
                  {previewOpen ? 'Hide' : 'View'}
                </button>
              )}
              <a
                href={fileUrl(item.path)}
                download={item.name}
                aria-label={`Download ${item.name}`}
                className="rounded p-1 text-zinc-500 transition-colors hover:bg-zinc-800 hover:text-zinc-200"
              >
                <Download className="h-3.5 w-3.5" />
              </a>
            </div>
          )}
        </td>
      </tr>
      {previewOpen && isText && (
        <tr className="bg-zinc-950">
          <td colSpan={5} className="px-4 py-3">
            <TextPreview path={item.path} />
          </td>
        </tr>
      )}
    </>
  )
}

function TextPreview({ path }: { path: string }) {
  const preview = useQuery({
    queryKey: ['artifact-content', path],
    queryFn: () => fetchContent(path),
    staleTime: 30_000,
  })
  return (
    <div className="ml-6 border-l-2 border-brand-primary/30 pl-4">
      {preview.isLoading && (
        <div className="flex items-center gap-2 py-2 text-xs text-zinc-500">
          <Loader2 className="h-3.5 w-3.5 animate-spin text-brand-cyan" /> Loading preview…
        </div>
      )}
      {preview.isError && (
        <div className="rounded border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-400">
          {(preview.error as Error).message}
        </div>
      )}
      {preview.isSuccess && (
        <pre className="max-h-80 overflow-auto rounded-md bg-zinc-900 p-3 font-mono text-[11px] leading-relaxed text-zinc-300">
          {preview.data || '(empty file)'}
        </pre>
      )}
    </div>
  )
}

// ── Breadcrumb ───────────────────────────────────────────────

function Breadcrumb({ cwd, onNavigate }: { cwd: string; onNavigate: (rel: string) => void }) {
  const parts = cwd.split('/').filter(Boolean)
  return (
    <div className="flex items-center gap-1 text-[12px] text-zinc-500">
      <button
        type="button"
        onClick={() => onNavigate('')}
        className="rounded px-1 py-0.5 font-mono transition-colors hover:bg-zinc-100 hover:text-zinc-200 dark:hover:bg-zinc-800/50"
      >
        artifacts/
      </button>
      {parts.map((part, i) => (
        <span key={i} className="flex items-center gap-1">
          <ChevronRight className="h-3 w-3 text-zinc-700" />
          <button
            type="button"
            onClick={() => onNavigate(parts.slice(0, i + 1).join('/'))}
            className={cn(
              'rounded px-1 py-0.5 font-mono transition-colors hover:bg-zinc-100 hover:text-zinc-200 dark:hover:bg-zinc-800/50',
              i === parts.length - 1 && 'text-zinc-200',
            )}
          >
            {part}
          </button>
        </span>
      ))}
    </div>
  )
}

// ── Page ─────────────────────────────────────────────────────

export default function ArtifactsPage() {
  const [cwd, setCwd] = useState('')
  const [filter, setFilter] = useState('')
  const [tab, setTab] = useState<TypeTab>('all')
  const [previewPath, setPreviewPath] = useState<string | null>(null)

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['artifacts', cwd],
    queryFn: () => fetchArtifacts(cwd),
  })

  const items = data?.items ?? []
  const imageCount = items.filter((i) => i.type === 'image').length
  const fileCount = items.length - imageCount

  const matches = (i: ArtifactItem) =>
    !filter || i.name.toLowerCase().includes(filter.toLowerCase())

  const visible =
    tab === 'images' ? items.filter((i) => i.type === 'image' && matches(i))
    : tab === 'files' ? items.filter((i) => i.type !== 'image' && matches(i))
    : items.filter(matches)

  const gridItems = tab === 'all' || tab === 'images'
    ? visible.filter((i) => i.type === 'image')
    : []
  const tableItems = tab === 'all' || tab === 'files'
    ? visible.filter((i) => i.type !== 'image')
    : []

  const navigate = (rel: string) => {
    setCwd(rel)
    setPreviewPath(null)
    setFilter('')
  }

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <FolderOpen className="h-6 w-6 text-brand-cyan" />
          <div>
            <h1 className="text-lg font-semibold text-zinc-100">Artifacts</h1>
            <p className="text-xs text-zinc-500">Files created by agent runs</p>
          </div>
          {items.length > 0 && (
            <span className="rounded-full bg-zinc-800 px-2 py-0.5 text-[11px] text-zinc-400">
              {items.length}
            </span>
          )}
        </div>
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter…"
          className="w-48 rounded-md border border-zinc-800 bg-zinc-900 px-3 py-1.5 text-sm text-zinc-100 outline-none transition-colors placeholder:text-zinc-600 focus:border-brand-primary"
        />
      </div>

      {/* Breadcrumb */}
      {cwd && <Breadcrumb cwd={cwd} onNavigate={navigate} />}

      {/* Type tabs — Hermes style */}
      <div className="flex items-center gap-1 border-b border-zinc-800/60">
        {TYPE_TABS.map(({ key, label }) => {
          const count = key === 'all' ? items.length : key === 'images' ? imageCount : fileCount
          return (
            <button
              key={key}
              type="button"
              onClick={() => setTab(key)}
              className={cn(
                'flex items-center gap-1.5 border-b-2 px-3 py-1.5 text-[13px] transition-colors',
                tab === key
                  ? 'border-brand-cyan text-zinc-100'
                  : 'border-transparent text-zinc-500 hover:text-zinc-300',
              )}
            >
              {label}
              <span className="text-[11px] text-zinc-600">{count}</span>
            </button>
          )
        })}
      </div>

      {/* Loading / error / empty */}
      {isLoading && (
        <div className="flex items-center justify-center py-20">
          <Loader2 className="h-6 w-6 animate-spin text-brand-cyan" />
        </div>
      )}
      {isError && (
        <div className="flex items-center gap-2 rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-400">
          <AlertCircle className="h-4 w-4 shrink-0" /> {(error as Error)?.message ?? 'Failed to load artifacts.'}
        </div>
      )}
      {!isLoading && !isError && items.length === 0 && (
        <div className="py-20 text-center">
          <FolderOpen className="mx-auto mb-3 h-10 w-10 text-zinc-700" />
          <p className="text-sm text-zinc-500">No artifacts yet</p>
          <p className="mt-1 text-xs text-zinc-600">Files the agent writes land in the artifacts folder</p>
        </div>
      )}
      {!isLoading && !isError && visible.length === 0 && items.length > 0 && (
        <div className="py-16 text-center text-sm text-zinc-500">
          {filter ? `No artifacts match "${filter}".` : `No ${tab} in this folder.`}
        </div>
      )}

      {/* Image grid */}
      {gridItems.length > 0 && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          {gridItems.map((item) => (
            <ImageCard key={item.path} item={item} />
          ))}
        </div>
      )}

      {/* Files table */}
      {tableItems.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-zinc-800">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-800 bg-zinc-900/50 text-left text-xs uppercase tracking-wide text-zinc-500">
                <th className="px-4 py-2.5 font-medium">Name</th>
                <th className="px-4 py-2.5 font-medium">Type</th>
                <th className="px-4 py-2.5 font-medium">Size</th>
                <th className="px-4 py-2.5 font-medium">Modified</th>
                <th className="px-4 py-2.5" />
              </tr>
            </thead>
            <tbody>
              {tableItems.map((item) => (
                <ArtifactRow
                  key={item.path}
                  item={item}
                  previewOpen={previewPath === item.path}
                  onOpen={(i) => {
                    if (i.type === 'dir') navigate(i.path)
                    else if (i.type === 'text') setPreviewPath(previewPath === i.path ? null : i.path)
                  }}
                  onPreview={(i) => setPreviewPath(previewPath === i.path ? null : i.path)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}