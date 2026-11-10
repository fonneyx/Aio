// Minimal service worker so Aio can be installed as an app (and appear in the phone's Share menu).
// Everything goes to the network: downloads and job status must always be live.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
