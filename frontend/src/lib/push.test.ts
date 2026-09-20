import { describe, expect, it } from 'vitest'
import { personalEnabled } from './personal'
import { isStandalone, urlBase64ToUint8Array } from './push'

describe('urlBase64ToUint8Array', () => {
  it('decodes url-safe base64 without padding', () => {
    // "hello?>" → aGVsbG8_Pg (url-safe, unpadded)
    expect(Array.from(urlBase64ToUint8Array('aGVsbG8_Pg'))).toEqual([
      104, 101, 108, 108, 111, 63, 62,
    ])
  })
})

describe('personal gating', () => {
  it('is off in a plain browser tab', () => {
    window.matchMedia = () => ({ matches: false }) as MediaQueryList
    expect(isStandalone()).toBe(false)
    expect(personalEnabled()).toBe(false)
  })
  it('is on when installed (display-mode: standalone)', () => {
    window.matchMedia = () => ({ matches: true }) as MediaQueryList
    expect(isStandalone()).toBe(true)
    expect(personalEnabled()).toBe(true)
  })
  it('is on with ?personal in the URL', () => {
    window.matchMedia = () => ({ matches: false }) as MediaQueryList
    history.replaceState(null, '', '/?personal')
    expect(personalEnabled()).toBe(true)
    history.replaceState(null, '', '/')
  })
})
