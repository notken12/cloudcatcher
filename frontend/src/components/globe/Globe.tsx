import { useEffect, useRef, useState } from 'react'
import type { Footage } from '../../lib/types'
import type { CameraPoint } from '../../lib/api'
import { EVENT_LABEL } from '../../lib/events'
import { mediaUrl } from '../../lib/api'
import { phiFacing, project, shortestTurn } from './projection'

interface Props {
  cameras: CameraPoint[]
  footage: Footage[]
  selectedId?: string
  onSelect: (f: Footage) => void
}

const THETA = 0.28
const IDLE_SPIN = 0.0025 // rad / frame
const MARKER_ELEVATION = 0.05

type PinPos = { x: number; y: number; visible: boolean; depth: number }

/**
 * cobe canvas + DOM pins. cobe draws the dotted earth and camera dots;
 * event pins are absolutely positioned <button>s projected with the same
 * camera maths every frame, so they stay clickable and styleable.
 */
export function Globe({ cameras, footage, selectedId, onSelect }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const pinRefs = useRef(new Map<string, HTMLButtonElement>())
  const [ready, setReady] = useState(false)

  // Mutable render state lives in refs so the rAF loop never re-renders React.
  const phi = useRef(0)
  const target = useRef<number | null>(null)
  const drag = useRef<{ x: number; phi: number } | null>(null)
  const lastInteraction = useRef(0)
  const footageRef = useRef(footage)
  useEffect(() => {
    footageRef.current = footage
  }, [footage])

  useEffect(() => {
    const canvas = canvasRef.current
    const wrap = wrapRef.current
    if (!canvas || !wrap) return
    let raf = 0
    let globe: { update: (s: Record<string, unknown>) => void; destroy: () => void } | null = null
    let cancelled = false
    let hidden = document.hidden

    const size = () => wrap.clientWidth || 600
    const dpr = Math.min(window.devicePixelRatio || 1, 2)

    const positionPins = (w: number) => {
      for (const f of footageRef.current) {
        const el = pinRefs.current.get(f.event_id)
        if (!el) continue
        const p: PinPos = project(f.camera.lat, f.camera.lon, {
          phi: phi.current,
          theta: THETA,
          aspect: 1,
          markerElevation: MARKER_ELEVATION,
        })
        el.style.transform = `translate(${(p.x * w).toFixed(1)}px, ${(p.y * w).toFixed(1)}px) translate(-50%, calc(-100% - 12px))`
        el.style.opacity = p.visible ? String(0.35 + 0.65 * p.depth) : '0'
        el.style.pointerEvents = p.visible ? 'auto' : 'none'
        el.style.zIndex = String(Math.round(100 + p.depth * 100))
      }
    }

    let w = size()
    const fit = () => {
      w = size()
      canvas.width = w * dpr
      canvas.height = w * dpr
      canvas.style.width = `${w}px`
      canvas.style.height = `${w}px`
      globe?.update({ width: w * dpr, height: w * dpr })
    }
    const ro = new ResizeObserver(fit)
    ro.observe(wrap)

    void import('cobe').then(({ default: createGlobe }) => {
      if (cancelled) return
      fit()
      globe = createGlobe(canvas, {
        devicePixelRatio: dpr,
        width: w * dpr,
        height: w * dpr,
        phi: phi.current,
        theta: THETA,
        dark: 0,
        diffuse: 1.6,
        mapSamples: 22000,
        mapBrightness: 3.2,
        mapBaseBrightness: 0.02,
        baseColor: [0.94, 0.94, 0.96],
        markerColor: [0.36, 0.42, 0.56],
        glowColor: [0.98, 0.98, 0.99],
        markerElevation: MARKER_ELEVATION,
        markers: cameras.map((c) => ({ location: c, size: 0.008 })),
      })
      setReady(true)

      const tick = () => {
        raf = requestAnimationFrame(tick)
        if (hidden) return
        const idle = performance.now() - lastInteraction.current > 4000
        if (target.current !== null) {
          const d = shortestTurn(phi.current, target.current)
          if (Math.abs(d) < 0.002) {
            phi.current = target.current
            target.current = null
          } else phi.current += d * 0.08
        } else if (!drag.current && idle) {
          phi.current += IDLE_SPIN
        }
        globe?.update({ phi: phi.current })
        positionPins(w)
      }
      tick()
    })

    const onVis = () => {
      hidden = document.hidden
    }
    document.addEventListener('visibilitychange', onVis)

    const onDown = (e: PointerEvent) => {
      if ((e.target as HTMLElement).closest('button')) return
      drag.current = { x: e.clientX, phi: phi.current }
      target.current = null
      lastInteraction.current = performance.now()
      canvas.style.cursor = 'grabbing'
    }
    const onMove = (e: PointerEvent) => {
      if (!drag.current) return
      phi.current = drag.current.phi + (e.clientX - drag.current.x) / 200
      lastInteraction.current = performance.now()
    }
    const onUp = () => {
      drag.current = null
      canvas.style.cursor = 'grab'
    }
    wrap.addEventListener('pointerdown', onDown)
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)

    return () => {
      cancelled = true
      cancelAnimationFrame(raf)
      ro.disconnect()
      globe?.destroy()
      document.removeEventListener('visibilitychange', onVis)
      wrap.removeEventListener('pointerdown', onDown)
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    // cameras only change when the catalog loads; remount for that.
  }, [cameras])

  // Fly to the selected footage.
  useEffect(() => {
    const f = footage.find((x) => x.event_id === selectedId)
    if (!f) return
    target.current = phiFacing(f.camera.lon)
    lastInteraction.current = performance.now()
  }, [selectedId, footage])

  return (
    <div
      ref={wrapRef}
      className="globe relative aspect-square w-full select-none"
      data-ready={ready}
    >
      <canvas
        ref={canvasRef}
        className="block"
        style={{ cursor: 'grab', opacity: ready ? 1 : 0, transition: 'opacity 0.6s' }}
      />
      <div className="pointer-events-none absolute inset-0">
        {footage.map((f) => (
          <button
            key={f.event_id}
            ref={(el) => {
              if (el) pinRefs.current.set(f.event_id, el)
              else pinRefs.current.delete(f.event_id)
            }}
            type="button"
            className={`pin ${f.event_id === selectedId ? 'pin-active' : ''}`}
            style={{ ['--pin' as string]: `var(--c-${f.event.type})`, opacity: 0 }}
            onClick={() => onSelect(f)}
            aria-label={`${EVENT_LABEL[f.event.type]} · ${f.event.place ?? f.camera.name}`}
            aria-pressed={f.event_id === selectedId}
          >
            <img src={mediaUrl(f.media.poster ?? f.media.src)} alt="" loading="lazy" />
            <span className="pin-tail" />
          </button>
        ))}
      </div>
    </div>
  )
}
