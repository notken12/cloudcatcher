import type { Footage } from '../../lib/types'
import { HlsMedia } from './HlsMedia'
import { IframeMedia } from './IframeMedia'
import { ImageMedia } from './ImageMedia'

/** The only place that branches on media.kind. */
export function FootageMedia({ footage }: { footage: Footage }) {
  const title = `${footage.camera.name} — ${footage.verdict?.caption ?? footage.event.type}`
  switch (footage.media.kind) {
    case 'image':
      return <ImageMedia media={footage.media} alt={title} />
    case 'hls':
      return <HlsMedia media={footage.media} />
    case 'iframe':
      return <IframeMedia media={footage.media} title={title} />
  }
}
