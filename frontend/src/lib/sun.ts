/** Where the sun is overhead right now (±1° — fine for shading a 600 px globe). */
export function subsolar(now = new Date()): { lat: number; lon: number } {
  const start = Date.UTC(now.getUTCFullYear(), 0, 0)
  const doy = (now.getTime() - start) / 86_400_000
  const lat = -23.44 * Math.cos((2 * Math.PI * (doy + 10)) / 365.25)
  const hours = now.getUTCHours() + now.getUTCMinutes() / 60 + now.getUTCSeconds() / 3600
  const lon = 180 - hours * 15
  return { lat, lon: ((lon + 540) % 360) - 180 }
}

/**
 * 0 = broad daylight, 1 = night, ramping through dusk/dawn; drives the globe's tint in time
 * travel, where every camera sits at the same local clock so there is no single terminator.
 */
export function duskiness(minutes: number): number {
  const h = minutes / 60
  const ramp = (from: number, to: number) => Math.min(1, Math.max(0, (h - from) / (to - from)))
  if (h < 12) return 1 - ramp(4.5, 7)
  return ramp(17.5, 20.5)
}
