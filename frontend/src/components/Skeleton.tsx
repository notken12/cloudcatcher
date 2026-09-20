/** Content-shaped placeholder for the hero card while /feed is in flight. */
export function SkeletonCard() {
  return (
    <div className="flex w-full flex-col gap-4" aria-busy aria-label="Loading">
      <div className="card skeleton aspect-video overflow-hidden" />
      <div className="flex flex-col gap-2 px-1">
        <div className="flex items-center gap-3">
          <div className="skeleton h-5 w-20 rounded-full" />
          <div className="skeleton h-5 w-48" />
        </div>
        <div className="skeleton h-4 w-3/4" />
        <div className="skeleton h-3 w-1/2" />
      </div>
    </div>
  )
}
