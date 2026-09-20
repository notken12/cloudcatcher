import { beforeEach, describe, expect, it } from 'vitest'
import { getUser, register, saveLikes, signOut } from './user'
import { viewFromHash } from './view'

describe('user (fixture mode: device-local account)', () => {
  beforeEach(() => signOut())

  it('registers, persists likes, signs out', async () => {
    expect(getUser()).toBeNull()
    const u = await register('Sofia', '', ['aurora'])
    expect(u.id.startsWith('local-')).toBe(true)
    expect(getUser()?.likes).toEqual(['aurora'])
    expect(JSON.parse(localStorage.getItem('sunroof.user')!).name).toBe('Sofia')
    await saveLikes(['aurora', 'sunset'])
    expect(getUser()?.likes).toEqual(['aurora', 'sunset'])
    signOut()
    expect(getUser()).toBeNull()
    expect(localStorage.getItem('sunroof.user')).toBeNull()
  })
})

describe('#/join route', () => {
  it('maps to the join view', () => {
    expect(viewFromHash('#/join')).toBe('join')
    expect(viewFromHash('#join')).toBe('join')
    expect(viewFromHash('#/joined')).toBe('broadcast')
  })
})
