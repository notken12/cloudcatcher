import { lazy, Suspense } from 'react'
import { EVENT_LABEL } from '../../lib/events'
import { formatCoord, relative } from '../../lib/time'
import type { Footage } from '../../lib/types'
import type { CameraPoint } from '../../lib/api'
import { FootageMedia } from '../media/FootageMedia'

const Globe = lazy(() => import('../globe/Globe').then((m) => ({ default: m.Globe })))

export const SHOW_PERIOD_MS = 8_000

interface Props {
  footage: Footage | undefined
  cameras: CameraPoint[] | undefined
  sun: { lat: number; lon: number }
  onExit: () => void
}

/**
 * "Tonight on Earth": one sight at a time, full-bleed with a slow drift, a caption that
 * rises in, and a small globe that flies to the place. Any key or click leaves.
 */
export function Show({ footage, cameras, sun, onExit }: Props) {
  const key = footage ? footage.event_id + footage.camera_id : 'none'
  return (
    <section
      className="show"
      onClick={(e) => {
        if (!e.isTrusted || (e.target as HTMLElement).closest('.globe')) return
        onExit()
      }}
      aria-label="Story mode — click or press Esc to exit"
    >
      {footage ? (
        <div key={key} className="show-media">
          <FootageMedia footage={footage} />
        </div>
      ) : (
        <div className="grid h-full place-items-center text-white/60">
          nothing verified right now
        </div>
      )}
      <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/70 via-black/10 to-transparent" />
      {footage && (
        <div key={`c${key}`} className="show-caption">
          <p className="mb-2 text-[13px] font-semibold tracking-[0.18em] text-white/70 uppercase">
            {EVENT_LABEL[footage.event.type]} · {relative(footage.frame_ts)}
          </p>
          <h2 className="max-w-[18ch] text-[clamp(28px,5vw,64px)] leading-[1.05] font-semibold tracking-tight">
            {footage.event.place ?? footage.camera.name}
          </h2>
          {footage.verdict && (
            <p className="mt-3 max-w-[60ch] text-[clamp(15px,1.6vw,22px)] text-white/85">
              “{footage.verdict.caption}”
            </p>
          )}
          <p className="mt-3 text-[13px] text-white/60">
            {footage.camera.name} · {footage.camera.attribution ?? footage.camera.source} ·{' '}
            {formatCoord(footage.camera.lat, footage.camera.lon)}
          </p>
        </div>
      )}
      {cameras && footage && (
        <div className="absolute right-[4vw] bottom-[6vh] hidden w-[min(22vw,240px)] md:block">
          <Suspense fallback={null}>
            <Globe
              cameras={cameras}
              pins={[
                {
                  id: footage.event_id,
                  lat: footage.camera.lat,
                  lon: footage.camera.lon,
                  color: `var(--c-${footage.event.type})`,
                  thumb: null,
                  label: '',
                },
              ]}
              selectedId={footage.event_id}
              onSelect={() => {}}
              sun={sun}
            />
          </Suspense>
        </div>
      )}
      {footage && <div key={`p${key}`} className="show-progress" />}
    </section>
  )
}
