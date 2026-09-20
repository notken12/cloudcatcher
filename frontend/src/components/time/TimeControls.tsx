import { Check, Link2, Pause, Play } from 'lucide-react'
import { useEffect, useState } from 'react'
import { ARCHIVE_FIRST_YEAR, type Moment } from '../../lib/view'

interface Props {
  moment: Moment
  onChange: (m: Moment) => void
  /** e.g. "8 cameras" */
  status: string
  /** Sunset ring: the local clock advances by itself, every camera passes through dusk together. */
  playing: boolean
  onPlaying: (p: boolean) => void
}

/** Copies the current (linkable) URL; flashes a check for a moment. */
function ShareButton() {
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    if (!copied) return
    const id = setTimeout(() => setCopied(false), 1500)
    return () => clearTimeout(id)
  }, [copied])
  return (
    <button
      type="button"
      className="btn"
      onClick={() => {
        void navigator.clipboard?.writeText(window.location.href).then(() => setCopied(true))
      }}
      title="Copy link to this moment"
    >
      {copied ? <Check className="h-3.5 w-3.5" /> : <Link2 className="h-3.5 w-3.5" />}
      {copied ? 'copied' : 'share'}
    </button>
  )
}

const pad = (n: number) => String(n).padStart(2, '0')

function clampDate(date: string, year: number, today: string): string {
  const next = `${year}${date.slice(4)}`
  return next > today ? today : next
}

/** Lets the browser hold half-typed dates (e.g. year "0002") and only reports in-range ones. */
function DateInput({
  value,
  min,
  max,
  onValid,
}: {
  value: string
  min: string
  max: string
  onValid: (date: string) => void
}) {
  const [typed, setTyped] = useState<{ base: string; text: string } | null>(null)
  const text = typed?.base === value ? typed.text : value
  return (
    <input
      type="date"
      className="plain"
      min={min}
      max={max}
      value={text}
      onChange={(e) => {
        const v = e.target.value
        setTyped({ base: value, text: v })
        if (v >= min && v <= max) onValid(v)
      }}
      aria-label="Date"
    />
  )
}

/**
 * Year slider → day picker → local time-of-day slider. Slider drags edit a local
 * draft and commit after a pause, so a scrub doesn't fire 25 image requests per pixel.
 */
export function TimeControls({ moment, onChange, status, playing, onPlaying }: Props) {
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

      <DateInput
        value={draft.date}
        min={`${ARCHIVE_FIRST_YEAR}-01-01`}
        max={today}
        onValid={(date) => setDraft({ ...draft, date })}
      />

      <div className="flex min-w-[240px] flex-1 items-center gap-3">
        <button
          type="button"
          className="btn"
          aria-pressed={playing}
          onClick={() => onPlaying(!playing)}
          title={
            playing ? 'Pause (space)' : 'Play the day: every camera at the same local time (space)'
          }
        >
          {playing ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
          <span className="sr-only">{playing ? 'Pause' : 'Play'}</span>
        </button>
        <label className="flex flex-1 items-center gap-3">
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
      </div>

      <span className="muted tabular-nums whitespace-nowrap">{status}</span>
      <ShareButton />
    </form>
  )
}
