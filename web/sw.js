/* Motra — service worker (offline shell + Web Push).
   IMPORTANT: bump CACHE on every front-end change, or installed clients keep the
   old shell (the precached index.html won't refresh until the SW reinstalls). */
const AI_NAME = "阿克";             // push-title fallback; keep in sync with index.html CONFIG.AI_NAME
const CACHE = "companion-v123";
const PRECACHE = [
  "./index.html",
  "./memory.html",
  "./fridge.html",
  "./cat.html",
  "./period.html",
  "./state.html",
  "./beach.webp",
  "./avatar-sea.png",
  "./sdv-skin.css",
  "./fonts/great-vibes.woff2",
  "./fonts/cormorant.woff2",
  "./assets/night/home/home-bg-starwheel.webp",
  "./assets/night/home/home-card-moonphase.webp",
  "./assets/night/home/home-card-back.webp",
  "./assets/night/sky-gold.webp",
  "./assets/night/sky-compass.webp",
  "./assets/night/sky-lake.webp",
  "./assets/night/sky-chart.webp",
  "./assets/night/gold-05.webp",
  "./assets/night/gold-06.webp",
  "./assets/night/gold-14.webp",
  "./assets/night/gold-15.webp",
  "./assets/night/gold-16.webp",
  "./assets/night/gold-17.webp",
  "./assets/sdv/frame_cat.png",
  "./assets/sdv/deco_cat.png",
  "./assets/sdv/sparkle_s.png",
  "./assets/sdv/frame_letter.png",
  "./assets/sdv/paper_letter.png",
  "./assets/sdv/paper_letter_frame.png",
  "./fonts/fusion-pixel-zh.woff2",
  "./assets/sdv/bg_title.png",
  "./assets/sdv/frame_card.png",
  "./assets/sdv/frame_calendar.png",
  "./assets/sdv/frame_input.png",
  "./assets/sdv/frame_banner.png",
  "./assets/sdv/bubble_b.png",
  "./assets/sdv/bubble_b_r.png",
  "./assets/sdv/bubble_tail.png",
  "./assets/sdv/avatar_frame.png",
  "./assets/sdv/deco_cat.png",
  /* ── 美化素材(用户抠图) ── */
  "./assets/sdv/frame_photo.png",
  "./assets/sdv/frame_cat.png",
  "./assets/sdv/frame_wood.png",
  "./assets/sdv/bar_input.png",
  "./assets/sdv/paper_letter.png",
  "./assets/sdv/photo_scene.png",
  "./assets/sdv/deco_cake.png",
  "./assets/sdv/splash_cats.png",
  "./assets/sdv/icon_chat.png",
  "./assets/sdv/icon_room.png",
  "./assets/sdv/icon_period.png",
  "./assets/sdv/icon_cat.png",
  "./assets/sdv/icon_fridge.png",
  "./assets/sdv/icon_memory.png",
  "./assets/sdv/icon_state.png",
  "./assets/sdv/icon_settings.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE)
      .then((c) => c.addAll(PRECACHE))
      .then(() => self.skipWaiting())
      .catch(() => self.skipWaiting())
  );
});
self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith("/relay/")) return;          // never intercept the API / SSE
  if (e.request.mode === "navigate") {
    // network-first for the page → an online reload always gets the latest index.html
    e.respondWith(fetch(e.request, { cache: "reload" }).catch(() => caches.match("./index.html")));
    return;
  }
  if (e.request.method === "GET" && url.origin === location.origin) {
    e.respondWith(
      caches.match(e.request).then((r) => {
        if (r) return r;
        return fetch(e.request).then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
          return res;
        });
      })
    );
  }
});

// ── Web Push (VAPID) ──────────────────────────────
// The relay sends a push when the AI replies and no PWA tab is holding the stream;
// here we surface it on the lock screen.
self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; }
  catch (_) { d = { body: (e.data && e.data.text && e.data.text()) || "" }; }
  const title = d.title || AI_NAME;                        // backend sends RELAY_AI_NAME as title
  const body  = d.body  || "你有一条新消息";
  const tag   = d.id ? ("companion-" + d.id) : "companion-msg";
  e.waitUntil(
    self.registration.showNotification(title, {
      body,
      tag,
      renotify: true,
      icon:  "./icon-192.png",
      badge: "./icon-192.png",
      vibrate: [80, 40, 80],
      data: { url: d.url || "./" },
    })
  );
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const target = (e.notification.data && e.notification.data.url) || "./";
  e.waitUntil(
    // matchAll only returns clients this SW controls (our own scope), so focus the first one.
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((cls) => {
      for (const c of cls) {
        if ("focus" in c){ c.postMessage({ type: "backfill" }); return c.focus(); }
      }
      return self.clients.openWindow ? self.clients.openWindow(target) : null;
    })
  );
});
