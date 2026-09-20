import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import type { Footage, StreamMessage } from './types'
import fixture from '../fixtures/feed.json'
import cameraFixture from '../fixtures/cameras.json'

/** Unset → fixture mode (fully offline demo). Set → e.g. "http://localhost:8000". */
export const API_BASE: string | undefined = import.meta.env.VITE_API_BASE
export const LIVE = Boolean(API_BASE)

export function mediaUrl(src: string): string {
  if (/^https?:/.test(src) || !API_BASE) return src
  return API_BASE.replace(/\/$/, '') + src
}

async function fetchFeed(): Promise<Footage[]> {
  if (!API_BASE) return fixture as Footage[]
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
  if (!API_BASE) return cameraFixture as CameraPoint[]
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
    if (!API_BASE) return
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
    return () => es.close()
  }, [qc])
  return state
}
