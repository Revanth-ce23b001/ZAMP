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
