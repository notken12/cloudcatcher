import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { Cycler } from './components/Cycler'
import { FootageCard } from './components/FootageCard'
import { Header } from './components/Header'
import { Help } from './components/Help'
import { Loader } from './components/Loader'
import { SkeletonCard } from './components/Skeleton'
import type { Pin } from './components/globe/Globe'
import { Show, SHOW_PERIOD_MS } from './components/show/Show'
import { ArchiveCard } from './components/time/ArchiveCard'
import { TimeControls } from './components/time/TimeControls'
import { LIVE, mediaUrl, useCameras, useFeed, useRefresh, useStream } from './lib/api'
import { ARCHIVE_CAMS, availableCams, camHasYear, describeMoment, frameUrl } from './lib/archive'
import { EVENT_LABEL, FILTER_LABEL, matchesFilter, type Filter } from './lib/events'
import { duskiness, subsolar } from './lib/sun'
import { relative } from './lib/time'
import { stepMoment, useMoment, useView, type View } from './lib/view'

const Globe = lazy(() => import('./components/globe/Globe').then((m) => ({ default: m.Globe })))

/** Broadcast cycles the top few; the globe and the show use many more. */
const HERO_SLOTS = 4
const PIN_SLOTS = 50
/** Sunset ring: the local clock jumps this far every PLAY_MS. */
const PLAY_STEP_MIN = 30
const PLAY_MS = 2_000
/** Story mode starts itself on the broadcast layout after this long without input (booth). */
const IDLE_MS = 90_000
const VIEW_KEYS: Record<string, View> = { 1: 'broadcast', 2: 'globe', 3: 'time', 4: 'show' }

/** Subsolar point, refreshed every minute (the terminator moves 0.25° in that time). */
function useSun() {
  const [sun, setSun] = useState(() => subsolar())
  useEffect(() => {
    const id = setInterval(() => setSun(subsolar()), 60_000)
    return () => clearInterval(id)
  }, [])
  return sun
}

export default function App() {
  const feed = useFeed()
  const stream = useStream()
  const refresh = useRefresh()
  const [view, setView] = useView()
  const [moment, setMoment] = useMoment()
  const cameras = useCameras()
  const [filter, setFilter] = useState<Filter>('all')
  const [index, setIndex] = useState(0)
  const [hover, setHover] = useState(false)
  const [playing, setPlaying] = useState(false)
  const [help, setHelp] = useState(false)
  const sun = useSun()

  const filtered = useMemo(
    () => (feed.data ?? []).filter((f) => matchesFilter(f.event.type, filter)),
    [feed.data, filter],
  )
  const visible = filtered.slice(0, view === 'broadcast' ? HERO_SLOTS : PIN_SLOTS)
  const onFilter = (f: Filter) => {
    setFilter(f)
    setIndex(0)
  }
  const onView = (v: View) => {
    setView(v)
    if ((v === 'time') !== (view === 'time')) setIndex(0)
    if (v !== 'time') setPlaying(false)
  }
  const current = visible[Math.min(index, visible.length - 1)]

  const archiveCams = useMemo(() => availableCams(), [])
  const archiveCam = archiveCams[Math.min(index, archiveCams.length - 1)]
  const pins = useMemo<Pin[]>(
    () =>
      view === 'time'
        ? archiveCams.map((c) => ({
            id: c.id,
            lat: c.lat,
            lon: c.lon,
            color: 'var(--c-archive)',
            thumb: camHasYear(c, moment) ? frameUrl(c, moment, 240) : null,
            label: `${c.name} · ${describeMoment(moment)}`,
          }))
        : visible.map((f) => ({
            id: f.event_id,
            lat: f.camera.lat,
            lon: f.camera.lon,
            color: `var(--c-${f.event.type})`,
            thumb: mediaUrl(f.media.poster ?? f.media.src),
            label: `${EVENT_LABEL[f.event.type]} · ${f.event.place ?? f.camera.name} · ${relative(f.frame_ts)}`,
          })),
    [view, archiveCams, moment, visible],
  )
  const selectedId = view === 'time' ? archiveCam?.id : current?.event_id
  const onSelectPin = (id: string) =>
    setIndex(
      Math.max(
        0,
        pins.findIndex((p) => p.id === id),
      ),
    )
  const count = view === 'time' ? archiveCams.length : visible.length

  // Sunset ring: advance the shared local clock; every pin refetches its frame.
  useEffect(() => {
    if (!playing || view !== 'time') return
    const id = setInterval(() => setMoment(stepMoment(moment, PLAY_STEP_MIN)), PLAY_MS)
    return () => clearInterval(id)
  }, [playing, view, moment, setMoment])

  // Warm the browser cache with the neighbouring slider steps once this moment has landed.
  useEffect(() => {
    if (view !== 'time') return
    const id = setTimeout(() => {
      for (const step of [PLAY_STEP_MIN, -PLAY_STEP_MIN]) {
        const m = stepMoment(moment, step)
        for (const c of archiveCams) {
          const url = camHasYear(c, m) && frameUrl(c, m, 240)
          if (url) new Image().src = url
        }
      }
    }, 1_200)
    return () => clearTimeout(id)
  }, [view, moment, archiveCams])

  // Story mode advances itself; any key or click leaves.
  useEffect(() => {
    if (view !== 'show' || count < 2) return
    const id = setInterval(() => setIndex((i) => (i + 1) % count), SHOW_PERIOD_MS)
    return () => clearInterval(id)
  }, [view, count])

  // Booth: idle on the broadcast layout long enough and the show starts.
  useEffect(() => {
    if (view !== 'broadcast') return
    let id = setTimeout(() => setView('show'), IDLE_MS)
    const reset = () => {
      clearTimeout(id)
      id = setTimeout(() => setView('show'), IDLE_MS)
    }
    const events = ['pointermove', 'pointerdown', 'keydown', 'wheel'] as const
    for (const e of events) window.addEventListener(e, reset, { passive: true })
    return () => {
      clearTimeout(id)
      for (const e of events) window.removeEventListener(e, reset)
    }
  }, [view, setView])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      if (t && (t.tagName === 'INPUT' || t.tagName === 'SELECT' || t.tagName === 'TEXTAREA')) return
      if (view === 'show') {
        onView('broadcast')
        return
      }
      if (e.key === 'ArrowRight') setIndex((i) => (count ? (i + 1) % count : 0))
      else if (e.key === 'ArrowLeft') setIndex((i) => (count ? (i - 1 + count) % count : 0))
      else if (e.key === ' ' && view === 'time') setPlaying((p) => !p)
      else if (e.key === '?') setHelp((h) => !h)
      else if (e.key === 'Escape') setHelp(false)
      else if (e.key === 'r' || e.key === 'R') refresh.trigger()
      else if (e.key in VIEW_KEYS) onView(VIEW_KEYS[e.key])
      else return
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  if (view === 'show') {
    return (
      <Show footage={current} cameras={cameras.data} sun={sun} onExit={() => onView('broadcast')} />
    )
  }

  const card =
    view === 'time' ? (
      archiveCam ? (
        <ArchiveCard cam={archiveCam} moment={moment} />
      ) : (
        <div className="card aspect-video">
          <Loader label="no archive cameras" />
        </div>
      )
    ) : feed.isPending ? (
      <SkeletonCard />
    ) : feed.isError ? (
      <div className="card aspect-video">
        <Loader label="feed unavailable — retrying" />
      </div>
    ) : !current ? (
      <div className="card aspect-video">
        <Loader label={`nothing verified for ${FILTER_LABEL[filter].toLowerCase()} right now`} />
      </div>
    ) : (
      <FootageCard footage={current} />
    )
  const cycler = (
    <Cycler
      count={count}
      index={index}
      onChange={setIndex}
      paused={hover || playing}
      periodMs={view === 'time' ? 8_000 : undefined}
    />
  )

  return (
    <div className="flex min-h-full flex-col">
      <Header
        filter={filter}
        onFilter={onFilter}
        liveCount={feed.data?.length ?? 0}
        stream={stream}
        refresh={refresh}
        view={view}
        onView={onView}
        onHelp={() => setHelp(true)}
      />
      {help && <Help onClose={() => setHelp(false)} />}

      {view === 'broadcast' ? (
        <main
          className="mx-auto flex w-full max-w-[1100px] flex-1 flex-col justify-center gap-2 px-6 pb-10"
          onMouseEnter={() => setHover(true)}
          onMouseLeave={() => setHover(false)}
        >
          {card}
          {cycler}
        </main>
      ) : (
        <main className="mx-auto flex w-full max-w-[1400px] flex-1 flex-col gap-4 px-6 pt-6 pb-10 lg:justify-center lg:pt-0">
          {view === 'time' && (
            <TimeControls
              moment={moment}
              onChange={setMoment}
              playing={playing}
              onPlaying={setPlaying}
              status={
                LIVE
                  ? `${archiveCams.length} archive cameras`
                  : `${archiveCams.length} of ${ARCHIVE_CAMS.length} cameras without backend`
              }
            />
          )}
          <div className="grid grid-cols-1 content-start gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(320px,440px)] lg:items-center lg:gap-6">
            <div className="mx-auto w-full max-w-[min(80%,calc(100dvh-140px))] lg:max-w-[min(100%,calc(100dvh-220px))]">
              <Suspense fallback={<div className="aspect-square" />}>
                {cameras.data && (
                  <Globe
                    cameras={cameras.data}
                    pins={pins}
                    selectedId={selectedId}
                    onSelect={onSelectPin}
                    sun={view === 'time' ? undefined : sun}
                    dusk={view === 'time' ? duskiness(moment.minutes) : 0}
                  />
                )}
              </Suspense>
            </div>
            <div
              className="flex flex-col gap-2"
              onMouseEnter={() => setHover(true)}
              onMouseLeave={() => setHover(false)}
            >
              {card}
              {cycler}
            </div>
          </div>
        </main>
      )}
    </div>
  )
}
