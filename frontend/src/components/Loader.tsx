import { Cloud, CloudLightning, Sun } from 'lucide-react'
import { useEffect, useState } from 'react'

const GLYPHS = [Sun, Cloud, CloudLightning]

/** Weather glyphs cross-fading: the loading / empty state. */
export function Loader({ label }: { label?: string }) {
  const [i, setI] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setI((n) => (n + 1) % GLYPHS.length), 900)
    return () => clearInterval(id)
  }, [])
  const Glyph = GLYPHS[i]
  return (
    <div className="muted grid h-full w-full place-items-center">
      <div className="flex flex-col items-center gap-3">
        <Glyph key={i} className="fade-in h-10 w-10" strokeWidth={1.4} />
        {label && <span className="text-sm">{label}</span>}
      </div>
    </div>
  )
}
