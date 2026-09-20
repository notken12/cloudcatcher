import { describe, expect, it } from 'vitest'
import { matchesFilter } from './events'

describe('matchesFilter', () => {
  it('merges fog and undercast under one chip', () => {
    expect(matchesFilter('fog', 'fog')).toBe(true)
    expect(matchesFilter('undercast', 'fog')).toBe(true)
    expect(matchesFilter('aurora', 'fog')).toBe(false)
    expect(matchesFilter('aurora', 'all')).toBe(true)
  })
})
