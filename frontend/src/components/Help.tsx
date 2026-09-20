import { X } from 'lucide-react'

const ROWS: [string, string][] = [
  ['← →', 'previous / next sight'],
  ['1 2 3 4', 'broadcast · globe · time travel · story mode'],
  ['space', 'play / pause the day (time travel)'],
  ['esc', 'leave story mode · close this'],
  ['?', 'this card'],
]

/** Keyboard cheat-sheet; a card floating over whatever layout is up. */
export function Help({ onClose }: { onClose: () => void }) {
  return (
    <div
      className="fixed inset-0 z-[1000] grid place-items-center bg-black/20 p-6"
      onClick={onClose}
      role="dialog"
      aria-label="Keyboard shortcuts"
    >
      <div className="card fade-in w-full max-w-sm p-5" onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-[17px] font-semibold">Keyboard</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="muted">
            <X className="h-4 w-4" />
          </button>
        </div>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
          {ROWS.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="flex gap-1">
                {k.split(' ').map((x) => (
                  <kbd key={x}>{x}</kbd>
                ))}
              </dt>
              <dd className="muted self-center">{v}</dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  )
}
