import type { EventType } from './types'

/** UI filter values: one chip per event type plus "all". */
export const FILTERS = [
  'all',
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
export type Filter = (typeof FILTERS)[number]

export const FILTER_LABEL: Record<Filter, string> = {
  all: 'All sights',
  sunrise: 'Sunrise',
  sunset: 'Sunset',
  thunderstorm: 'Thunderstorm',
  lightning: 'Lightning',
  mammatus: 'Mammatus',
  lenticular: 'Lenticular',
  undercast: 'Undercast',
  aurora: 'Aurora',
  rainbow: 'Rainbow',
}

export const EVENT_LABEL: Record<EventType, string> = {
  sunrise: 'Sunrise',
  sunset: 'Sunset',
  thunderstorm: 'Thunderstorm',
  lightning: 'Lightning',
  mammatus: 'Mammatus',
  lenticular: 'Lenticular',
  undercast: 'Undercast',
  aurora: 'Aurora',
  rainbow: 'Rainbow',
}

/** One-line field-guide caption (education track). */
export const EVENT_BLURB: Record<EventType, string> = {
  sunrise: 'High cloud lit from below while the sun is still under the horizon.',
  sunset: 'Long light path scatters out the blues; mid and high cloud catch the reds.',
  thunderstorm: 'A cumulonimbus tower: warm moist air rising to the tropopause.',
  lightning: 'Charge separated by ice collisions inside the storm discharges.',
  mammatus: 'Pouch-like lobes sinking from the underside of a decaying anvil.',
  lenticular: 'Stationary lens clouds where air waves over a ridge.',
  undercast: 'Looking down on a cloud deck from above the inversion.',
  aurora: 'Solar wind particles exciting oxygen and nitrogen high in the atmosphere.',
  rainbow: 'Sunlight refracted in raindrops, always opposite the sun.',
}

export function matchesFilter(type: EventType, f: Filter): boolean {
  if (f === 'all') return true
  return type === f
}

export function eventColor(type: EventType): string {
  return `var(--c-${type})`
}
