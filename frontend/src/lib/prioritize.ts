import type { EventType, Footage } from './types'

export const LIKED_DWELL = 1.5

/** Liked event types first, preserving the feed's own order within each group. */
export function prioritize<T extends { event: { type: EventType } }>(
  items: T[],
  likes: EventType[],
): T[] {
  if (!likes.length) return items
  const liked = items.filter((f) => likes.includes(f.event.type))
  return liked.length ? [...liked, ...items.filter((f) => !likes.includes(f.event.type))] : items
}

export function dwellMs(f: Footage | undefined, likes: EventType[], base: number): number {
  return f && likes.includes(f.event.type) ? base * LIKED_DWELL : base
}
