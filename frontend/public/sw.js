/* Push-only service worker: no caching, so the live view is never stale. */

self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()))

self.addEventListener('push', (e) => {
  let data = {}
  try {
    data = e.data ? e.data.json() : {}
  } catch {
    data = { body: e.data ? e.data.text() : '' }
  }
  const title = data.title || 'sunroof'
  const options = {
    body: data.body || 'Something beautiful is happening in the sky right now.',
    icon: '/icons/icon-192.png',
    badge: '/icons/icon-192.png',
    image: data.image || undefined,
    tag: data.tag || 'sunroof',
    data: { url: data.url || '/' },
  }
  e.waitUntil(self.registration.showNotification(title, options))
})

self.addEventListener('notificationclick', (e) => {
  e.notification.close()
  const url = new URL((e.notification.data && e.notification.data.url) || '/', self.location.origin)
    .href
  e.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
      const open = list.find((c) => 'focus' in c)
      if (open) return open.navigate(url).then((c) => (c || open).focus())
      return self.clients.openWindow(url)
    }),
  )
})
