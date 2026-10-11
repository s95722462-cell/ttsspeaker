// TTS 스피커 서비스 워커 — 올릴 때마다 CACHE 번호를 올림
const CACHE = "ttsspk-v7";
const FILES = ["./", "index.html", "manifest.webmanifest", "icon-192.png", "icon-512.png"];
self.addEventListener("install", e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(FILES)).then(() => self.skipWaiting())); });
self.addEventListener("activate", e => { e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())); });
self.addEventListener("fetch", e => {                 // 같은 주소의 파일만: 인터넷 먼저, 안 되면 저장본
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.origin !== location.origin) return;
  e.respondWith(fetch(u.href, { cache: "no-cache" }).then(r => {   // no-cache: 브라우저 저장본(최대 10분) 대신 늘 새 화면 확인
    if (r.ok) { const c = r.clone(); caches.open(CACHE).then(ca => ca.put(e.request, c)); }
    return r;
  }).catch(() => caches.match(e.request, { ignoreSearch: true }).then(r => r || caches.match("index.html"))));
});
