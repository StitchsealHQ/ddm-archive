// 동대문 아카이브 서비스 워커.
//
// 전략: 같은 출처 GET은 네트워크 우선, 실패하면 캐시.
// 자료(data.json)가 매일 갱신되고 화면(index.html)도 자주 고치므로 캐시 우선을
// 쓰면 옛 내용이 남는다. 온라인이면 항상 최신, 오프라인이면 마지막으로 본 내용.
// 외부 출처(폰트 CDN, 기사 썸네일)는 가로채지 않고 그대로 통과시킨다.
//
// 배포 파일을 바꿔도 VERSION을 올릴 필요는 없다 (네트워크 우선이라 자동 반영).
// 캐시 구조 자체를 바꿀 때만 올린다.
// v2: 아이콘 교체(청·적·황) — 옛 아이콘 캐시를 확실히 비우기 위해 올림
const VERSION = 'v2';
const CACHE = `ddm-archive-${VERSION}`;
const SHELL = [
  './', 'index.html', 'guide.html', 'manifest.webmanifest',
  'icons/icon-192.png', 'icons/icon-512.png', 'icons/icon-maskable-512.png',
  'icons/apple-touch-icon.png', 'icons/favicon-32.png',
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => c.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  if (new URL(req.url).origin !== location.origin) return;

  e.respondWith(
    fetch(req)
      .then(res => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then(c => c.put(req, copy));
        }
        return res;
      })
      .catch(() => caches.match(req).then(hit => {
        if (hit) return hit;
        // 캐시에 없는 화면 요청이면 첫 화면으로 (오프라인에서 딥링크를 열었을 때)
        if (req.mode === 'navigate') return caches.match('./');
        return Response.error();
      }))
  );
});
