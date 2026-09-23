self.addEventListener('install', event => {
  console.log('Service Worker installing.');
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  console.log('Service Worker activating.');
  event.waitUntil(self.clients.claim());
});

// A fetch event handler is REQUIRED by Chrome/Android to trigger the beforeinstallprompt event
self.addEventListener('fetch', event => {
  // Only intercept GET requests
  if (event.request.method !== 'GET') return;

  event.respondWith(
    fetch(event.request).catch(error => {
      // For navigation requests (page loads), return an offline message
      if (event.request.mode === 'navigate') {
        return new Response("You are offline.", {
          status: 503,
          statusText: "Service Unavailable",
          headers: { 'Content-Type': 'text/plain' }
        });
      }
      // For API calls and static assets, propagate the network error so fetch/axios catches it
      throw error;
    })
  );
});

self.addEventListener('push', function(event) {
  if (!event.data) return;
  let data;
  try { data = event.data.json(); } catch (e) { data = { title: 'servicesBills', body: event.data.text() }; }
  const options = {
    body: data.body,
    icon: '/logo192.png',
    badge: '/logo192.png',
    vibrate: [100, 50, 100],
    data: {
      dateOfArrival: Date.now(),
      url: data.url || '/',
      conversationId: data.conversation_id || null
    }
  };
  // Same tag -> the new notification replaces the old one (one per WhatsApp
  // conversation) and renotify makes it buzz again instead of updating silently.
  if (data.tag) {
    options.tag = data.tag;
    options.renotify = true;
  }
  event.waitUntil(self.registration.showNotification(data.title || 'servicesBills', options));
});

self.addEventListener('notificationclick', function(event) {
  event.notification.close();
  const data = event.notification.data || {};
  const urlToOpen = new URL(data.url || '/', self.location.origin).href;

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
      // Reuse any open window of this app: tell it where to go (App.js listens)
      // instead of requiring an exact URL match like before.
      const client = windowClients.find(c => new URL(c.url).origin === self.location.origin);
      if (client) {
        client.postMessage({ type: 'open-url', url: urlToOpen, conversationId: data.conversationId || null });
        return client.focus();
      }
      return clients.openWindow(urlToOpen);
    })
  );
});
