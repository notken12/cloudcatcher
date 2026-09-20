import { useEffect, useState } from 'react'
import { ARCHIVE_FIRST_YEAR, type Moment } from '../../lib/view'

interface Props {
  moment: Moment
  onChange: (m: Moment) => void
  /** e.g. "8 cameras" */
  status: string
}

const pad = (n: number) => String(n).padStart(2, '0')

function clampDate(date: string, year: number, today: string): string {
  const next = `${year}${date.slice(4)}`
  return next > today ? today : next
}

/**
 * Year slider → day picker → local time-of-day slider. Slider drags edit a local
 * draft and commit after a pause, so a scrub doesn't fire 25 image requests per pixel.
 */
export function TimeControls({ moment, onChange, status }: Props) {
  // Draft is keyed to the committed moment: a new `moment` from outside discards the draft.
  const [pending, setPending] = useState<{ base: Moment; draft: Moment } | null>(null)
  const draft = pending?.base === moment ? pending.draft : moment
  const setDraft = (d: Moment) => setPending({ base: moment, draft: d })
  useEffect(() => {
    if (draft === moment) return
    const id = setTimeout(() => onChange(draft), 350)
    return () => clearTimeout(id)
  }, [draft, moment, onChange])

  const today = new Date().toISOString().slice(0, 10)
  const thisYear = Number(today.slice(0, 4))
  const year = Number(draft.date.slice(0, 4))
  const hh = pad(Math.floor(draft.minutes / 60))
  const mm = pad(draft.minutes % 60)

  return (
    <form
      className="card flex flex-wrap items-center gap-x-6 gap-y-3 px-5 py-3 text-sm"
      onSubmit={(e) => e.preventDefault()}
      aria-label="Time travel"
    >
      <label className="flex min-w-[220px] flex-1 items-center gap-3">
        <span className="muted w-8 tabular-nums">{ARCHIVE_FIRST_YEAR}</span>
        <input
          type="range"
          min={ARCHIVE_FIRST_YEAR}
          max={thisYear}
          step={1}
          value={year}
          onChange={(e) =>
            setDraft({ ...draft, date: clampDate(draft.date, Number(e.target.value), today) })
          }
          aria-label="Year"
          className="flex-1"
        />
        <span className="w-10 text-right font-semibold tabular-nums">{year}</span>
      </label>

      <input
        type="date"
        className="plain"
        min={`${ARCHIVE_FIRST_YEAR}-01-01`}
        max={today}
        value={draft.date}
        onChange={(e) => e.target.value && setDraft({ ...draft, date: e.target.value })}
        aria-label="Date"
      />

      <label className="flex min-w-[220px] flex-1 items-center gap-3">
        <span className="muted whitespace-nowrap">local time</span>
        <input
          type="range"
          min={0}
          max={1439}
          step={30}
          value={draft.minutes}
          onChange={(e) => setDraft({ ...draft, minutes: Number(e.target.value) })}
          aria-label="Local time of day"
          className="flex-1"
        />
        <span className="w-12 text-right font-semibold tabular-nums">
          {hh}:{mm}
        </span>
      </label>

      <span className="muted tabular-nums whitespace-nowrap">{status}</span>
    </form>
  )
}
