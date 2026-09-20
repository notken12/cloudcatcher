import { useCallback, useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import type { Footage, StreamMessage } from './types'
import fixture from '../fixtures/feed.json'
import cameraFixture from '../fixtures/cameras.json'

/** Unset → fixture mode (fully offline demo). Set → e.g. "http://localhost:8000",
 *  or "/" when the camera backend serves this build itself (`serve --frontend`). */
const RAW_BASE: string | undefined = import.meta.env.VITE_API_BASE
export const LIVE = RAW_BASE !== undefined && RAW_BASE !== ''
export const API_BASE: string = (RAW_BASE ?? '').replace(/\/$/, '')

export function mediaUrl(src: string): string {
  if (/^https?:/.test(src) || !LIVE) return src
  return API_BASE + src
}

async function fetchFeed(): Promise<Footage[]> {
  if (!LIVE) return fixture as Footage[]
  const r = await fetch(`${API_BASE}/feed`)
  if (!r.ok) throw new Error(`/feed ${r.status}`)
  return (await r.json()) as Footage[]
}

export function useFeed() {
  return useQuery({
    queryKey: ['feed'],
    queryFn: fetchFeed,
    staleTime: 30_000,
    refetchInterval: LIVE ? 60_000 : false,
  })
}

/** [lat, lon] for every camera in the catalog; dots on the globe. */
export type CameraPoint = [number, number]

async function fetchCameras(): Promise<CameraPoint[]> {
  if (!LIVE) return cameraFixture as CameraPoint[]
  const r = await fetch(`${API_BASE}/cameras.geojson`)
  if (!r.ok) throw new Error(`/cameras.geojson ${r.status}`)
  const gj = (await r.json()) as {
    features: { geometry: { coordinates: [number, number] } }[]
  }
  return gj.features.map((f) => [f.geometry.coordinates[1], f.geometry.coordinates[0]])
}

export function useCameras() {
  return useQuery({ queryKey: ['cameras'], queryFn: fetchCameras, staleTime: Infinity })
}

export type StreamState = 'fixture' | 'connecting' | 'open' | 'closed'

/** SSE /stream: any message means some event's footage changed → refetch /feed. */
export function useStream(): StreamState {
  const qc = useQueryClient()
  const [state, setState] = useState<StreamState>(LIVE ? 'connecting' : 'fixture')
  useEffect(() => {
    if (!LIVE) return
    const es = new EventSource(`${API_BASE}/stream`)
    es.onopen = () => setState('open')
    es.onerror = () => setState('closed')
    const onFootage = (e: MessageEvent<string>) => {
      const msg = JSON.parse(e.data) as StreamMessage
      void qc.invalidateQueries({ queryKey: ['feed'] })
      void qc.invalidateQueries({ queryKey: ['footage', msg.event_id] })
    }
    es.onmessage = onFootage
    es.addEventListener('footage', onFootage)
    es.addEventListener('refresh', () => void qc.invalidateQueries({ queryKey: ['feed'] }))
    return () => es.close()
  }, [qc])
  return state
}

export interface RefreshStatus {
  running: boolean
  finished?: string
  resolved?: number
  with_footage?: number
  error?: string
}

export interface Refresh {
  running: boolean
  /** seconds until the backend accepts another manual refresh (0 = now). */
  cooldown: number
  last?: RefreshStatus
  trigger: () => void
}

/** The Refresh button: `POST /refresh` starts a footage pass on the backend (same as the cron
 *  tick), `GET /refresh` is polled until it finishes, then /feed is refetched. Throttled
 *  server-side (429 + Retry-After) so a busy demo can't run up the VLM bill. */
export function useRefresh(): Refresh {
  const qc = useQueryClient()
  const [running, setRunning] = useState(false)
  const [cooldown, setCooldown] = useState(0)
  const [last, setLast] = useState<RefreshStatus>()
  const alive = useRef(true)

  useEffect(() => {
    if (cooldown <= 0) return
    const t = setTimeout(() => setCooldown((c) => Math.max(0, c - 1)), 1000)
    return () => clearTimeout(t)
  }, [cooldown])
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  const poll = useCallback(async () => {
    for (;;) {
      const r = await fetch(`${API_BASE}/refresh`)
      const s = (await r.json()) as RefreshStatus
      if (!alive.current) return
      if (!s.running) {
        setLast(s)
        setRunning(false)
        await qc.invalidateQueries({ queryKey: ['feed'] })
        return
      }
      await new Promise((res) => setTimeout(res, 2000))
    }
  }, [qc])

  const trigger = useCallback(() => {
    if (running || cooldown > 0) return
    if (!LIVE) {
      setRunning(true)
      void qc.refetchQueries({ queryKey: ['feed'] }).then(() => setRunning(false))
      return
    }
    setRunning(true)
    void (async () => {
      const r = await fetch(`${API_BASE}/refresh`, { method: 'POST' })
      if (r.status === 429) {
        setRunning(false)
        setCooldown(Number(r.headers.get('Retry-After') ?? 30))
        return
      }
      if (!r.ok) {
        setRunning(false)
        setLast({ running: false, error: `/refresh ${r.status}` })
        return
      }
      await poll()
    })()
  }, [running, cooldown, qc, poll])

  return { running, cooldown, last, trigger }
}
