import { Pause, Play } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { type ArchiveCam, lapseFrames } from '../../lib/archive'
import type { Moment } from '../../lib/view'

const FPS = 6
const pad = (n: number) => String(n).padStart(2, '0')

/**
 * One camera's whole day: preloads every frame (missing ones drop out), then flips through
 * the loaded ones at FPS. `<img>` swaps only — no video decoding, so it runs anywhere.
 */
export function Timelapse({ cam, moment }: { cam: ArchiveCam; moment: Moment }) {
  const frames = useMemo(() => lapseFrames(cam, moment.date), [cam, moment.date])
  const [loaded, setLoaded] = useState<ReadonlySet<string>>(() => new Set())
  const [failed, setFailed] = useState(0)
  const [i, setI] = useState(() => {
    const k = frames.findIndex((f) => f.minutes >= moment.minutes)
    return k < 0 ? 0 : k
  })
  const [playing, setPlaying] = useState(true)
  const imgs = useRef<HTMLImageElement[]>([])

  useEffect(() => {
    imgs.current = frames.map((f) => {
      const im = new Image()
      im.onload = () => setLoaded((s) => new Set(s).add(f.url))
      im.onerror = () => setFailed((n) => n + 1)
      im.src = f.url
      return im
    })
    return () => {
      for (const im of imgs.current) im.onload = im.onerror = null
    }
  }, [frames])

  const ready = useMemo(() => frames.filter((f) => loaded.has(f.url)), [frames, loaded])

  useEffect(() => {
    if (!playing || ready.length < 2) return
    const id = setInterval(() => {
      setI((k) => {
        // advance to the next *loaded* frame after k, wrapping
        const cur = frames[k]?.minutes ?? -1
        const next = ready.find((f) => f.minutes > cur) ?? ready[0]
        return frames.indexOf(next)
      })
    }, 1000 / FPS)
    return () => clearInterval(id)
  }, [playing, ready, frames])

  const cur = frames[i]
  const showing = cur && loaded.has(cur.url) ? cur : ready[0]
  const done = loaded.size + failed >= frames.length

  return (
    <div className="absolute inset-0">
      {showing ? (
        <img src={showing.url} alt="" className="h-full w-full object-cover" />
      ) : (
        <div className="skeleton h-full w-full rounded-none" />
      )}
      <div className="absolute inset-x-0 bottom-0 flex items-center gap-3 bg-gradient-to-t from-black/60 to-transparent px-4 pt-8 pb-3 text-white">
        <button
          type="button"
          className="grid h-8 w-8 place-items-center rounded-full bg-white/20 backdrop-blur hover:bg-white/30"
          onClick={() => setPlaying((p) => !p)}
          aria-label={playing ? 'Pause time-lapse' : 'Play time-lapse'}
        >
          {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
        </button>
        <input
          type="range"
          min={0}
          max={frames.length - 1}
          value={i}
          onChange={(e) => {
            setPlaying(false)
            setI(Number(e.target.value))
          }}
          aria-label="Time of day"
          className="flex-1 accent-white"
        />
        <span className="w-12 text-right text-sm tabular-nums">
          {showing ? `${pad(Math.floor(showing.minutes / 60))}:${pad(showing.minutes % 60)}` : '—'}
        </span>
        <span className="w-16 text-right text-xs tabular-nums opacity-70">
          {done ? `${ready.length} frames` : `${loaded.size}/${frames.length}`}
        </span>
      </div>
    </div>
  )
}
