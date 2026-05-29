// Service Worker — Lowly People Tracker
// Gerencia Push Notifications de mortes PvP

self.addEventListener('push', function(event) {
  if (!event.data) return;

  var data = {};
  try { data = event.data.json(); } catch(e) { data = { title: 'Lowly People', body: event.data.text() }; }

  var title   = data.title || 'Lowly People';
  var options = {
    body:    data.body || '',
    icon:    data.icon || '/icon-192.png',
    badge:   '/icon-72.png',
    tag:     data.tag || 'death-notification',
    data:    data.url ? { url: data.url } : {},
    vibrate: [200, 100, 200],
    requireInteraction: false,
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', function(event) {
  event.notification.close();
  var url = (event.notification.data && event.notification.data.url) || 'https://amon-ot-tracker.vercel.app';
  event.waitUntil(clients.openWindow(url));
});

self.addEventListener('install', function(event) {
  self.skipWaiting();
});

self.addEventListener('activate', function(event) {
  event.waitUntil(clients.claim());
});
