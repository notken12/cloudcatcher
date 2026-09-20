import { lazy, Suspense, useMemo, useState } from 'react'
import { Cycler } from './components/Cycler'
import { FootageCard } from './components/FootageCard'
import { Header } from './components/Header'
import { JoinCard } from './components/JoinCard'
import { Loader } from './components/Loader'
import type { Pin } from './components/globe/Globe'
import { ArchiveCard } from './components/time/ArchiveCard'
import { TimeControls } from './components/time/TimeControls'
import { LIVE, mediaUrl, useCameras, useFeed, useStream } from './lib/api'
import { ARCHIVE_CAMS, availableCams, camHasYear, describeMoment, frameUrl } from './lib/archive'
import { EVENT_LABEL, FILTER_LABEL, matchesFilter, type Filter } from './lib/events'
import { useUser } from './lib/user'
import { dwellMs, prioritize } from './lib/prioritize'
import type { EventType } from './lib/types'
import { useMoment, useView, type View } from './lib/view'

const Globe = lazy(() => import('./components/globe/Globe').then((m) => ({ default: m.Globe })))

/** Broadcast cycles the top few; the globe shows (and lets you pick) many more. */
const HERO_SLOTS = 4
const PIN_SLOTS = 50
const NO_LIKES: EventType[] = []

export default function App() {
  const feed = useFeed()
  const stream = useStream()
  const [view, setView] = useView()
  const [moment, setMoment] = useMoment()
  const cameras = useCameras()
  const user = useUser()
  const [filter, setFilter] = useState<Filter>('all')
  const [index, setIndex] = useState(0)
  const [hover, setHover] = useState(false)

  const likes = user?.likes ?? NO_LIKES
  const filtered = useMemo(
    () =>
      prioritize(
        (feed.data ?? []).filter((f) => matchesFilter(f.event.type, filter)),
        likes,
      ),
    [feed.data, filter, likes],
  )
  const visible = filtered.slice(0, view === 'globe' ? PIN_SLOTS : HERO_SLOTS)
  const onFilter = (f: Filter) => {
    setFilter(f)
    setIndex(0)
  }
  const onView = (v: View) => {
    setView(v)
    if ((v === 'time') !== (view === 'time')) setIndex(0)
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
            label: `${EVENT_LABEL[f.event.type]} · ${f.event.place ?? f.camera.name}`,
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
      <div className="card aspect-video">
        <Loader label="looking at the sky…" />
      </div>
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
      count={view === 'time' ? archiveCams.length : visible.length}
      index={index}
      onChange={setIndex}
      paused={hover}
      periodMs={view === 'time' ? 8_000 : dwellMs(current, likes, 20_000)}
    />
  )

  return (
    <div className="flex min-h-full flex-col">
      <Header
        filter={filter}
        onFilter={onFilter}
        liveCount={feed.data?.length ?? 0}
        stream={stream}
        view={view}
        onView={onView}
        userId={user?.id}
      />

      {view === 'join' ? (
        <main className="mx-auto flex w-full max-w-[1100px] flex-1 flex-col justify-center px-6 pb-10">
          <JoinCard />
        </main>
      ) : view === 'broadcast' ? (
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
