import { useEffect, useRef, useState } from 'react'
import { mediaUrl } from '../../lib/api'
import type { Media } from '../../lib/types'

/** hls.js on a <video>; Safari plays HLS natively. Poster = the verified still. */
export function HlsMedia({ media }: { media: Media }) {
  const ref = useRef<HTMLVideoElement>(null)
  const [failed, setFailed] = useState(false)
  const src = mediaUrl(media.src)
  const poster = media.poster ? mediaUrl(media.poster) : undefined

  useEffect(() => {
    const video = ref.current
    if (!video) return
    setFailed(false)
    if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = src
      return
    }
    let hls: import('hls.js').default | undefined
    let cancelled = false
    void import('hls.js').then(({ default: Hls }) => {
      if (cancelled || !Hls.isSupported()) return
      hls = new Hls({ lowLatencyMode: true })
      hls.on(Hls.Events.ERROR, (_e, data) => {
        if (data.fatal) setFailed(true)
      })
      hls.loadSource(src)
      hls.attachMedia(video)
    })
    return () => {
      cancelled = true
      hls?.destroy()
    }
  }, [src])

  if (failed && poster) {
    return <img src={poster} alt="" className="h-full w-full object-cover" />
  }
  return (
    <video
      ref={ref}
      poster={poster}
      autoPlay
      muted
      playsInline
      controls={false}
      className="h-full w-full object-cover"
    />
  )
}
