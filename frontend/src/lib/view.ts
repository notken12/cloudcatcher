import { useCallback, useSyncExternalStore } from 'react'

export type View = 'broadcast' | 'globe'

/** Hash route: `#/globe` ↔ globe layout, anything else ↔ broadcast. */
export function viewFromHash(hash: string): View {
  return hash.replace(/^#\/?/, '') === 'globe' ? 'globe' : 'broadcast'
}

function subscribe(cb: () => void) {
  window.addEventListener('hashchange', cb)
  return () => window.removeEventListener('hashchange', cb)
}

export function useView(): [View, (v: View) => void] {
  const view = useSyncExternalStore(
    subscribe,
    () => viewFromHash(window.location.hash),
    () => 'broadcast' as View,
  )
  const setView = useCallback((v: View) => {
    const next = v === 'globe' ? '#/globe' : ''
    if (next) window.location.hash = next
    else history.replaceState(null, '', window.location.pathname + window.location.search)
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  }, [])
  return [view, setView]
}
