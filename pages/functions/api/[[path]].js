// Proxy /api/* na Python backend. Prehliadač tak komunikuje len s doménou Pages (rovnaký pôvod,
// cookies aj CSRF fungujú bez CORS) a backend prijíma požiadavky iba s tajomstvom tejto proxy.

const CONFIG_PATH = "/api/config";
const CONFIG_FRESH_MS = 5 * 60 * 1000;          // potom sa skúsi obnoviť z backendu
const CONFIG_STALE_MS = 24 * 60 * 60 * 1000;    // pri výpadku backendu sa ešte servíruje stará kópia
const CONFIG_CACHE_CONTROL = "public, max-age=300, stale-while-revalidate=86400, stale-if-error=86400";
const DEFAULT_RETRY_AFTER = 30;

// Kópia configu v rámci izolátu; Cache API navyše zdieľa kópiu medzi izolátmi v dátovom centre
// (na *.pages.dev môže byť no-op, preto obe vrstvy).
let configMemo = null;   // { body, storedAt }

function jsonError(status, error, retryAfter) {
  const headers = { "Cache-Control": "no-store" };
  if (retryAfter) headers["Retry-After"] = String(retryAfter);
  return Response.json({ error, retry_after: retryAfter || undefined }, { status, headers });
}

function retryAfterSeconds(response) {
  const value = response.headers.get("Retry-After");
  if (!value) return null;
  const seconds = /^\d+$/.test(value) ? Number(value) : Math.ceil((Date.parse(value) - Date.now()) / 1000);
  return Number.isFinite(seconds) && seconds > 0 ? seconds : null;
}

// Chyby z backendu sú JSON; text/HTML chyby od hostingu (napr. Render „Too Many Requests“ alebo
// stránka s výzvou) prevedieme na JSON, aby ich frontend vedel zobraziť zrozumiteľne.
function normalizeError(response) {
  const type = response.headers.get("Content-Type") || "";
  if (response.ok || type.includes("application/json")) return null;
  if (response.status === 429) {
    const retry = retryAfterSeconds(response) || DEFAULT_RETRY_AFTER;
    return jsonError(429, "Server je momentálne preťažený (príliš veľa požiadaviek). Skús to o chvíľu znova.", retry);
  }
  if (response.status >= 500) {
    return jsonError(502, "Backend je dočasne nedostupný, skús to o chvíľu.", retryAfterSeconds(response) || DEFAULT_RETRY_AFTER);
  }
  return jsonError(response.status, `Požiadavka zlyhala (HTTP ${response.status}).`);
}

function configResponse(body, source) {
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "application/json", "Cache-Control": CONFIG_CACHE_CONTROL, "X-Config-Source": source },
  });
}

async function forward(request, env) {
  const incoming = new URL(request.url);
  const target = new URL(incoming.pathname + incoming.search, env.BACKEND_URL);
  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.set("X-Proxy-Secret", env.PROXY_SECRET || "");
  headers.set("X-Client-IP", request.headers.get("CF-Connecting-IP") || "");
  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  return fetch(target, {
    method: request.method,
    headers,
    body: hasBody ? request.body : undefined,
    redirect: "manual",
  });
}

async function serveConfig(request, env, waitUntil) {
  const cacheKey = new Request(new URL(CONFIG_PATH, request.url).toString());
  const cache = typeof caches !== "undefined" ? caches.default : null;

  if (!configMemo && cache) {
    const hit = await cache.match(cacheKey).catch(() => null);
    if (hit) configMemo = { body: await hit.text(), storedAt: Number(hit.headers.get("X-Stored-At")) || 0 };
  }
  const age = configMemo ? Date.now() - configMemo.storedAt : Infinity;
  if (age < CONFIG_FRESH_MS) return configResponse(configMemo.body, "cache");

  let upstream;
  try {
    upstream = await forward(new Request(request.url, { method: "GET", headers: request.headers }), env);
  } catch {
    upstream = null;
  }
  const type = upstream?.headers.get("Content-Type") || "";
  if (upstream?.ok && type.includes("application/json")) {
    const body = await upstream.text();
    configMemo = { body, storedAt: Date.now() };
    if (cache) {
      const stored = new Response(body, {
        headers: { "Content-Type": "application/json", "Cache-Control": "public, max-age=86400", "X-Stored-At": String(configMemo.storedAt) },
      });
      waitUntil(cache.put(cacheKey, stored).catch(() => {}));
    }
    return configResponse(body, "backend");
  }
  // Backend neodpovedá alebo nás obmedzuje: radšej mierne stará konfigurácia než chyba.
  if (configMemo && age < CONFIG_STALE_MS) return configResponse(configMemo.body, "stale");
  if (!upstream) return jsonError(502, "Backend je nedostupný, skús to o chvíľu.", DEFAULT_RETRY_AFTER);
  return normalizeError(upstream) || jsonError(502, "Neplatná odpoveď backendu.", DEFAULT_RETRY_AFTER);
}

export async function onRequest({ request, env, waitUntil }) {
  if (!env.BACKEND_URL) {
    return jsonError(500, "BACKEND_URL nie je nastavené.");
  }
  if (request.method === "GET" && new URL(request.url).pathname === CONFIG_PATH) {
    return serveConfig(request, env, waitUntil);
  }

  let response;
  try {
    response = await forward(request, env);
  } catch {
    return jsonError(502, "Backend je nedostupný, skús to o chvíľu.", DEFAULT_RETRY_AFTER);
  }
  return normalizeError(response) || new Response(response.body, response);
}
