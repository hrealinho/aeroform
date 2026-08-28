// Browser requests go to the public URL. Server components run inside the container and
// must use the internal service address instead: in Docker, `localhost:8000` resolves to
// the web container itself, so a server-side fetch to the public URL never reaches the
// API and the page silently rendered zeros.
const PUBLIC_API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";
const SERVER_API = process.env.API_URL_INTERNAL || PUBLIC_API;

const API = PUBLIC_API;

/** Base URL correct for the current execution context. */
export function apiBase(): string {
  return typeof window === "undefined" ? SERVER_API : PUBLIC_API;
}

export async function getJSON<T>(path: string): Promise<T> {
  const r = await fetch(apiBase() + path, { cache: "no-store" });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

export { API, PUBLIC_API, SERVER_API };
