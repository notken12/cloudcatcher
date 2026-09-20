import { useCallback, useMemo, useSyncExternalStore } from 'react'

export type View = 'broadcast' | 'globe' | 'time' | 'show'

/** Hash route: `#/globe` ↔ globe, `#/time[/YYYY-MM-DDTHH:MM]` ↔ time travel, `#/show` ↔ story
 *  mode (full-screen auto-play), else broadcast. */
export function viewFromHash(hash: string): View {
  const path = hash.replace(/^#\/?/, '')
  if (path === 'globe') return 'globe'
  if (path === 'show') return 'show'
  if (path === 'time' || path.startsWith('time/')) return 'time'
  return 'broadcast'
}

/** A camera-local wall-clock moment: calendar date + minutes since midnight. */
export interface Moment {
  date: string // YYYY-MM-DD
  minutes: number // 0..1439
}

export const ARCHIVE_FIRST_YEAR = 2016

export function defaultMoment(now = new Date()): Moment {
  // A year ago today, an hour before a typical sunset — the most photogenic default.
  const d = new Date(Date.UTC(now.getUTCFullYear() - 1, now.getUTCMonth(), now.getUTCDate()))
  return { date: d.toISOString().slice(0, 10), minutes: 18 * 60 }
}

export function momentFromHash(hash: string): Moment | null {
  const m = /^#\/?time\/(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})$/.exec(hash)
  if (!m) return null
  const minutes = Number(m[2]) * 60 + Number(m[3])
  if (minutes > 1439 || Number.isNaN(Date.parse(m[1]))) return null
  return { date: m[1], minutes }
}

/** `m` advanced by `step` minutes; wraps within the same day (the time-of-day slider is a ring). */
export function stepMoment(m: Moment, step: number): Moment {
  return { ...m, minutes: (((m.minutes + step) % 1440) + 1440) % 1440 }
}

export function momentToHash(m: Moment): string {
  const hh = String(Math.floor(m.minutes / 60)).padStart(2, '0')
  const mm = String(m.minutes % 60).padStart(2, '0')
  return `#/time/${m.date}T${hh}:${mm}`
}

function subscribe(cb: () => void) {
  window.addEventListener('hashchange', cb)
  return () => window.removeEventListener('hashchange', cb)
}

function setHash(next: string, replace: boolean) {
  if (!next) history.replaceState(null, '', window.location.pathname + window.location.search)
  else if (replace) history.replaceState(null, '', next)
  else window.location.hash = next
  window.dispatchEvent(new HashChangeEvent('hashchange'))
}

export function useView(): [View, (v: View) => void] {
  const view = useSyncExternalStore(
    subscribe,
    () => viewFromHash(window.location.hash),
    () => 'broadcast' as View,
  )
  const setView = useCallback((v: View) => {
    setHash(v === 'broadcast' ? '' : `#/${v}`, false)
  }, [])
  return [view, setView]
}

const DEFAULT_MOMENT = defaultMoment()

/** The time-travel moment lives in the hash so a view is linkable; scrubbing replaces, not pushes. */
export function useMoment(): [Moment, (m: Moment) => void] {
  const hash = useSyncExternalStore(
    subscribe,
    () => window.location.hash,
    () => '',
  )
  const moment = useMemo(() => momentFromHash(hash) ?? DEFAULT_MOMENT, [hash])
  const setMoment = useCallback((m: Moment) => setHash(momentToHash(m), true), [])
  return [moment, setMoment]
}
