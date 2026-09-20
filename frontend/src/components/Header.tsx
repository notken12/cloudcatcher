import { ChevronDown, Clapperboard, Earth, History, RefreshCw, Tv } from 'lucide-react'
import type { Refresh, StreamState } from '../lib/api'
import { FILTER_LABEL, FILTERS, type Filter } from '../lib/events'
import type { View } from '../lib/view'

interface Props {
  filter: Filter
  onFilter: (f: Filter) => void
  liveCount: number
  stream: StreamState
  refresh: Refresh
  view: View
  onView: (v: View) => void
  onHelp: () => void
}

export function Header({
  filter,
  onFilter,
  liveCount,
  stream,
  refresh,
  view,
  onView,
  onHelp,
}: Props) {
  const refreshTitle = refresh.running
    ? 'Refreshing footage…'
    : refresh.cooldown > 0
      ? `Refresh again in ${refresh.cooldown}s`
      : refresh.last?.error
        ? `Refresh failed: ${refresh.last.error}`
        : refresh.last
          ? `Refresh footage (last: ${refresh.last.with_footage ?? 0}/${refresh.last.resolved ?? 0} events with footage)`
          : 'Refresh footage now (R)'
  const dot =
    stream === 'open'
      ? 'bg-emerald-500 pulse'
      : stream === 'fixture'
        ? 'bg-neutral-400'
        : 'bg-amber-400'
  const label = stream === 'fixture' ? 'fixture' : stream === 'open' ? 'connected' : stream
  return (
    <header className="flex h-14 items-center justify-between gap-3 px-4 sm:px-6">
      <h1 className="text-[20px] font-semibold tracking-tight">skylight</h1>
      <div className="flex min-w-0 items-center gap-2 sm:gap-4">
        <div className="seg" role="group" aria-label="Layout">
          <button
            type="button"
            aria-pressed={view === 'broadcast'}
            onClick={() => onView('broadcast')}
            title="Broadcast"
          >
            <Tv className="h-4 w-4" aria-hidden />
            <span className="sr-only">Broadcast</span>
          </button>
          <button
            type="button"
            aria-pressed={view === 'globe'}
            onClick={() => onView('globe')}
            title="Globe"
          >
            <Earth className="h-4 w-4" aria-hidden />
            <span className="sr-only">Globe</span>
          </button>
          <button
            type="button"
            aria-pressed={view === 'time'}
            onClick={() => onView('time')}
            title="Time travel"
          >
            <History className="h-4 w-4" aria-hidden />
            <span className="sr-only">Time travel</span>
          </button>
          <button
            type="button"
            aria-pressed={view === 'show'}
            onClick={() => onView('show')}
            title="Story mode (full screen)"
          >
            <Clapperboard className="h-4 w-4" aria-hidden />
            <span className="sr-only">Story mode</span>
          </button>
        </div>
        <label className={`relative ${view === 'time' ? 'hidden' : ''}`}>
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
        <button
          type="button"
          className="muted inline-grid h-7 w-7 place-items-center rounded-full border border-[var(--line)] disabled:opacity-50"
          onClick={refresh.trigger}
          disabled={refresh.running || refresh.cooldown > 0}
          aria-busy={refresh.running}
          title={refreshTitle}
          aria-label="Refresh footage"
        >
          <RefreshCw className={`h-4 w-4 ${refresh.running ? 'animate-spin' : ''}`} aria-hidden />
        </button>
        <span
          className="muted inline-flex items-center gap-2 text-sm tabular-nums whitespace-nowrap"
          title={`${label} · ${liveCount}`}
        >
          <span className={`inline-block h-2 w-2 rounded-full ${dot}`} />
          <span className="hidden sm:inline">
            {label} · {liveCount}
          </span>
        </span>
        <button
          type="button"
          className="muted hidden h-7 w-7 rounded-full border border-[var(--line)] text-[13px] sm:inline-grid sm:place-items-center"
          onClick={onHelp}
          title="Keyboard shortcuts (?)"
          aria-label="Keyboard shortcuts"
        >
          ?
        </button>
      </div>
    </header>
  )
}
