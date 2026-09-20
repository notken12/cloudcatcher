import { useEffect, useRef, useState } from 'react'
import type { CameraPoint } from '../../lib/api'
import { drawNight, phiFacing, project, shortestTurn } from './projection'

/** Anything with a place and a thumbnail: live footage or an archive frame. */
export interface Pin {
  id: string
  lat: number
  lon: number
  /** CSS color for the ring/tail. */
  color: string
  /** Thumbnail URL; null = nothing to show at this moment (pin renders hollow). */
  thumb: string | null
  label: string
}

interface Props {
  cameras: CameraPoint[]
  pins: Pin[]
  selectedId?: string
  onSelect: (id: string) => void
  /** Subsolar point: shades the night half. Omit for a flat, unlit globe. */
  sun?: { lat: number; lon: number }
  /** 0 (day) .. 1 (night): tints the whole globe when there is no single terminator. */
  dusk?: number
}

const DAY = { base: [0.94, 0.94, 0.96], glow: [0.98, 0.98, 0.99], marker: [0.36, 0.42, 0.56] }
const NIGHT = { base: [0.72, 0.74, 0.84], glow: [0.86, 0.87, 0.94], marker: [0.95, 0.8, 0.45] }
const mix = (a: number[], b: number[], t: number) => a.map((v, i) => v + (b[i] - v) * t)

const THETA = 0.28
const IDLE_SPIN = 0.0025 // rad / frame
const MARKER_ELEVATION = 0.05

type PinPos = { x: number; y: number; visible: boolean; depth: number }

/**
 * cobe canvas + DOM pins. cobe draws the dotted earth and camera dots;
 * event pins are absolutely positioned <button>s projected with the same
 * camera maths every frame, so they stay clickable and styleable.
 */
export function Globe({ cameras, pins, selectedId, onSelect, sun, dusk = 0 }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const nightRef = useRef<HTMLCanvasElement>(null)
  const pinRefs = useRef(new Map<string, HTMLButtonElement>())
  const [ready, setReady] = useState(false)
  const [broken, setBroken] = useState<ReadonlySet<string>>(() => new Set())

  // Mutable render state lives in refs so the rAF loop never re-renders React.
  const phi = useRef(0)
  const target = useRef<number | null>(null)
  const drag = useRef<{ x: number; phi: number } | null>(null)
  const lastInteraction = useRef(0)
  const pinsRef = useRef(pins)
  useEffect(() => {
    pinsRef.current = pins
  }, [pins])
  const sunRef = useRef(sun)
  const duskRef = useRef(dusk)
  const shadeDirty = useRef(true)
  useEffect(() => {
    sunRef.current = sun
    duskRef.current = dusk
    shadeDirty.current = true
  }, [sun, dusk])

  useEffect(() => {
    const canvas = canvasRef.current
    const night = nightRef.current
    const wrap = wrapRef.current
    if (!canvas || !night || !wrap) return
    const nctx = night.getContext('2d')
    let raf = 0
    let globe: { update: (s: Record<string, unknown>) => void; destroy: () => void } | null = null
    let cancelled = false
    let hidden = document.hidden

    const size = () => wrap.clientWidth || 600
    const dpr = Math.min(window.devicePixelRatio || 1, 2)

    const positionPins = (w: number) => {
      for (const f of pinsRef.current) {
        const el = pinRefs.current.get(f.id)
        if (!el) continue
        const p: PinPos = project(f.lat, f.lon, {
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
      night.width = w
      night.height = w
      night.style.width = `${w}px`
      night.style.height = `${w}px`
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
        const t = duskRef.current
        globe?.update(
          shadeDirty.current
            ? {
                phi: phi.current,
                baseColor: mix(DAY.base, NIGHT.base, t),
                glowColor: mix(DAY.glow, NIGHT.glow, t),
                markerColor: mix(DAY.marker, NIGHT.marker, t),
              }
            : { phi: phi.current },
        )
        shadeDirty.current = false
        if (nctx) drawNight(nctx, w, sunRef.current, { phi: phi.current, theta: THETA })
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

  // Fly to the selected pin.
  useEffect(() => {
    const f = pins.find((x) => x.id === selectedId)
    if (!f) return
    target.current = phiFacing(f.lon)
    lastInteraction.current = performance.now()
  }, [selectedId, pins])

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
      <canvas
        ref={nightRef}
        className="pointer-events-none absolute inset-0 block"
        style={{ opacity: ready ? 1 : 0, transition: 'opacity 0.6s' }}
        aria-hidden
      />
      <div className="pointer-events-none absolute inset-0">
        {pins.map((f) => {
          const thumb = f.thumb && !broken.has(f.thumb) ? f.thumb : null
          return (
            <button
              key={f.id}
              ref={(el) => {
                if (el) pinRefs.current.set(f.id, el)
                else pinRefs.current.delete(f.id)
              }}
              type="button"
              className={`pin ${f.id === selectedId ? 'pin-active' : ''} ${thumb ? '' : 'pin-empty'}`}
              style={{ ['--pin' as string]: f.color, opacity: 0 }}
              onClick={() => onSelect(f.id)}
              aria-label={f.label}
              aria-pressed={f.id === selectedId}
            >
              {thumb && (
                <img
                  src={thumb}
                  alt=""
                  loading="lazy"
                  onLoad={(e) => e.currentTarget.classList.add('loaded')}
                  onError={() => setBroken((b) => new Set(b).add(thumb))}
                />
              )}
              <span className="pin-tail" />
              <span className="pin-label">{f.label}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
