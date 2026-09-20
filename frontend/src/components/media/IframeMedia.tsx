import { useState } from 'react'
import { Play } from 'lucide-react'
import { mediaUrl } from '../../lib/api'
import type { Media } from '../../lib/types'

/**
 * YouTube / Panomax / Roundshot embeds (ToS-required for those sources).
 * Shows the verified poster first; the iframe loads on click so a slow
 * third-party player never blanks the hero.
 */
export function IframeMedia({ media, title }: { media: Media; title: string }) {
  const [open, setOpen] = useState(!media.poster)
  const poster = media.poster ? mediaUrl(media.poster) : undefined

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="group relative h-full w-full cursor-pointer"
        aria-label={`Open ${title}`}
      >
        <img src={poster} alt={title} className="h-full w-full object-cover" />
        <span className="absolute inset-0 grid place-items-center bg-black/10 transition group-hover:bg-black/20">
          <span className="grid h-16 w-16 place-items-center rounded-full bg-white/90 shadow">
            <Play className="ml-1 h-7 w-7" fill="currentColor" />
          </span>
        </span>
      </button>
    )
  }
  return (
    <iframe
      src={media.src}
      title={title}
      className="h-full w-full border-0"
      allow="autoplay; fullscreen"
      loading="lazy"
    />
  )
}
