import { describe, expect, it } from 'vitest'
import { LIKED_DWELL, dwellMs, prioritize } from './prioritize'
import type { EventType, Footage } from './types'

const f = (id: string, type: EventType) => ({ id, event: { type } })

describe('prioritize', () => {
  const feed = [f('a', 'sunset'), f('b', 'aurora'), f('c', 'rainbow'), f('d', 'aurora')]

  it('is a no-op for anonymous users', () => {
    expect(prioritize(feed, [])).toBe(feed)
  })

  it('moves liked types first, keeping order within each group', () => {
    expect(prioritize(feed, ['aurora']).map((x) => x.id)).toEqual(['b', 'd', 'a', 'c'])
    expect(prioritize(feed, ['rainbow', 'sunset']).map((x) => x.id)).toEqual(['a', 'c', 'b', 'd'])
  })

  it('leaves the feed untouched when nothing liked is live', () => {
    expect(prioritize(feed, ['lightning'])).toBe(feed)
  })
})

describe('dwellMs', () => {
  it('lingers longer on liked sights only', () => {
    const cur = f('a', 'sunset') as unknown as Footage
    expect(dwellMs(cur, ['sunset'], 20_000)).toBe(20_000 * LIKED_DWELL)
    expect(dwellMs(cur, ['aurora'], 20_000)).toBe(20_000)
    expect(dwellMs(undefined, ['sunset'], 20_000)).toBe(20_000)
  })
})
