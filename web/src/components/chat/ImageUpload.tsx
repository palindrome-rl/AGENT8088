import { useState, useCallback } from 'react'
import { Upload, X, ImagePlus } from 'lucide-react'
import { useWebSocket } from '@/hooks/useWebSocket'
import { cn } from '@/lib/utils'

interface ImageUploadProps {
  onClose?: () => void
  initialImage?: string | null
}

export function ImageUpload({ onClose, initialImage }: ImageUploadProps) {
  const [image, setImage] = useState<string | null>(initialImage ?? null)
  const [question, setQuestion] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const [fromClipboard, setFromClipboard] = useState(!!initialImage)
  const { send } = useWebSocket()

  const handleFile = useCallback((file: File, viaClipboard = false) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = reader.result as string
      setImage(result)
      setFromClipboard(viaClipboard)
    }
    reader.readAsDataURL(file)
  }, [])

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const file = e.dataTransfer.files[0]
    if (file && file.type.startsWith('image/')) handleFile(file, false)
  }

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const items = e.clipboardData.items
    for (const item of items) {
      if (item.type.startsWith('image/')) {
        const file = item.getAsFile()
        if (file) handleFile(file, true)
        break
      }
    }
  }, [handleFile])

  const handleSend = () => {
    if (!image || !question) return
    /* Use /paste for clipboard-sourced images, /image for file uploads.
       The backend reads the system clipboard for /paste and processes
       uploaded image data for /image. Falls back to chat if backend
       doesn't support the command. */
    if (fromClipboard) {
      send({ type: 'command', command: 'paste', args: question })
    } else {
      send({ type: 'command', command: 'image', args: question })
    }
    setImage(null)
    setQuestion('')
    setFromClipboard(false)
    onClose?.()
  }

  return (
    <div
      className="mb-2 rounded-[14px] border border-zinc-300 dark:border-zinc-800 bg-white dark:bg-zinc-900/50 p-3"
      onPaste={handlePaste}
    >
      {/* Header */}
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <ImagePlus className="h-3.5 w-3.5 text-brand-cyan" />
          <span className="text-[11px] font-medium text-zinc-500 dark:text-zinc-400">Image analysis</span>
        </div>
        {onClose && (
          <button
            type="button"
            aria-label="Close image upload"
            onClick={onClose}
            className="flex h-5 w-5 items-center justify-center rounded text-zinc-400 transition-colors hover:bg-zinc-100 dark:hover:bg-zinc-800 hover:text-zinc-700 dark:hover:text-zinc-200"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {image ? (
        <div>
          <div className="relative inline-block">
            <img src={image} alt="preview" className="max-h-32 rounded-lg border border-zinc-300 dark:border-zinc-700" />
            <button
              onClick={() => { setImage(null); setFromClipboard(false) }}
              className="absolute -right-2 -top-2 rounded-full bg-zinc-200 dark:bg-zinc-800 p-1 text-zinc-500 dark:text-zinc-400 hover:text-red-400"
            >
              <X className="h-3 w-3" />
            </button>
          </div>
          <div className="mt-2 flex gap-2">
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Ask a question about this image..."
              className="flex-1 rounded-lg border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100 placeholder:text-zinc-400 dark:placeholder:text-zinc-600 focus:border-brand-primary focus:outline-none"
              onKeyDown={(e) => e.key === 'Enter' && handleSend()}
            />
            <button
              onClick={handleSend}
              className="rounded-lg bg-brand-primary/20 px-3 py-2 text-sm text-brand-cyan hover:bg-brand-primary/30"
            >
              Send
            </button>
          </div>
        </div>
      ) : (
        <label
          onDrop={handleDrop}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
          onDragLeave={() => setDragOver(false)}
          className={cn(
            'flex cursor-pointer items-center gap-2 rounded-lg border border-dashed py-3 text-sm transition-colors',
            dragOver
              ? 'border-brand-cyan bg-brand-primary/5 text-brand-cyan'
              : 'border-zinc-300 dark:border-zinc-700 text-zinc-500 hover:border-zinc-400 dark:hover:border-zinc-600',
          )}
        >
          <Upload className="h-4 w-4" />
          <span>Drop image, paste from clipboard, or click to upload</span>
          <input
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f, false) }}
          />
        </label>
      )}
    </div>
  )
}