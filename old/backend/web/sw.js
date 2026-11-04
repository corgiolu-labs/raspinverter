/* Service Worker - Inverter Dashboard - OTTIMIZZATO */
const VERSION = '1.1.0';                              // incrementa per forzare update
const ASSETS_VERSION = '20251128';                    // bust cache per CSS/JS
const APP_CACHE     = `app-shell-${VERSION}`;
const RUNTIME_CACHE = `runtime-${VERSION}`;
const OFFLINE_URL   = '/offline.html';

// Cache strategy ottimizzata
const CACHE_STRATEGIES = {
  APP_SHELL: 'cache-first',      // App shell sempre dalla cache
  STATIC: 'stale-while-revalidate', // Statici: cache + aggiornamento background
  API: 'network-first',          // API: network + fallback cache
  HTML: 'network-first'          // HTML: network + fallback cache
};

const APP_SHELL = [
  './index.html',
  `./main.css?v=${ASSETS_VERSION}`,
  `./app.mod.js?v=${ASSETS_VERSION}`,
  `./settings.mod.js?v=${ASSETS_VERSION}`,
  './settings.html',
  './analysis_dashboard.html',
  './manifest.webmanifest',
  './offline.html'
];

// Cache di runtime per dati dinamici
const RUNTIME_CACHE_PATTERNS = [
  /\/api\/inverter/,
  /\/api\/history/,
  /\/api\/energy/,
  /\/api\/totals/
];

// Install: precache dell'app shell con compressione
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(APP_CACHE).then((cache) => {
      return Promise.allSettled(APP_SHELL.map(url => 
        cache.add(url).catch(err => {
          return null;
        })
      ));
    }).then((results) => {
      const successCount = results.filter(r => r.status === 'fulfilled' && r.value !== null).length;
      return self.skipWaiting();
    }).catch((error) => {
      return self.skipWaiting();
    })
  );
});

// Activate: pulizia cache vecchie con cleanup intelligente
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      const cleanupPromises = keys.map((key) => {
        if (key !== APP_CACHE && key !== RUNTIME_CACHE) {
          return caches.delete(key);
        }
      });
      
      // Cleanup cache runtime se troppo grande
      return Promise.all(cleanupPromises).then(() => {
        return cleanupRuntimeCache();
      });
    }).then(() => {
      return self.clients.claim();
    })
  );
});

// Cleanup intelligente cache runtime
async function cleanupRuntimeCache() {
  try {
    const cache = await caches.open(RUNTIME_CACHE);
    const keys = await cache.keys();
    
    // Se cache runtime > 50MB, rimuovi elementi più vecchi
    if (keys.length > 100) {
      const sortedKeys = keys.sort((a, b) => {
        const aTime = a.headers.get('date') || 0;
        const bTime = b.headers.get('date') || 0;
        return new Date(aTime) - new Date(bTime);
      });
      
      // Rimuovi 20% degli elementi più vecchi
      const toDelete = sortedKeys.slice(0, Math.floor(keys.length * 0.2));
      await Promise.all(toDelete.map(key => cache.delete(key)));
    }
      } catch (error) {
      // Runtime cache cleanup failed - log error for debugging
      console.warn('[SW] Runtime cache cleanup failed:', error);
    }
}

// Fetch strategy ottimizzata con fallback intelligenti
self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // Gestisci solo stesso origin
  if (url.origin !== self.location.origin) return;

  const accept = req.headers.get('accept') || '';
  const isNavigation = req.mode === 'navigate' || accept.includes('text/html');
  const isAPI = url.pathname.startsWith('/api/');
  const isStatic = !isNavigation && !isAPI;

  // Strategia per navigazioni HTML
  if (isNavigation) {
    event.respondWith(handleHTMLRequest(req));
    return;
  }

  // Strategia per API
  if (isAPI) {
    event.respondWith(handleAPIRequest(req));
    return;
  }

  // Strategia per statici
  if (isStatic) {
    event.respondWith(handleStaticRequest(req));
    return;
  }
});

// Gestione richieste HTML con fallback intelligente
async function handleHTMLRequest(req) {
  try {
    // Network first per HTML
    const networkResponse = await fetch(req);
    
    // Aggiorna cache se risposta valida e metodo GET (non supporta POST/PUT/DELETE)
    if (networkResponse.ok && req.method === 'GET') {
      const cache = await caches.open(APP_CACHE);
      cache.put(req, networkResponse.clone());
    }
    
    return networkResponse;
  } catch (error) {
    console.log('[SW] HTML network failed, trying cache:', req.url);
    
    // Fallback a cache
    const cachedResponse = await caches.match(req);
    if (cachedResponse) {
      return cachedResponse;
    }
    
    // Fallback finale a offline page
    return caches.match(OFFLINE_URL);
  }
}

// Gestione richieste API con cache intelligente
async function handleAPIRequest(req) {
  // DEBUG: Log della richiesta per capire cosa sta succedendo
  console.log('[SW] handleAPIRequest chiamato per:', req.method, req.url);
  
  try {
    // Network first per API
    const networkResponse = await fetch(req);
    
    // Cache solo risposte GET valide (non supporta POST/PUT/DELETE)
    if (networkResponse.ok && req.method === 'GET') {
      console.log('[SW] Mettendo in cache richiesta GET:', req.url);
      const cache = await caches.open(RUNTIME_CACHE);
      
      // Clona risposta per cache
      const responseToCache = networkResponse.clone();
      
      // Aggiungi timestamp per cleanup
      const headers = new Headers(responseToCache.headers);
      headers.set('date', new Date().toISOString());
      
      const cachedResponse = new Response(responseToCache.body, {
        status: responseToCache.status,
        statusText: responseToCache.statusText,
        headers: headers
      });
      
      // CONTROLLO EXTRA: Verifica che sia effettivamente GET
      if (req.method === 'GET') {
        cache.put(req, cachedResponse);
        console.log('[SW] Cache PUT eseguito per GET:', req.url);
      } else {
        console.log('[SW] ERRORE: Tentativo di cache PUT per metodo non-GET:', req.method, req.url);
      }
    } else {
      console.log('[SW] NON mettendo in cache richiesta:', req.method, req.url);
    }
    
    return networkResponse;
  } catch (error) {
    console.log('[SW] API network failed, trying cache:', req.url);
    
    // Fallback a cache se disponibile
    const cachedResponse = await caches.match(req);
    if (cachedResponse) {
      return cachedResponse;
    }
    
    // Fallback a risposta offline per API
    return new Response(JSON.stringify({ 
      error: 'offline',
      message: 'Servizio non disponibile offline',
      timestamp: new Date().toISOString()
    }), {
      status: 503,
      statusText: 'Service Unavailable',
      headers: { 
        'Content-Type': 'application/json',
        'Cache-Control': 'no-cache'
      }
    });
  }
}

// Gestione richieste statiche con stale-while-revalidate
async function handleStaticRequest(req) {
  const cache = await caches.open(APP_CACHE);
  const cachedResponse = await cache.match(req);
  
  // Ritorna subito dalla cache se disponibile
  if (cachedResponse) {
    // Aggiorna cache in background solo per GET
    if (req.method === 'GET') {
      fetch(req).then((networkResponse) => {
        if (networkResponse.ok) {
          cache.put(req, networkResponse);
        }
      }).catch(() => {
        // Ignora errori di aggiornamento background
      });
    }
    
    return cachedResponse;
  }
  
  // Se non in cache, prova network
  try {
    const networkResponse = await fetch(req);
    if (networkResponse.ok && req.method === 'GET') {
      cache.put(req, networkResponse.clone());
    }
    return networkResponse;
  } catch (error) {
    // Fallback a risposta generica per statici
    return new Response('Resource not available offline', {
      status: 404,
      statusText: 'Not Found',
      headers: { 'Content-Type': 'text/plain' }
    });
  }
}

// Gestione messaggi per comunicazione con app
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
  
  if (event.data && event.data.type === 'GET_VERSION') {
    event.ports[0].postMessage({ version: VERSION });
  }
  
  if (event.data && event.data.type === 'CLEAR_CACHE') {
    event.waitUntil(
      caches.keys().then((keys) => {
        return Promise.all(keys.map(key => caches.delete(key)));
      }).then(() => {
        event.ports[0].postMessage({ success: true });
      })
    );
  }
  
  // FORZA AGGIORNAMENTO IMMEDIATO
  if (event.data && event.data.type === 'FORCE_UPDATE') {
    console.log('[SW] Forzando aggiornamento...');
    self.skipWaiting();
    event.ports[0].postMessage({ success: true });
  }
});

// Gestione errori globali
self.addEventListener('error', (event) => {
  console.error('[SW] Global error:', event.error);
});

self.addEventListener('unhandledrejection', (event) => {
  console.error('[SW] Unhandled rejection:', event.reason);
});

// Background sync per aggiornamenti offline
self.addEventListener('sync', (event) => {
  if (event.tag === 'background-sync') {
    event.waitUntil(backgroundSync());
  }
});

// Funzione background sync
async function backgroundSync() {
  try {
    // Sincronizza dati offline quando connessione ripristinata
    const clients = await self.clients.matchAll();
    clients.forEach(client => {
      client.postMessage({
        type: 'BACKGROUND_SYNC',
        timestamp: new Date().toISOString()
      });
    });
  } catch (error) {
    console.warn('[SW] Background sync failed:', error);
  }
}

// Gestione push notifications (se implementate in futuro)
self.addEventListener('push', (event) => {
  if (event.data) {
    const data = event.data.json();
    
    event.waitUntil(
      self.registration.showNotification(data.title || 'Inverter Dashboard', {
        body: data.body || 'Nuova notifica',
        icon: '/icons/icon-192.png',
        badge: '/icons/icon-72.png',
        data: data
      })
    );
  }
});

// Gestione click su notifiche
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  
  event.waitUntil(
    self.clients.matchAll().then((clients) => {
      if (clients.length > 0) {
        // Focus su client esistente
        clients[0].focus();
      } else {
        // Apri nuovo client
        self.clients.openWindow('/');
      }
    })
  );
});
