// Nahoko app shell: nothing is cached (the site is always fresh); with no network a page gets a small "offline" screen instead of the browser error.
self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (e) { e.waitUntil(self.clients.claim()); });
self.addEventListener("fetch", function (e) {
  if (e.request.mode !== "navigate") return;
  e.respondWith(fetch(e.request).catch(function () {
    return new Response('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Nahoko</title>' +
      '<body style="margin:0;height:100vh;display:grid;place-items:center;background:#141414;color:#a3a3a3;font:16px system-ui,sans-serif;text-align:center">' +
      '<div><div style="width:64px;height:64px;margin:0 auto 18px;border-radius:16px;background:linear-gradient(135deg,#ff3b3b,#c10f1d);color:#fff;font:800 36px/64px system-ui">N</div>' +
      'No connection.<br><button onclick="location.reload()" style="margin-top:16px;padding:10px 18px;border:0;border-radius:10px;background:#e50914;color:#fff;font:600 15px system-ui">Try again</button></div>',
      {headers: {"Content-Type": "text/html; charset=utf-8"}});
  }));
});
