import { ExternalLink, Film } from 'lucide-react'
import { useState } from 'react'
import { Timelapse } from './Timelapse'
import { type ArchiveCam, camHasYear, describeMoment, frameUrl } from '../../lib/archive'
import { LIVE } from '../../lib/api'
import { formatCoord } from '../../lib/time'
import type { Moment } from '../../lib/view'
import { Loader } from '../Loader'

/** One <img> with its own load state; remounted (keyed) per URL. */
function Frame({ url, alt }: { url: string; alt: string }) {
  const [state, setState] = useState<'loading' | 'ok' | 'missing'>('loading')
  return (
    <>
      <img
        src={url}
        alt={alt}
        className="h-full w-full object-cover"
        style={{ opacity: state === 'ok' ? 1 : 0, transition: 'opacity 0.3s' }}
        onLoad={() => setState('ok')}
        onError={() => setState('missing')}
      />
      {state !== 'ok' && (
        <div className="absolute inset-0">
          <Loader
            label={
              state === 'missing' ? 'no frame archived for this moment' : 'fetching archive frame…'
            }
          />
        </div>
      )}
    </>
  )
}

/** Big archive frame + where/when/whose. Same silhouette as FootageCard so the layout holds. */
export function ArchiveCard({ cam, moment }: { cam: ArchiveCam; moment: Moment }) {
  const url = camHasYear(cam, moment) ? frameUrl(cam, moment, 1280) : null
  const [lapse, setLapse] = useState(false)

  return (
    <article className="fade-in flex w-full flex-col gap-4" key={cam.id}>
      <div className="card relative aspect-video overflow-hidden bg-black/5">
        {url && lapse ? (
          <Timelapse key={`${cam.id}/${moment.date}`} cam={cam} moment={moment} />
        ) : url ? (
          <Frame key={url} url={url} alt={`${cam.name} at ${describeMoment(moment)}`} />
        ) : (
          <Loader
            label={
              camHasYear(cam, moment)
                ? 'needs the camera backend (VITE_API_BASE)'
                : `archive starts in ${cam.since}`
            }
          />
        )}
      </div>

      <div className="flex flex-col gap-1 px-1">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <h2 className="text-[17px] font-semibold">{cam.name}</h2>
          <div className="muted text-sm tabular-nums">{describeMoment(moment)}</div>
        </div>
        <p className="muted text-sm">
          Archived sky at this camera's local time · {formatCoord(cam.lat, cam.lon)}
          {LIVE ? '' : ' · loaded directly from the source'}
        </p>
        <div className="muted mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px]">
          <a
            href={cam.page}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1"
          >
            {cam.source}
            <ExternalLink className="h-3 w-3" />
          </a>
          <span>
            every {cam.step_min} min since {cam.since}
          </span>
          {url && (
            <button
              type="button"
              className="btn ml-auto"
              aria-pressed={lapse}
              onClick={() => setLapse((v) => !v)}
              title="Play this whole day at this camera"
            >
              <Film className="h-3.5 w-3.5" /> 24 h
            </button>
          )}
        </div>
      </div>
    </article>
  )
}
