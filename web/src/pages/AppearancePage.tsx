import { Check, Palette, RotateCcw, SlidersHorizontal, Type, Zap } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useUIStore, type Accent, type CodeFont, type ContentFont, type UIFont } from '@/stores/ui'

const presets: Array<{ value: Accent; label: string; colors: readonly [string, string] }> = [
  { value: 'ocean', label: 'Codex', colors: ['#237dd7', '#00edff'] },
  { value: 'violet', label: 'Catppuccin', colors: ['#8b5cf6', '#d8b4fe'] },
  { value: 'cobalt', label: 'GitHub', colors: ['#2563eb', '#60a5fa'] },
  { value: 'rose', label: 'Dracula', colors: ['#db2777', '#f9a8d4'] },
  { value: 'forest', label: 'Everforest', colors: ['#4d7c0f', '#bef264'] },
  { value: 'gold', label: 'Gruvbox', colors: ['#d97706', '#fcd34d'] },
  { value: 'ember', label: 'Ayu', colors: ['#f97316', '#facc15'] },
  { value: 'mono', label: 'Linear', colors: ['#a1a1aa', '#f4f4f5'] },
]

function Panel({ icon: Icon, title, detail, children }: { icon: typeof Palette; title: string; detail: string; children: React.ReactNode }) {
  return <section className="rounded-2xl border border-zinc-200 bg-white/70 p-5 shadow-sm dark:border-zinc-800 dark:bg-zinc-900/45">
    <div className="mb-5 flex items-start gap-3"><span className="mt-0.5 rounded-lg bg-brand-primary/10 p-2 text-brand-cyan"><Icon className="h-4 w-4" /></span><div><h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">{title}</h2><p className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400">{detail}</p></div></div>
    {children}
  </section>
}

function SelectRow<T extends string>({ label, detail, value, options, onChange }: { label: string; detail: string; value: T; options: Array<{ value: T; label: string }>; onChange: (value: T) => void }) {
  const id = `appearance-${label.toLowerCase().replaceAll(' ', '-')}`
  return <div className="flex flex-col gap-2 border-t border-zinc-200 py-3 first:border-t-0 first:pt-0 sm:flex-row sm:items-center sm:justify-between dark:border-zinc-800">
    <label htmlFor={id}><span className="block text-sm font-medium text-zinc-800 dark:text-zinc-200">{label}</span><span className="block text-xs text-zinc-500 dark:text-zinc-400">{detail}</span></label>
    <select id={id} value={value} onChange={(event) => onChange(event.target.value as T)} className="rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-800 outline-none focus:border-brand-primary dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100">
      {options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
    </select>
  </div>
}

export default function AppearancePage() {
  const { accent, setAccent, customAccent, setCustomAccent, uiFont, setUIFont, contentFont, setContentFont, codeFont, setCodeFont, sidebarTranslucent, setSidebarTranslucent, contrast, setContrast, motion, toggleMotion, resetAppearance } = useUIStore()

  return <div className="mx-auto w-full max-w-5xl px-5 py-8 sm:px-8">
    <header className="mb-7"><div className="mb-2 flex items-center gap-2 text-brand-cyan"><Palette className="h-5 w-5" /><span className="text-xs font-semibold uppercase tracking-[0.18em]">Workspace</span></div><h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">Appearance</h1><p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">Tune the workspace without changing your Dark, Light, or System mode—those stay in the sidebar settings.</p></header>

    <div className="grid gap-5 lg:grid-cols-2">
      <Panel icon={Palette} title="Theme palette" detail="Choose a preset or create your own accent.">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4" role="group" aria-label="Theme presets">
          {presets.map((preset) => <button key={preset.value} type="button" aria-label={`${preset.label} preset`} aria-pressed={accent === preset.value} onClick={() => setAccent(preset.value)} className={cn('relative rounded-xl border p-2.5 text-left transition-colors', accent === preset.value ? 'border-brand-primary bg-brand-primary/10' : 'border-zinc-200 hover:border-zinc-300 dark:border-zinc-800 dark:hover:border-zinc-700')}>
            <span className="mb-2 block h-7 rounded-lg" style={{ background: `linear-gradient(135deg, ${preset.colors[0]}, ${preset.colors[1]})` }} />
            <span className="text-xs font-medium text-zinc-700 dark:text-zinc-200">{preset.label}</span>{accent === preset.value && <Check className="absolute right-2 top-2 h-3.5 w-3.5 text-white" />}
          </button>)}
        </div>
        <div className="mt-4 flex items-center justify-between border-t border-zinc-200 pt-4 dark:border-zinc-800"><div><p className="text-sm font-medium text-zinc-800 dark:text-zinc-200">Custom accent</p><p className="text-xs text-zinc-500 dark:text-zinc-400">Applied immediately across controls and highlights.</p></div><label className="flex items-center gap-2 rounded-lg border border-zinc-300 bg-white px-2 py-1.5 text-xs font-mono text-zinc-700 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-200"><input aria-label="Custom accent color" type="color" value={customAccent} onChange={(event) => setCustomAccent(event.target.value)} className="h-6 w-6 cursor-pointer border-0 bg-transparent p-0" />{customAccent.toUpperCase()}</label></div>
      </Panel>

      <Panel icon={Type} title="Typography" detail="Use installed system fonts; no font download is required.">
        <SelectRow<UIFont> label="UI font" detail="Navigation, controls, and general interface text." value={uiFont} onChange={setUIFont} options={[{ value: 'inter', label: 'Inter' }, { value: 'system', label: 'System default' }, { value: 'serif', label: 'Serif' }, { value: 'mono', label: 'Monospace' }]} />
        <SelectRow<ContentFont> label="Content font" detail="Chat answers and rendered documents." value={contentFont} onChange={setContentFont} options={[{ value: 'ui', label: 'Same as UI font' }, { value: 'system', label: 'System default' }, { value: 'inter', label: 'Inter' }, { value: 'serif', label: 'Serif' }, { value: 'mono', label: 'Monospace' }]} />
        <SelectRow<CodeFont> label="Code font" detail="Code blocks and command output." value={codeFont} onChange={setCodeFont} options={[{ value: 'mono', label: 'Monospace' }, { value: 'system', label: 'System default' }, { value: 'serif', label: 'Serif' }]} />
      </Panel>

      <Panel icon={SlidersHorizontal} title="Interface" detail="Adjust the shell without affecting your saved chats.">
        <div className="flex items-center justify-between border-b border-zinc-200 py-3 dark:border-zinc-800"><div><p className="text-sm font-medium text-zinc-800 dark:text-zinc-200">Translucent sidebar</p><p className="text-xs text-zinc-500 dark:text-zinc-400">Let the ambient backdrop show through navigation.</p></div><button type="button" role="switch" aria-label="Translucent sidebar" aria-checked={sidebarTranslucent} onClick={() => setSidebarTranslucent(!sidebarTranslucent)} className={cn('relative h-7 w-12 rounded-full transition-colors', sidebarTranslucent ? 'bg-brand-primary' : 'bg-zinc-300 dark:bg-zinc-700')}><span className={cn('absolute top-1 h-5 w-5 rounded-full bg-white shadow transition-transform', sidebarTranslucent ? 'translate-x-6' : 'translate-x-1')} /></button></div>
        <div className="py-4"><div className="mb-3 flex items-center justify-between"><div><p className="text-sm font-medium text-zinc-800 dark:text-zinc-200">Contrast</p><p className="text-xs text-zinc-500 dark:text-zinc-400">Fine-tune visual separation.</p></div><output className="font-mono text-sm text-brand-cyan">{contrast}%</output></div><input aria-label="Contrast" type="range" min="85" max="115" value={contrast} onChange={(event) => setContrast(Number(event.target.value))} className="w-full accent-brand-primary" /></div>
        <div className="flex items-center justify-between border-t border-zinc-200 pt-3 dark:border-zinc-800"><div><p className="text-sm font-medium text-zinc-800 dark:text-zinc-200">Interface motion</p><p className="text-xs text-zinc-500 dark:text-zinc-400">Respect a calmer, reduced-motion workspace.</p></div><button type="button" aria-pressed={motion === 'reduced'} onClick={toggleMotion} className="rounded-lg border border-zinc-300 px-3 py-2 text-xs font-medium text-zinc-700 hover:border-brand-primary dark:border-zinc-700 dark:text-zinc-200">{motion === 'reduced' ? 'Reduced' : 'Full motion'}</button></div>
      </Panel>

      <Panel icon={Zap} title="Reset" detail="Restore appearance preferences while keeping your color mode."><button type="button" onClick={resetAppearance} className="flex items-center gap-2 rounded-lg border border-zinc-300 px-3 py-2 text-sm font-medium text-zinc-700 transition-colors hover:border-brand-primary hover:text-brand-cyan dark:border-zinc-700 dark:text-zinc-200"><RotateCcw className="h-4 w-4" />Reset appearance</button></Panel>
    </div>
  </div>
}
