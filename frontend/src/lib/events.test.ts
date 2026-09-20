import { describe, expect, it } from 'vitest'
import { matchesFilter } from './events'

describe('matchesFilter', () => {
  it('matches exact type or all', () => {
    expect(matchesFilter('undercast', 'undercast')).toBe(true)
    expect(matchesFilter('aurora', 'undercast')).toBe(false)
    expect(matchesFilter('aurora', 'all')).toBe(true)
  })
})
