// খবরের খাতা -- Service Worker
//
// লক্ষ্য: শুধু অ্যাপ শেল (HTML/আইকন) অফলাইনে কাজ করবে, যাতে ইন্টারনেট
// না থাকলেও অ্যাপটা খোলে এবং একটা "ইন্টারনেট নেই" জাতীয় অবস্থা দেখানো
// যায়। প্রকৃত খবরের ডেটা (news.json) কখনো ক্যাশ থেকে দেখানো হবে না --
// এটা প্রতিদিন বদলায়, তাই সবসময় নেটওয়ার্ক থেকেই আনা হবে। নেটওয়ার্ক না
// থাকলে news.json fetch ব্যর্থ হবে এবং index.html-এর নিজস্ব এরর-হ্যান্ডলিং
// (see fetch handler) সেটা সামলাবে।

const CACHE_VERSION = 'khoborer-khata-shell-v1';

// অ্যাপ শেলের অংশ হিসেবে যা ক্যাশ করা হবে -- news.json ইচ্ছাকৃতভাবে বাদ।
const SHELL_ASSETS = [
  './',
  './index.html',
  './manifest.json',
  './fonts.css',
  './fonts/tiro-bangla-400-normal.woff2',
  './fonts/tiro-bangla-400-italic.woff2',
  './icons/icon-72.png',
  './icons/icon-96.png',
  './icons/icon-128.png',
  './icons/icon-144.png',
  './icons/icon-152.png',
  './icons/icon-192.png',
  './icons/icon-384.png',
  './icons/icon-512.png',
  './icons/icon-maskable-192.png',
  './icons/icon-maskable-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) => cache.addAll(SHELL_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key !== CACHE_VERSION)
          .map((key) => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // news.json (এবং তার cache-busting query সহ ভ্যারিয়েন্ট) কখনো ক্যাশ
  // থেকে সার্ভ করা হবে না -- সবসময় নেটওয়ার্কে যেতে হবে, যাতে খবর
  // সবসময় টাটকা থাকে।
  if (url.pathname.endsWith('/news.json')) {
    event.respondWith(fetch(event.request));
    return;
  }

  // শুধু নিজের origin-এর GET রিকোয়েস্ট হ্যান্ডল করা হবে (ফন্ট বা অন্য
  // থার্ড-পার্টি রিসোর্স ছুঁয়ে দেখা হবে না)।
  if (event.request.method !== 'GET' || url.origin !== self.location.origin) {
    return;
  }

  // অ্যাপ শেলের জন্য cache-first, নেটওয়ার্কে ব্যর্থ হলে ক্যাশে ফলব্যাক।
  event.respondWith(
    caches.match(event.request).then((cached) => {
      if (cached) return cached;
      return fetch(event.request)
        .then((response) => {
          if (response && response.ok) {
            const clone = response.clone();
            caches.open(CACHE_VERSION).then((cache) => cache.put(event.request, clone));
          }
          return response;
        })
        .catch(() => caches.match('./index.html'));
    })
  );
});
