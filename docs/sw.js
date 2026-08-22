/* Service Worker — damit die Seite als App installierbar ist und offline
 * wenigstens das zuletzt Gesehene zeigt.
 *
 * Zwei Strategien, und die Trennung ist wichtig:
 *
 *   Gerüst (HTML, CSS, JS, Symbole)  →  aus dem Zwischenspeicher, im
 *   Hintergrund erneuert. Es ändert sich selten.
 *
 *   Daten (data/*.json)  →  immer zuerst aus dem Netz. Eine Kursanalyse aus
 *   dem Zwischenspeicher wäre schlimmer als keine: sie sähe aktuell aus.
 *   Nur wenn das Netz nicht antwortet, kommt die letzte bekannte Fassung —
 *   und die Seite zeigt am Kopf, von wann sie ist.
 */
const VERSION = 'v1';
const GERUEST = 'geruest-' + VERSION;
const DATEN = 'daten-' + VERSION;

const SCHALE = [
  './',
  'index.html',
  'style.css',
  'app.js',
  'manifest.json',
  'icon.svg',
  'icon-192.png',
  'icon-512.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(GERUEST)
      .then((c) => c.addAll(SCHALE))
      .then(() => self.skipWaiting())
      .catch(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((namen) => Promise.all(
        namen.filter((n) => n !== GERUEST && n !== DATEN)
             .map((n) => caches.delete(n))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const anfrage = e.request;
  if (anfrage.method !== 'GET') return;

  const url = new URL(anfrage.url);
  // Fremde Server (Schriften, ntfy) laufen unberührt durch.
  if (url.origin !== self.location.origin) return;

  if (url.pathname.includes('/data/')) {
    e.respondWith(netzZuerst(anfrage));
  } else {
    e.respondWith(speicherZuerst(anfrage));
  }
});

async function netzZuerst(anfrage) {
  try {
    const antwort = await fetch(anfrage);
    if (antwort.ok) {
      const speicher = await caches.open(DATEN);
      // Der Cache-Buster in der Adresse würde sonst jede Abfrage neu ablegen.
      speicher.put(ohneAbfrage(anfrage), antwort.clone());
    }
    return antwort;
  } catch (fehler) {
    const gemerkt = await caches.match(ohneAbfrage(anfrage));
    if (gemerkt) return gemerkt;
    return new Response('null', {
      status: 503,
      headers: { 'Content-Type': 'application/json' },
    });
  }
}

async function speicherZuerst(anfrage) {
  const gemerkt = await caches.match(anfrage, { ignoreSearch: true });
  const ausDemNetz = fetch(anfrage).then((antwort) => {
    if (antwort.ok) {
      caches.open(GERUEST).then((c) => c.put(anfrage, antwort.clone()));
    }
    return antwort;
  }).catch(() => gemerkt);
  return gemerkt || ausDemNetz;
}

function ohneAbfrage(anfrage) {
  const url = new URL(anfrage.url);
  url.search = '';
  return url.toString();
}
