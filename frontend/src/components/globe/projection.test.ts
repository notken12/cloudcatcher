import { describe, expect, it } from 'vitest'
import { phiFacing, project, shortestTurn } from './projection'

describe('projection', () => {
  it('phiFacing centres the point horizontally and puts it on the near side', () => {
    for (const [lat, lon] of [
      [0, 0],
      [39.7, -104.9],
      [67.8, 20.4],
      [-33.9, 151.2],
    ]) {
      const p = project(lat, lon, { phi: phiFacing(lon), theta: 0, aspect: 1 })
      expect(p.x).toBeCloseTo(0.5, 6)
      expect(p.visible).toBe(true)
      expect(p.depth).toBeCloseTo(Math.cos((lat * Math.PI) / 180), 6)
    }
  })

  it('the antipode is hidden', () => {
    const p = project(0, 180, { phi: phiFacing(0), theta: 0, aspect: 1 })
    expect(p.visible).toBe(false)
  })

  it('northern latitudes render above centre', () => {
    const p = project(60, 0, { phi: phiFacing(0), theta: 0, aspect: 1 })
    expect(p.y).toBeLessThan(0.5)
  })

  it('shortestTurn wraps', () => {
    expect(shortestTurn(0, 2 * Math.PI - 0.1)).toBeCloseTo(-0.1)
    expect(shortestTurn(3, -3)).toBeCloseTo(2 * Math.PI - 6)
  })
})
