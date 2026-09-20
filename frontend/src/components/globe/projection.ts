/**
 * Mirrors cobe's internal camera so DOM pins can be placed over its canvas
 * without depending on CSS anchor positioning. Globe radius in cobe is 0.8 of
 * the half-height; markers sit at radius 0.8 + markerElevation.
 */
export interface View {
  phi: number
  theta: number
  /** canvas aspect (width / height) */
  aspect: number
  scale?: number
  markerElevation?: number
}

export interface Projected {
  /** 0..1 across the canvas */
  x: number
  /** 0..1 down the canvas */
  y: number
  /** faces the camera */
  visible: boolean
  /** -1 (far side) .. 1 (nearest) — use for depth fade */
  depth: number
}

export function toUnit(lat: number, lon: number): [number, number, number] {
  const r = (lat * Math.PI) / 180
  const a = (lon * Math.PI) / 180 - Math.PI
  const o = Math.cos(r)
  return [-o * Math.cos(a), Math.sin(r), o * Math.sin(a)]
}

export function project(lat: number, lon: number, v: View): Projected {
  const radius = 0.8 + (v.markerElevation ?? 0.05)
  const [ux, uy, uz] = toUnit(lat, lon)
  const t0 = ux * radius
  const t1 = uy * radius
  const t2 = uz * radius
  const r = Math.cos(v.theta)
  const a = Math.cos(v.phi)
  const o = Math.sin(v.theta)
  const i = Math.sin(v.phi)
  const scale = v.scale ?? 1
  const c = a * t0 + i * t2
  const s = i * o * t0 + r * t1 - a * o * t2
  const depth = (-i * r * t0 + o * t1 + a * r * t2) / radius
  return {
    x: ((c / v.aspect) * scale + 1) / 2,
    y: (-s * scale + 1) / 2,
    visible: depth >= 0,
    depth,
  }
}

/** phi that brings (lat, lon) to face the camera (the point with depth = 1). */
export function phiFacing(lon: number): number {
  // depth = -sin(phi)cos(θ)t0 + ... is maximised when the unit vector's xz
  // component aligns with (-sin φ, cos φ); for lon: (x, z) = (-cos a, sin a).
  const a = (lon * Math.PI) / 180 - Math.PI
  return Math.atan2(Math.cos(a), Math.sin(a))
}

/** Shortest signed distance from `from` to `to` on the circle. */
export function shortestTurn(from: number, to: number): number {
  const d = (to - from) % (2 * Math.PI)
  return d > Math.PI ? d - 2 * Math.PI : d < -Math.PI ? d + 2 * Math.PI : d
}
