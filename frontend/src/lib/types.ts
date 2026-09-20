// Contract owned by notken12/sunroof camera/docs/query-and-routing-plan.md §5.
// Keep in sync by hand; this file is the diff to review when the backend changes.

export const EVENT_TYPES = [
  'sunrise',
  'sunset',
  'thunderstorm',
  'lightning',
  'mammatus',
  'lenticular',
  'undercast',
  'aurora',
  'rainbow',
] as const

export type EventType = (typeof EVENT_TYPES)[number]
export type MediaKind = 'image' | 'hls' | 'iframe'

export interface Media {
  kind: MediaKind
  /** Always our proxy unless embed_allowed && CORS ok. */
  src: string
  /** For hls/iframe: last verified still, shown while the player buffers. */
  poster?: string
  /** image only: re-request src with a new ?t every refresh_s. */
  refresh_s?: number
  expires_at?: string
  width?: number
  height?: number
}

export interface Verdict {
  event_visible: 'yes' | 'partial' | 'no' | 'unsure'
  confidence: number
  quality: number
  caption: string
}

export interface CameraRef {
  name: string
  lat: number
  lon: number
  source: string
  page_url?: string
  attribution?: string
  license?: string
}

export interface EventRef {
  type: EventType
  lat: number
  lon: number
  radius_km: number
  place?: string
  rarity?: number
  severity?: number
}

export interface Footage {
  event_id: string
  camera_id: string
  rank: number
  /** false for iframe-only sources we could not VLM-check */
  verified: boolean
  media: Media
  verdict?: Verdict
  why: string
  camera: CameraRef
  /** UTC ISO */
  frame_ts: string
  hold_until: string
  event: EventRef
}

export interface StreamMessage {
  event_id: string
  status: string
}
