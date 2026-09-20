import { describe, expect, it } from 'vitest'
import { viewFromHash } from './view'

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
