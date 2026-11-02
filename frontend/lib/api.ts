import "server-only";

// Server-side calls to the FastAPI backend. BACKEND_URL is a server-only env var.

export type Meta = {
  product: string;
  product_version: string;
  prd_version: string;
  policy_version: string;
  policy_version_short: string;
  fixture_version: string;
  scope: Record<string, string | boolean>;
};

export function backendUrl(): string | undefined {
  return process.env.BACKEND_URL?.replace(/\/+$/, "") || undefined;
}

export async function getMeta(): Promise<Meta> {
  const base = backendUrl();
  if (!base) throw new Error("BACKEND_URL is not set");
  const res = await fetch(`${base}/api/meta`, { cache: "no-store" });
  if (!res.ok) throw new Error(`GET /api/meta returned ${res.status}`);
  return res.json();
}
