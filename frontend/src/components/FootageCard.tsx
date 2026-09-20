import { ExternalLink, ShieldCheck } from 'lucide-react'
import { useEffect, useState } from 'react'
import { EVENT_BLURB, EVENT_LABEL, eventColor } from '../lib/events'
import { delayHint, formatCoord, relative, utcClock } from '../lib/time'
import type { Footage } from '../lib/types'
import { FootageMedia } from './media/FootageMedia'

function useNow(everyMs = 15_000): number {
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), everyMs)
    return () => clearInterval(id)
  }, [everyMs])
  return now
}

/** Hero: media + the honest caption row + attribution. Takes one Footage; the globe reuses it. */
export function FootageCard({ footage }: { footage: Footage }) {
  const now = useNow()
  const { event, camera, media, verdict } = footage
  const isStream = media.kind !== 'image'
  const ratio = media.width && media.height ? media.width / media.height : 16 / 9
  const place = event.place ?? formatCoord(event.lat, event.lon)

  return (
    <article
      className="fade-in flex w-full flex-col gap-4"
      key={footage.event_id + footage.camera_id}
    >
      <div
        className="card overflow-hidden bg-black/5"
        style={{ aspectRatio: Math.max(1, Math.min(ratio, 2.4)) }}
      >
        <FootageMedia footage={footage} />
      </div>

      <div className="flex flex-col gap-1 px-1">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <div className="flex items-center gap-3">
            <span className="chip" style={{ background: eventColor(event.type) }}>
              {EVENT_LABEL[event.type]}
            </span>
            <h2 className="text-[17px] font-semibold">{place}</h2>
          </div>
          <div className="muted text-sm tabular-nums">
            {isStream ? (
              <>stream · {delayHint(footage.frame_ts, now)}</>
            ) : (
              <>
                frame {utcClock(footage.frame_ts)} · {relative(footage.frame_ts, now)}
              </>
            )}
          </div>
        </div>

        <p className="muted text-sm">{EVENT_BLURB[event.type]}</p>

        <div className="muted mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px]">
          {camera.page_url ? (
            <a
              href={camera.page_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1"
            >
              {camera.name} · {camera.attribution ?? camera.source}
              <ExternalLink className="h-3 w-3" />
            </a>
          ) : (
            <span>
              {camera.name} · {camera.attribution ?? camera.source}
            </span>
          )}
          {footage.verified && (
            <span
              className="inline-flex items-center gap-1"
              title={`VLM confidence ${Math.round((verdict?.confidence ?? 0) * 100)}%`}
            >
              <ShieldCheck className="h-3.5 w-3.5" /> verified
            </span>
          )}
          <span title={footage.why} className="truncate">
            {footage.why}
          </span>
        </div>
      </div>
    </article>
  )
}
