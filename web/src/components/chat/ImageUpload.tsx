import { useState, useCallback } from 'react'
import { Upload, X } from 'lucide-react'
import { useWebSocket } from '@/hooks/useWebSocket'
import { cn } from '@/lib/utils'

export function ImageUpload() {
  const [image, setImage] = useState<string | null>(null)
  const [question, setQuestion] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const { send } = useWebSocket()

  const handleFile = useCallback((file: File) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = reader.result as string
      setImage(result)
    }
    reader.readAsDataURL(file)
  }, [])

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const file = e.dataTransfer.files[0]
    if (file && file.type.startsWith('image/')) handleFile(file)
  }

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const items = e.clipboardData.items
    for (const item of items) {
      if (item.type.startsWith('image/')) {
        const file = item.getAsFile()
        if (file) handleFile(file)
        break
      }
    }
  }, [handleFile])

  const handleSend = () => {
    if (!image || !question) return
    // Send as a chat message with image context
    send({ type: 'chat', text: `[image uploaded] ${question}` })
    setImage(null)
    setQuestion('')
  }

  return (
    <div className="border-t border-zinc-800 bg-zinc-950 p-3"
         onPaste={handlePaste}>
      {image ? (
        <div className="mb-2">
          <div className="relative inline-block">
            <img src={image} alt="preview" className="max-h-32 rounded-lg border border-zinc-700" />
            <button
              onClick={() => setImage(null)}
              className="absolute -right-2 -top-2 rounded-full bg-zinc-800 p-1 text-zinc-400 hover:text-red-400"
            >
              <X className="h-3 w-3" />
            </button>
          </div>
          <div className="mt-2 flex gap-2">
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Ask a question about this image..."
              className="flex-1 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-brand-primary focus:outline-none"
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
            dragOver ? 'border-brand-cyan bg-brand-primary/5 text-brand-cyan' : 'border-zinc-700 text-zinc-500 hover:border-zinc-600',
          )}
        >
          <Upload className="h-4 w-4" />
          <span>Drop image, paste from clipboard, or click to upload</span>
          <input
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f) }}
          />
        </label>
      )}
    </div>
  )
}