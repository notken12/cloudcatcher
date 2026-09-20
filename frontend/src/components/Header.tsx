import { ChevronDown } from 'lucide-react'
import type { StreamState } from '../lib/api'
import { FILTER_LABEL, FILTERS, type Filter } from '../lib/events'

interface Props {
  filter: Filter
  onFilter: (f: Filter) => void
  liveCount: number
  stream: StreamState
}

export function Header({ filter, onFilter, liveCount, stream }: Props) {
  const dot =
    stream === 'open'
      ? 'bg-emerald-500 pulse'
      : stream === 'fixture'
        ? 'bg-neutral-400'
        : 'bg-amber-400'
  const label = stream === 'fixture' ? 'fixture' : stream === 'open' ? 'live' : stream
  return (
    <header className="flex h-14 items-center justify-between px-6">
      <h1 className="text-[20px] font-semibold tracking-tight">sunroof</h1>
      <div className="flex items-center gap-4">
        <label className="relative">
          <select
            className="plain"
            value={filter}
            onChange={(e) => onFilter(e.target.value as Filter)}
            aria-label="Filter by sight"
          >
            {FILTERS.map((f) => (
              <option key={f} value={f}>
                {FILTER_LABEL[f]}
              </option>
            ))}
          </select>
          <ChevronDown className="muted pointer-events-none absolute top-1/2 right-3 h-4 w-4 -translate-y-1/2" />
        </label>
        <span className="muted inline-flex items-center gap-2 text-sm tabular-nums">
          <span className={`inline-block h-2 w-2 rounded-full ${dot}`} />
          {label} · {liveCount}
        </span>
      </div>
    </header>
  )
}
