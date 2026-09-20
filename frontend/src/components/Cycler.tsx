import { useEffect } from 'react'

interface Props {
  count: number
  index: number
  onChange: (i: number) => void
  paused: boolean
  periodMs?: number
}

/** Dots under the hero; auto-advances unless paused (hover). */
export function Cycler({ count, index, onChange, paused, periodMs = 20_000 }: Props) {
  useEffect(() => {
    if (paused || count < 2) return
    const id = setInterval(() => onChange((index + 1) % count), periodMs)
    return () => clearInterval(id)
  }, [paused, count, index, onChange, periodMs])

  if (count < 2) return null
  return (
    <nav className="flex justify-center gap-2 py-2" aria-label="Other sights right now">
      {Array.from({ length: count }, (_, i) => (
        <button
          key={i}
          type="button"
          onClick={() => onChange(i)}
          aria-label={`Show ${i + 1} of ${count}`}
          aria-current={i === index}
          className={`h-2 w-2 cursor-pointer rounded-full transition ${
            i === index ? 'bg-neutral-800' : 'bg-neutral-300 hover:bg-neutral-400'
          }`}
        />
      ))}
    </nav>
  )
}
