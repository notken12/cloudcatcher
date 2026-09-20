import { Check } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { EVENT_BLURB, EVENT_LABEL, eventColor } from '../lib/events'
import { EVENT_TYPES, type EventType } from '../lib/types'
import { register, saveLikes, signOut, useUser } from '../lib/user'
import { NotifyButton } from './NotifyButton'

function toggle(list: EventType[], t: EventType): EventType[] {
  return list.includes(t) ? list.filter((x) => x !== t) : [...list, t]
}

/** Pill per sky event; pressed = filled with the event colour. */
function LikeChips({
  likes,
  onChange,
  disabled,
}: {
  likes: EventType[]
  onChange: (l: EventType[]) => void
  disabled?: boolean
}) {
  return (
    <div className="flex flex-wrap gap-2" role="group" aria-label="Sights you love">
      {EVENT_TYPES.map((t) => {
        const on = likes.includes(t)
        return (
          <button
            key={t}
            type="button"
            className="like"
            aria-pressed={on}
            disabled={disabled}
            style={{ ['--like' as string]: eventColor(t) }}
            title={EVENT_BLURB[t]}
            onClick={() => onChange(toggle(likes, t))}
          >
            {on && <Check className="h-3.5 w-3.5" aria-hidden />}
            {EVENT_LABEL[t]}
          </button>
        )
      })}
    </div>
  )
}

/** `#/join`: a one-screen sign-up (name, optional email, the sights you love) → then your prefs. */
export function JoinCard() {
  const user = useUser()
  return (
    <section className="card fade-in mx-auto w-full max-w-[520px] px-6 py-6 sm:px-8 sm:py-8">
      {user ? <Prefs /> : <SignUp />}
    </section>
  )
}

function SignUp() {
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [likes, setLikes] = useState<EventType[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!name.trim() || busy) return
    setBusy(true)
    setError(null)
    register(name.trim(), email.trim(), likes)
      .catch(() => setError('Could not reach sunroof — try again in a moment.'))
      .finally(() => setBusy(false))
  }

  return (
    <form className="flex flex-col gap-5" onSubmit={submit} aria-label="Join sunroof">
      <header className="flex flex-col gap-1">
        <h2 className="text-[22px] font-semibold tracking-tight">Hello, sky watcher</h2>
        <p className="muted text-[15px]">
          Tell us what you love and we'll tap you on the shoulder when it's happening somewhere —
          once in a while, never a flood.
        </p>
      </header>

      <label className="flex flex-col gap-1.5">
        <span className="muted text-[13px]">Your name</span>
        <input
          className="plain"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Sofia"
          autoComplete="given-name"
          maxLength={60}
          required
        />
      </label>
      <label className="flex flex-col gap-1.5">
        <span className="muted text-[13px]">Email (optional)</span>
        <input
          className="plain"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          autoComplete="email"
          maxLength={120}
        />
      </label>

      <div className="flex flex-col gap-2">
        <span className="muted text-[13px]">Sights you love</span>
        <LikeChips likes={likes} onChange={setLikes} />
      </div>

      {error && <p className="text-sm text-[#b3261e]">{error}</p>}

      <button type="submit" className="cta" disabled={!name.trim() || busy}>
        {busy ? 'Joining…' : 'Join sunroof'}
      </button>
    </form>
  )
}

function Prefs() {
  const user = useUser()!
  const [saving, setSaving] = useState(false)
  const onLikes = (l: EventType[]) => {
    setSaving(true)
    saveLikes(l).finally(() => setSaving(false))
  }
  return (
    <div className="flex flex-col gap-5">
      <header className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h2 className="text-[22px] font-semibold tracking-tight">Hi, {user.name}</h2>
          <p className="muted text-[15px]">
            {user.likes.length
              ? 'We’ll nudge you when one of these is live.'
              : 'Pick a few sights and we’ll nudge you when one is live.'}
          </p>
        </div>
        <NotifyButton userId={user.id} />
      </header>

      <div className="flex flex-col gap-2">
        <span className="muted text-[13px]">Sights you love {saving && '· saving…'}</span>
        <LikeChips likes={user.likes} onChange={onLikes} disabled={saving} />
      </div>

      <p className="muted text-[13px]">
        Liked sights show up first and linger longer in your broadcast.
      </p>

      <button type="button" className="muted self-start text-[13px] underline" onClick={signOut}>
        Sign out on this device
      </button>
    </div>
  )
}
