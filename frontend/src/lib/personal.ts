import { isStandalone } from './push'

/**
 * Personal features (notifications, sign-up, preferences) are shown only to the installed
 * home-screen app, so the shared web/projector view stays anonymous and unchanged.
 * `VITE_PERSONAL=1` or `?personal` forces them on for desktop development.
 */
export function personalEnabled(): boolean {
  if (import.meta.env.VITE_PERSONAL === '1') return true
  if (typeof window !== 'undefined' && new URLSearchParams(window.location.search).has('personal'))
    return true
  return isStandalone()
}
