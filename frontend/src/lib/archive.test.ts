import { describe, expect, it } from 'vitest'
import { ARCHIVE_CAMS, camHasYear, frameUrl, localInstant, utcOffsetHours } from './archive'

const by = (id: string) => ARCHIVE_CAMS.find((c) => c.id === id)!
const m = { date: '2023-06-15', minutes: 18 * 60 + 5 }

describe('archive cams (fixture mode)', () => {
  it('ships a valid, globally spread fixture', () => {
    expect(ARCHIVE_CAMS.length).toBeGreaterThanOrEqual(20)
    expect(new Set(ARCHIVE_CAMS.map((c) => c.id)).size).toBe(ARCHIVE_CAMS.length)
    expect(ARCHIVE_CAMS.some((c) => c.lat < 0)).toBe(true)
    expect(ARCHIVE_CAMS.some((c) => c.lon > 100)).toBe(true)
  })
  it('snaps to cadence and fills the strftime template in camera-local time', () => {
    const cam = by('fotowebcam:zugspitze')
    expect(localInstant(cam, m).toISOString()).toBe('2023-06-15T18:10:00.000Z')
    expect(frameUrl(cam, m)).toBe(
      'https://www.foto-webcam.eu/webcam/zugspitze/2023/06/15/1810_la.jpg',
    )
  })
  it('shifts UTC-keyed archives by the longitude offset', () => {
    const cam = by('iem:ISUC-006')
    expect(utcOffsetHours(cam.lon)).toBe(-6)
    expect(frameUrl(cam, m)).toContain('/2023/06/16/camera/ISUC-006/ISUC-006_202306160005.jpg')
  })
  it('needs the backend for cams without a direct template', () => {
    expect(frameUrl(by('phenocam:harvard'), m)).toBeNull()
    expect(camHasYear(by('phenocam:bezamahafaly'), m)).toBe(false)
  })
})
