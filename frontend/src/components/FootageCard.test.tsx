import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import feed from '../fixtures/feed.json'
import type { Footage } from '../lib/types'
import { FootageCard } from './FootageCard'

const rows = feed as Footage[]

describe('FootageCard', () => {
  it('renders an image frame with honest timing, never the bare word "live"', () => {
    const f = rows.find((r) => r.media.kind === 'image')!
    render(<FootageCard footage={f} />)
    expect(screen.getByRole('img')).toHaveAttribute('src', expect.stringContaining(f.media.src))
    expect(screen.getByText(/^frame \d\d:\d\d UTC/)).toBeInTheDocument()
    expect(screen.getByText(f.event.place!)).toBeInTheDocument()
    expect(screen.queryByText(/^live$/)).not.toBeInTheDocument()
  })

  it('shows a delay hint for streams and a poster for iframe-only sources', () => {
    const f = rows.find((r) => r.media.kind === 'iframe')!
    render(<FootageCard footage={f} />)
    expect(screen.getByText(/stream · delay ≈/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /open/i })).toBeInTheDocument()
    expect(screen.queryByText('verified')).not.toBeInTheDocument()
  })
})
