// Backend reachability for the UI (Day 9B). The backend runs on a free host that sleeps after about
// 15 minutes idle and takes 30-45 s to start again. Every browser call to it goes through
// backendFetch(), which (1) reports when a call has been pending long enough that the server is
// probably starting, so the page can say so instead of looking frozen, and (2) retries reads that
// fail while the server is starting. Writes (POST) are never retried automatically.

type Listener = (waiting: boolean) => void;

const SLOW_MS = 2500;                 // after this, tell the user the server is starting
const READ_ATTEMPTS = 4;              // a read is retried on network errors and 502/503/504
const RETRY_PAUSE_MS = 3000;

let pendingSlow = 0;
const listeners = new Set<Listener>();
const emit = () => listeners.forEach((l) => l(pendingSlow > 0));

export function onBackendWaiting(l: Listener): () => void {
  listeners.add(l);
  l(pendingSlow > 0);
  return () => listeners.delete(l);
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const RETRYABLE = new Set([502, 503, 504]);

export async function backendFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const read = !init.method || init.method === "GET";
  let slow = false;
  const timer = setTimeout(() => { slow = true; pendingSlow += 1; emit(); }, SLOW_MS);
  try {
    for (let attempt = 1; ; attempt++) {
      try {
        const res = await fetch(url, init);
        if (read && RETRYABLE.has(res.status) && attempt < READ_ATTEMPTS) { await sleep(RETRY_PAUSE_MS); continue; }
        return res;
      } catch (err) {
        if (!read || attempt >= READ_ATTEMPTS) throw err;
        await sleep(RETRY_PAUSE_MS);
      }
    }
  } finally {
    clearTimeout(timer);
    if (slow) { pendingSlow -= 1; emit(); }
  }
}
