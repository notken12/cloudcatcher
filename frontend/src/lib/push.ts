import { useCallback, useEffect, useState } from 'react'
import { API_BASE } from './api'

/** True when launched from the iOS/Android home screen (or a desktop PWA window). */
export function isStandalone(): boolean {
  if (typeof window === 'undefined') return false
  const nav = window.navigator as Navigator & { standalone?: boolean }
  return nav.standalone === true || window.matchMedia('(display-mode: standalone)').matches
}

export function pushSupported(): boolean {
  return (
    typeof window !== 'undefined' &&
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    'Notification' in window
  )
}

export function registerServiceWorker(): void {
  if (!('serviceWorker' in navigator)) return
  navigator.serviceWorker.register('/sw.js').catch(() => {})
}

export function urlBase64ToUint8Array(b64: string): Uint8Array<ArrayBuffer> {
  const pad = '='.repeat((4 - (b64.length % 4)) % 4)
  const raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'))
  const out = new Uint8Array(new ArrayBuffer(raw.length))
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i)
  return out
}

export type PushState = 'unsupported' | 'idle' | 'subscribing' | 'subscribed' | 'denied' | 'error'

async function fetchVapidKey(): Promise<string> {
  const r = await fetch(`${API_BASE}/push/vapid-public-key`)
  if (!r.ok) throw new Error(`/push/vapid-public-key ${r.status}`)
  return ((await r.json()) as { key: string }).key
}

async function postSubscription(sub: PushSubscription, userId?: string): Promise<void> {
  const r = await fetch(`${API_BASE}/push/subscribe`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ subscription: sub.toJSON(), user_id: userId ?? null }),
  })
  if (!r.ok) throw new Error(`/push/subscribe ${r.status}`)
}

/**
 * One-button Web Push opt-in. Only meaningful with a backend (`API_BASE`) and, on iOS,
 * when the page runs installed on the home screen.
 */
export function usePush(userId?: string): [PushState, () => void] {
  const [state, setState] = useState<PushState>(() =>
    !API_BASE || !pushSupported()
      ? 'unsupported'
      : Notification.permission === 'denied'
        ? 'denied'
        : 'idle',
  )

  useEffect(() => {
    if (state !== 'idle') return
    navigator.serviceWorker.ready
      .then((reg) => reg.pushManager.getSubscription())
      .then((sub) => {
        if (sub) setState('subscribed')
      })
      .catch(() => {})
  }, [state])

  const subscribe = useCallback(() => {
    if (state !== 'idle' && state !== 'error') return
    setState('subscribing')
    ;(async () => {
      const perm = await Notification.requestPermission()
      if (perm !== 'granted') {
        setState('denied')
        return
      }
      const reg = await navigator.serviceWorker.ready
      const key = await fetchVapidKey()
      const sub =
        (await reg.pushManager.getSubscription()) ??
        (await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(key),
        }))
      await postSubscription(sub, userId)
      setState('subscribed')
    })().catch(() => setState('error'))
  }, [state, userId])

  return [state, subscribe]
}
