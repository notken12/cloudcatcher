import { useCallback, useSyncExternalStore } from 'react'
import { API_BASE } from './api'
import type { EventType } from './types'

/** The one signed-in person on this phone. No password: the id is the credential. */
export interface User {
  id: string
  name: string
  email?: string
  likes: EventType[]
}

const KEY = 'sunroof.user'
const listeners = new Set<() => void>()

function read(): User | null {
  try {
    const raw = localStorage.getItem(KEY)
    return raw ? (JSON.parse(raw) as User) : null
  } catch {
    return null
  }
}

let cached: User | null | undefined

function write(u: User | null) {
  cached = u
  if (u) localStorage.setItem(KEY, JSON.stringify(u))
  else localStorage.removeItem(KEY)
  listeners.forEach((l) => l())
}

function subscribe(cb: () => void) {
  listeners.add(cb)
  return () => {
    listeners.delete(cb)
  }
}

export function getUser(): User | null {
  if (cached === undefined) cached = read()
  return cached
}

async function api<T>(path: string, method: string, body: unknown): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, {
    method,
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`${path} ${r.status}`)
  return (await r.json()) as T
}

/** Fixture mode (no backend) keeps the account on the device only. */
export async function register(name: string, email: string, likes: EventType[]): Promise<User> {
  const u: User = API_BASE
    ? await api<User>('/users', 'POST', { name, email: email || null, likes })
    : { id: `local-${Date.now().toString(36)}`, name, email: email || undefined, likes }
  write(u)
  return u
}

export async function saveLikes(likes: EventType[]): Promise<void> {
  const u = getUser()
  if (!u) return
  if (API_BASE && !u.id.startsWith('local-')) {
    await api<User>(`/users/${u.id}/prefs`, 'PUT', { likes })
  }
  write({ ...u, likes })
}

export function signOut(): void {
  write(null)
}

export function useUser(): User | null {
  return useSyncExternalStore(subscribe, getUser, () => null)
}

export function useLikes(): [EventType[], (likes: EventType[]) => Promise<void>] {
  const u = useUser()
  return [u?.likes ?? [], useCallback((l: EventType[]) => saveLikes(l), [])]
}
