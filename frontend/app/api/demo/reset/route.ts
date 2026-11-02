import "server-only";

// Server-side reset proxy (PRD FR-116, DEC-002).
// Intentionally public: anyone who can open the demo can trigger a reset, which is acceptable
// only because all data is synthetic (A-06).
// This route ONLY adds the token and forwards. It performs no confirmation check, no rate
// limiting and no retries; the backend enforces all of that on every request.
// The token comes from a server-only env var (no NEXT_PUBLIC_ prefix) and never reaches the browser.
// The path matches the backend's /api/demo/reset; that is safe only because the two apps run on
// different origins. Rename this route if they are ever served from one origin.

export const dynamic = "force-dynamic";

// Client address from the hosting platform's forwarded headers (Vercel sets x-real-ip and
// x-forwarded-for; the Next.js server adds x-forwarded-for locally). App Router route handlers
// do not expose the raw socket address, so when no header is present nothing is forwarded and
// the backend falls back to this server's socket address.
function clientAddress(request: Request): string | undefined {
  const realIp = request.headers.get("x-real-ip")?.trim();
  if (realIp) return realIp;
  const first = request.headers.get("x-forwarded-for")?.split(",")[0]?.trim();
  return first || undefined;
}

export async function POST(request: Request): Promise<Response> {
  const backend = process.env.BACKEND_URL?.replace(/\/+$/, "");
  if (!backend) {
    console.error("demo reset proxy: BACKEND_URL is not set");
    return Response.json(
      { error: "proxy_not_configured", message: "The reset service is not configured on the server." },
      { status: 500 },
    );
  }

  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = process.env.DEMO_RESET_TOKEN;
  if (token) headers["X-Demo-Reset-Token"] = token;
  // If the token is unset, the request is forwarded without it and the backend returns 401.

  // DEC-011: pass the browser's address so the backend's per-IP rejected-attempt limit applies
  // per visitor, not to this server as a whole. The backend trusts it only alongside a valid token.
  const clientIp = clientAddress(request);
  if (clientIp) headers["X-Client-IP"] = clientIp;

  let upstream: Response;
  try {
    upstream = await fetch(`${backend}/api/demo/reset`, {
      method: "POST",
      headers,
      body: await request.text(),
      cache: "no-store",
    });
  } catch {
    console.error("demo reset proxy: backend unreachable");
    return Response.json(
      { error: "backend_unreachable", message: "Could not reach the backend. No data was changed." },
      { status: 502 },
    );
  }

  // Status code and body returned unchanged.
  return new Response(await upstream.text(), {
    status: upstream.status,
    headers: { "Content-Type": upstream.headers.get("Content-Type") ?? "application/json" },
  });
}
