import { lazy, Suspense, useMemo, useState } from 'react'
import { Cycler } from './components/Cycler'
import { FootageCard } from './components/FootageCard'
import { Header } from './components/Header'
import { Loader } from './components/Loader'
import { useCameras, useFeed, useStream } from './lib/api'
import { FILTER_LABEL, matchesFilter, type Filter } from './lib/events'
import { useView } from './lib/view'

const Globe = lazy(() => import('./components/globe/Globe').then((m) => ({ default: m.Globe })))

/** Broadcast cycles the top few; the globe shows (and lets you pick) many more. */
const HERO_SLOTS = 4
const PIN_SLOTS = 50

export default function App() {
  const feed = useFeed()
  const stream = useStream()
  const [view, setView] = useView()
  const cameras = useCameras()
  const [filter, setFilter] = useState<Filter>('all')
  const [index, setIndex] = useState(0)
  const [hover, setHover] = useState(false)

  const filtered = useMemo(
    () => (feed.data ?? []).filter((f) => matchesFilter(f.event.type, filter)),
    [feed.data, filter],
  )
  const visible = filtered.slice(0, view === 'globe' ? PIN_SLOTS : HERO_SLOTS)
  const onFilter = (f: Filter) => {
    setFilter(f)
    setIndex(0)
  }
  const current = visible[Math.min(index, visible.length - 1)]

  const card = feed.isPending ? (
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
  const cycler = <Cycler count={visible.length} index={index} onChange={setIndex} paused={hover} />

  return (
    <div className="flex min-h-full flex-col">
      <Header
        filter={filter}
        onFilter={onFilter}
        liveCount={feed.data?.length ?? 0}
        stream={stream}
        view={view}
        onView={setView}
      />

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
        <main className="mx-auto grid w-full max-w-[1400px] flex-1 grid-cols-1 content-start gap-4 px-6 pt-12 pb-10 lg:grid-cols-[minmax(0,1fr)_minmax(320px,440px)] lg:content-center lg:items-center lg:gap-6 lg:pt-0">
          <div className="mx-auto w-full max-w-[min(80%,calc(100dvh-140px))] lg:max-w-[min(100%,calc(100dvh-140px))]">
            <Suspense fallback={<div className="aspect-square" />}>
              {cameras.data && (
                <Globe
                  cameras={cameras.data}
                  footage={visible}
                  selectedId={current?.event_id}
                  onSelect={(f) => setIndex(visible.findIndex((x) => x.event_id === f.event_id))}
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
        </main>
      )}
    </div>
  )
}
