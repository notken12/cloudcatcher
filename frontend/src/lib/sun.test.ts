import { describe, expect, it } from 'vitest'
import { duskiness, subsolar } from './sun'
import { stepMoment } from './view'

describe('subsolar', () => {
  it('is over the equator near the equinox and at lon 0 at 12:00 UTC', () => {
    const s = subsolar(new Date('2024-03-20T12:00:00Z'))
    expect(Math.abs(s.lat)).toBeLessThan(1)
    expect(Math.abs(s.lon)).toBeLessThan(1)
  })
  it('is at the tropic in late June and over the antimeridian at 00:00 UTC', () => {
    const s = subsolar(new Date('2024-06-21T00:00:00Z'))
    expect(s.lat).toBeCloseTo(23.4, 0)
    expect(Math.abs(Math.abs(s.lon) - 180)).toBeLessThan(1)
  })
})

describe('duskiness', () => {
  it('is day at noon, night at midnight, in between at dusk', () => {
    expect(duskiness(12 * 60)).toBe(0)
    expect(duskiness(0)).toBe(1)
    const d = duskiness(19 * 60)
    expect(d).toBeGreaterThan(0.3)
    expect(d).toBeLessThan(0.7)
  })
})

describe('stepMoment', () => {
  it('wraps within the day', () => {
    expect(stepMoment({ date: '2024-01-01', minutes: 1430 }, 30).minutes).toBe(20)
    expect(stepMoment({ date: '2024-01-01', minutes: 0 }, -30).minutes).toBe(1410)
  })
})
