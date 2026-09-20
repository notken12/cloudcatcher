import { API_BASE, LIVE } from './api'
import type { Moment } from './view'
import cams from '../fixtures/archive-cams.json'

/**
 * Hand-picked cameras with a deep per-timestamp archive (see PLAN.md §time travel).
 * `direct` is a strftime template the browser may load itself (fixture mode);
 * cameras without it need the backend's /proxy/history.
 */
export interface ArchiveCam {
  id: string
  name: string
  lat: number
  lon: number
  /** First year with usable frames. */
  since: number
  source: string
  page: string
  direct?: string
  /** Archive cadence in minutes; requests snap to it. */
  step_min: number
  /** Archive paths keyed in UTC instead of camera-local time. */
  utc?: boolean
}

export const ARCHIVE_CAMS = cams as ArchiveCam[]

/** Cameras usable right now: all with a backend, only the directly loadable ones without. */
export function availableCams(): ArchiveCam[] {
  return LIVE ? ARCHIVE_CAMS : ARCHIVE_CAMS.filter((c) => c.direct)
}

/** Whole-hour offset from longitude (solar time); DST/zone borders are ±1 h off, which the
 *  archives' 5–30 min cadence and our hour-granular UI tolerate for a demo. */
export function utcOffsetHours(lon: number): number {
  return Math.round(lon / 15)
}

const pad = (n: number) => String(n).padStart(2, '0')

/** Camera-local wall clock for `m`, snapped to the archive cadence; UTC-keyed cams shift. */
export function localInstant(cam: ArchiveCam, m: Moment): Date {
  const [y, mo, d] = m.date.split('-').map(Number)
  const snapped = Math.round(m.minutes / cam.step_min) * cam.step_min
  const t = new Date(Date.UTC(y, mo - 1, d, 0, snapped))
  if (cam.utc) t.setUTCHours(t.getUTCHours() - utcOffsetHours(cam.lon))
  return t
}

function strftime(tpl: string, t: Date): string {
  return tpl
    .replace(/%Y/g, String(t.getUTCFullYear()))
    .replace(/%m/g, pad(t.getUTCMonth() + 1))
    .replace(/%d/g, pad(t.getUTCDate()))
    .replace(/%H/g, pad(t.getUTCHours()))
    .replace(/%M/g, pad(t.getUTCMinutes()))
}

function isoWithOffset(m: Moment, lon: number): string {
  const off = utcOffsetHours(lon)
  const sign = off < 0 ? '-' : '+'
  return `${m.date}T${pad(Math.floor(m.minutes / 60))}:${pad(m.minutes % 60)}:00${sign}${pad(Math.abs(off))}:00`
}

/** URL of the frame at `m` (camera-local), or null if this camera can't be shown offline. */
export function frameUrl(cam: ArchiveCam, m: Moment, width?: number): string | null {
  if (LIVE) {
    const q = new URLSearchParams({ ts: isoWithOffset(m, cam.lon) })
    if (width) q.set('w', String(width))
    return `${API_BASE}/proxy/history/${encodeURIComponent(cam.id)}?${q}`
  }
  if (!cam.direct) return null
  return strftime(cam.direct, localInstant(cam, m))
}

export function camHasYear(cam: ArchiveCam, m: Moment): boolean {
  return Number(m.date.slice(0, 4)) >= cam.since
}

/** "18:00 local · 15 Jun 2023" */
export function describeMoment(m: Moment): string {
  const [y, mo, d] = m.date.split('-').map(Number)
  const day = new Date(Date.UTC(y, mo - 1, d)).toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  })
  return `${pad(Math.floor(m.minutes / 60))}:${pad(m.minutes % 60)} local · ${day}`
}

/** Frames per day: 48 keeps a day under ~3 MB at w=640 through the proxy. */
export const LAPSE_STEP_MIN = 30

/** Frame URLs for `date` at LAPSE_STEP_MIN cadence (or the camera's, if coarser). */
export function lapseFrames(cam: ArchiveCam, date: string, width = 640) {
  const step = Math.max(cam.step_min, LAPSE_STEP_MIN)
  const out: { minutes: number; url: string }[] = []
  for (let minutes = 0; minutes < 1440; minutes += step) {
    const url = frameUrl(cam, { date, minutes }, width)
    if (url) out.push({ minutes, url })
  }
  return out
}
