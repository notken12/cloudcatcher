import { describe, expect, it } from 'vitest'
import { momentFromHash, momentToHash, viewFromHash } from './view'

describe('viewFromHash', () => {
  it('maps #/globe (and #globe) to the globe layout', () => {
    expect(viewFromHash('#/globe')).toBe('globe')
    expect(viewFromHash('#globe')).toBe('globe')
  })
  it('everything else is broadcast', () => {
    expect(viewFromHash('')).toBe('broadcast')
    expect(viewFromHash('#/')).toBe('broadcast')
    expect(viewFromHash('#/other')).toBe('broadcast')
  })
})

describe('time route', () => {
  it('maps #/time and #/time/<moment> to the time layout', () => {
    expect(viewFromHash('#/time')).toBe('time')
    expect(viewFromHash('#/time/2023-06-15T18:00')).toBe('time')
    expect(viewFromHash('#/timeline')).toBe('broadcast')
  })
  it('round-trips a moment through the hash', () => {
    const m = { date: '2023-06-15', minutes: 18 * 60 + 30 }
    expect(momentToHash(m)).toBe('#/time/2023-06-15T18:30')
    expect(momentFromHash(momentToHash(m))).toEqual(m)
    expect(momentFromHash('#/time')).toBeNull()
    expect(momentFromHash('#/time/2023-06-15T25:00')).toBeNull()
  })
})
