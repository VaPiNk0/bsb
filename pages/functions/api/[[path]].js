// Proxy /api/* na Python backend. Prehliadač tak komunikuje len s doménou Pages (rovnaký pôvod,
// cookies aj CSRF fungujú bez CORS) a backend prijíma požiadavky iba s tajomstvom tejto proxy.
export async function onRequest({ request, env }) {
  if (!env.BACKEND_URL) {
    return Response.json({ error: "BACKEND_URL nie je nastavené." }, { status: 500 });
  }
  const incoming = new URL(request.url);
  const target = new URL(incoming.pathname + incoming.search, env.BACKEND_URL);

  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.set("X-Proxy-Secret", env.PROXY_SECRET || "");
  headers.set("X-Client-IP", request.headers.get("CF-Connecting-IP") || "");

  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  let response;
  try {
    response = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? request.body : undefined,
      redirect: "manual",
    });
  } catch {
    return Response.json({ error: "Backend je nedostupný, skús to o chvíľu." }, { status: 502 });
  }
  return new Response(response.body, response);
}
