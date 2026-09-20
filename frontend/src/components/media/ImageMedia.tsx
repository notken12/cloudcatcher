import { useEffect, useState } from 'react'
import { mediaUrl } from '../../lib/api'
import type { Media } from '../../lib/types'

function bust(src: string, t: number): string {
  const u = src.includes('?') ? `${src}&t=${t}` : `${src}?t=${t}`
  return mediaUrl(u)
}

/**
 * Still frame re-requested every `refresh_s`. The previous frame stays on
 * screen until the new one has fully loaded (no flash, no broken image).
 * Keyed on `media.src` so a new source resets the state instead of syncing it.
 */
export function ImageMedia(props: { media: Media; alt: string }) {
  return <Frame key={props.media.src} {...props} />
}

function Frame({ media, alt }: { media: Media; alt: string }) {
  const [shown, setShown] = useState(() => bust(media.src, Date.now()))

  useEffect(() => {
    const period = (media.refresh_s ?? 300) * 1000
    const id = setInterval(() => {
      const next = bust(media.src, Date.now())
      const img = new Image()
      img.onload = () => setShown(next)
      img.src = next
    }, period)
    return () => clearInterval(id)
  }, [media.src, media.refresh_s])

  return <img key={shown} src={shown} alt={alt} className="fade-in h-full w-full object-cover" />
}
