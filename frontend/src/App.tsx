import { useMemo, useState } from 'react'
import { Cycler } from './components/Cycler'
import { FootageCard } from './components/FootageCard'
import { Header } from './components/Header'
import { Loader } from './components/Loader'
import { useFeed, useStream } from './lib/api'
import { FILTER_LABEL, matchesFilter, type Filter } from './lib/events'

const HERO_SLOTS = 4

export default function App() {
  const feed = useFeed()
  const stream = useStream()
  const [filter, setFilter] = useState<Filter>('all')
  const [index, setIndex] = useState(0)
  const [hover, setHover] = useState(false)

  const visible = useMemo(
    () => (feed.data ?? []).filter((f) => matchesFilter(f.event.type, filter)).slice(0, HERO_SLOTS),
    [feed.data, filter],
  )
  const onFilter = (f: Filter) => {
    setFilter(f)
    setIndex(0)
  }
  const current = visible[Math.min(index, visible.length - 1)]

  return (
    <div className="flex min-h-full flex-col">
      <Header
        filter={filter}
        onFilter={onFilter}
        liveCount={feed.data?.length ?? 0}
        stream={stream}
      />

      <main
        className="mx-auto flex w-full max-w-[1100px] flex-1 flex-col justify-center gap-2 px-6 pb-10"
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
      >
        {feed.isPending ? (
          <div className="card aspect-video">
            <Loader label="looking at the sky…" />
          </div>
        ) : feed.isError ? (
          <div className="card aspect-video">
            <Loader label="feed unavailable — retrying" />
          </div>
        ) : !current ? (
          <div className="card aspect-video">
            <Loader
              label={`nothing verified for ${FILTER_LABEL[filter].toLowerCase()} right now`}
            />
          </div>
        ) : (
          <FootageCard footage={current} />
        )}
        <Cycler count={visible.length} index={index} onChange={setIndex} paused={hover} />
      </main>
    </div>
  )
}
