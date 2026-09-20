import { Bell, BellOff, BellRing } from 'lucide-react'
import { usePush } from '../lib/push'

const TITLE = {
  idle: 'Notify me about cool sky events',
  subscribing: 'Asking…',
  subscribed: 'Notifications on',
  denied: 'Notifications blocked in Settings',
  error: 'Could not subscribe — tap to retry',
} as const

export function NotifyButton({ userId }: { userId?: string }) {
  const [state, subscribe] = usePush(userId)
  if (state === 'unsupported') return null
  const Icon = state === 'subscribed' ? BellRing : state === 'denied' ? BellOff : Bell
  const on = state === 'subscribed'
  return (
    <button
      type="button"
      className={`seg-solo ${state === 'subscribing' ? 'pulse' : ''}`}
      aria-pressed={on}
      disabled={on || state === 'denied' || state === 'subscribing'}
      onClick={subscribe}
      title={TITLE[state]}
    >
      <Icon className="h-4 w-4" aria-hidden />
      <span className="sr-only">{TITLE[state]}</span>
    </button>
  )
}
