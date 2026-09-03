import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  BookOpen, ChevronDown, ChevronRight, Loader2, FileText, Power,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import type { SkillPackage } from '@/types/api'

/* ------------------------------------------------------------------ */
/* API helpers                                                         */
/* ------------------------------------------------------------------ */

async function fetchSkills(): Promise<SkillPackage[]> {
  const res = await fetch('/api/skills')
  if (!res.ok) throw new Error(`Failed to load skills (${res.status})`)
  return res.json() as Promise<SkillPackage[]>
}

async function fetchSkillResource(name: string, resource: string): Promise<string> {
  const res = await fetch(`/api/skills/${encodeURIComponent(name)}/resource/${encodeURIComponent(resource)}`)
  if (!res.ok) throw new Error(`Failed to load ${resource} (${res.status})`)
  const data = await res.json() as { content: string }
  return data.content
}

async function toggleSkill(name: string, enable: boolean): Promise<{ name: string; enabled: boolean }> {
  const res = await fetch(`/api/skills/${encodeURIComponent(name)}/toggle`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enable }),
  })
  if (!res.ok) throw new Error(`Toggle failed (${res.status})`)
  return res.json() as Promise<{ name: string; enabled: boolean }>
}

/* ------------------------------------------------------------------ */
/* Skill card                                                          */
/* ------------------------------------------------------------------ */

interface SkillCardProps {
  skill: SkillPackage
}

function SkillCard({ skill }: SkillCardProps) {
  const [expanded, setExpanded] = useState(false)
  const queryClient = useQueryClient()

  // Lazy-load SKILL.md body only when expanded
  const resourceQuery = useQuery({
    queryKey: ['skill', skill.name, 'SKILL.md'],
    queryFn: () => fetchSkillResource(skill.name, 'SKILL.md'),
    enabled: expanded,
  })

  const toggleMutation = useMutation({
    mutationFn: (enable: boolean) => toggleSkill(skill.name, enable),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['skills'] }),
  })

  return (
    <div className={cn(
      'rounded-lg border transition-colors',
      skill.enabled ? 'border-zinc-800 bg-zinc-900/30' : 'border-zinc-800/50 bg-zinc-950',
    )}>
      {/* header row */}
      <div className="flex items-start gap-3 p-4">
        <button
          onClick={() => setExpanded((v) => !v)}
          className="mt-0.5 text-zinc-500 hover:text-zinc-200"
          aria-label={expanded ? 'Collapse' : 'Expand'}
        >
          {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </button>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <BookOpen className="h-3.5 w-3.5 shrink-0 text-brand-cyan/70" />
            <h3 className="truncate font-mono text-sm font-medium text-zinc-100">{skill.name}</h3>
          </div>
          <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-zinc-400">
            {skill.description || 'No description'}
          </p>
          {skill.resources.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {skill.resources.slice(0, 6).map((r) => (
                <span key={r} className="rounded bg-zinc-800/60 px-1.5 py-0.5 text-[10px] text-zinc-500">
                  {r}
                </span>
              ))}
              {skill.resources.length > 6 && (
                <span className="text-[10px] text-zinc-600">+{skill.resources.length - 6} more</span>
              )}
            </div>
          )}
        </div>

        {/* toggle */}
        <button
          onClick={() => toggleMutation.mutate(!skill.enabled)}
          disabled={toggleMutation.isPending}
          className={cn(
            'flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] font-medium transition-colors disabled:opacity-50',
            skill.enabled
              ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20'
              : 'border-zinc-700 bg-zinc-800/50 text-zinc-400 hover:bg-zinc-800',
          )}
          title={skill.enabled ? 'Disable skill' : 'Enable skill'}
        >
          <Power className="h-3 w-3" />
          {skill.enabled ? 'Enabled' : 'Disabled'}
        </button>
      </div>

      {/* expanded body */}
      {expanded && (
        <div className="border-t border-zinc-800/70 px-4 py-3">
          {resourceQuery.isLoading && (
            <div className="flex items-center gap-2 py-4 text-xs text-zinc-500">
              <Loader2 className="h-3.5 w-3.5 animate-spin text-brand-cyan" />
              Loading SKILL.md…
            </div>
          )}
          {resourceQuery.isError && (
            <div className="rounded border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-400">
              {(resourceQuery.error as Error)?.message ?? 'Failed to load resource'}
            </div>
          )}
          {resourceQuery.isSuccess && (
            <div>
              <div className="mb-2 flex items-center gap-1.5 text-[11px] text-zinc-500">
                <FileText className="h-3 w-3" /> SKILL.md
              </div>
              <pre className="max-h-80 overflow-auto rounded-md bg-zinc-950 p-3 font-mono text-[11px] leading-relaxed text-zinc-300">
{resourceQuery.data || '(empty)'}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* Page                                                                */
/* ------------------------------------------------------------------ */

export default function SkillsPage() {
  const [filter, setFilter] = useState('')

  const { data: skills, isLoading, isError, error } = useQuery({
    queryKey: ['skills'],
    queryFn: fetchSkills,
  })

  const filtered = (skills ?? []).filter((s) =>
    !filter || s.name.toLowerCase().includes(filter.toLowerCase()) || s.description.toLowerCase().includes(filter.toLowerCase()),
  )

  const enabledCount = (skills ?? []).filter((s) => s.enabled).length

  return (
    <div className="mx-auto max-w-5xl p-6">
      {/* header */}
      <div className="mb-6 flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <BookOpen className="h-5 w-5 text-brand-cyan" />
          <h1 className="text-lg font-semibold text-zinc-100">Skills</h1>
          {skills && (
            <>
              <span className="rounded-full bg-zinc-800 px-2 py-0.5 text-[11px] text-zinc-400">
                {skills.length}
              </span>
              <span className="text-xs text-zinc-600">({enabledCount} enabled)</span>
            </>
          )}
        </div>
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter…"
          className="w-48 rounded-md border border-zinc-800 bg-zinc-900 px-3 py-1.5 text-sm text-zinc-100 outline-none transition-colors placeholder:text-zinc-600 focus:border-brand-primary"
        />
      </div>

      {/* loading */}
      {isLoading && (
        <div className="flex items-center justify-center py-20">
          <Loader2 className="h-6 w-6 animate-spin text-brand-cyan" />
        </div>
      )}

      {/* error */}
      {isError && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-400">
          {(error as Error)?.message ?? 'Failed to load skills.'}
        </div>
      )}

      {/* grid */}
      {skills && filtered.length > 0 && (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {filtered.map((skill) => (
            <SkillCard key={skill.name} skill={skill} />
          ))}
        </div>
      )}

      {/* empty */}
      {skills && filtered.length === 0 && (
        <div className="py-20 text-center text-sm text-zinc-500">
          {filter ? `No skills match "${filter}".` : 'No skills installed.'}
        </div>
      )}
    </div>
  )
}