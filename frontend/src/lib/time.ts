export function relative(iso: string, now = Date.now()): string {
  const s = Math.max(0, Math.round((now - Date.parse(iso)) / 1000))
  if (s < 45) return 'just now'
  const m = Math.round(s / 60)
  if (m < 60) return `${m} min ago`
  const h = Math.round(m / 60)
  if (h < 48) return `${h} h ago`
  return `${Math.round(h / 24)} d ago`
}

export function utcClock(iso: string): string {
  const d = new Date(iso)
  const hh = String(d.getUTCHours()).padStart(2, '0')
  const mm = String(d.getUTCMinutes()).padStart(2, '0')
  return `${hh}:${mm} UTC`
}

export function localClock(iso: string, tz?: string): string | null {
  if (!tz) return null
  try {
    return new Intl.DateTimeFormat(undefined, {
      hour: '2-digit',
      minute: '2-digit',
      timeZone: tz,
    }).format(new Date(iso))
  } catch {
    return null
  }
}

/** Delay hint for streams: "delay ≈ 40 s" / "delay ≈ 3 min". */
export function delayHint(iso: string, now = Date.now()): string {
  const s = Math.max(0, Math.round((now - Date.parse(iso)) / 1000))
  if (s < 90) return `delay ≈ ${s} s`
  return `delay ≈ ${Math.round(s / 60)} min`
}

export function formatCoord(lat: number, lon: number): string {
  const f = (v: number, pos: string, neg: string) =>
    `${Math.abs(v).toFixed(1)}°${v >= 0 ? pos : neg}`
  return `${f(lat, 'N', 'S')} ${f(lon, 'E', 'W')}`
}
