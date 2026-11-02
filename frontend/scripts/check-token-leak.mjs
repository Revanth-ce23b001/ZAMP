// Build-output check: the demo reset token must never reach the build output (PRD FR-116 AC3).
//
// 1. Runs `next build` with DEMO_RESET_TOKEN set to a random sentinel value.
// 2. Fails if the sentinel value appears in ANY file under .next (server or client).
// 3. Also fails if the real token from .env.local (when set) appears anywhere under .next.
// 4. Fails if the variable NAME appears in client assets (.next/static); the server route
//    legitimately reads process.env.DEMO_RESET_TOKEN, so the name may appear in server code only.
// 5. Fails if any source file uses a NEXT_PUBLIC_ variant of a server-only variable.
// Positive control: the compiled reset route must be present, so an empty build cannot pass.
//
// Usage (from frontend/): npm run test:token-leak
import { spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const nextDir = join(root, ".next");
const failures = [];

function walk(dir, out = []) {
  if (!existsSync(dir)) return out;
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) walk(p, out);
    else out.push(p);
  }
  return out;
}

function filesContaining(files, needle) {
  const bytes = Buffer.from(needle, "utf8");
  return files.filter((f) => readFileSync(f).includes(bytes)).map((f) => relative(root, f));
}

function realTokenFromEnvLocal() {
  const file = join(root, ".env.local");
  if (!existsSync(file)) return null;
  const line = readFileSync(file, "utf8").split(/\r?\n/).find((l) => l.startsWith("DEMO_RESET_TOKEN="));
  const value = line?.slice("DEMO_RESET_TOKEN=".length).trim().replace(/^["']|["']$/g, "");
  return value && value.length >= 8 ? value : null;
}

// ---- 1. build with a sentinel token
const sentinel = "leakcheck-" + randomBytes(16).toString("hex");
const env = { ...process.env, DEMO_RESET_TOKEN: sentinel, BACKEND_URL: process.env.BACKEND_URL || "http://127.0.0.1:9" };
for (const k of Object.keys(env)) if (k.startsWith("NEXT_PUBLIC_")) delete env[k];

console.log("Building with a sentinel DEMO_RESET_TOKEN...");
const nextBin = join(root, "node_modules", "next", "dist", "bin", "next");
const build = spawnSync(process.execPath, [nextBin, "build"], { cwd: root, env, stdio: "inherit" });
if (build.status !== 0) {
  console.error("FAIL: next build failed");
  process.exit(1);
}

const all = walk(nextDir);
const client = walk(join(nextDir, "static"));
console.log(`Scanning ${all.length} files under .next (${client.length} client assets in .next/static)`);

// ---- positive control
const routeFiles = filesContaining(walk(join(nextDir, "server")), "X-Demo-Reset-Token");
if (routeFiles.length === 0) failures.push("positive control: compiled reset route not found in .next/server");
else console.log(`Positive control OK: reset route compiled (${routeFiles[0]})`);
if (client.length === 0) failures.push("positive control: no client assets found in .next/static");

// ---- 2. sentinel value anywhere
const sentinelHits = filesContaining(all, sentinel);
if (sentinelHits.length) failures.push(`token value found in build output: ${sentinelHits.join(", ")}`);

// ---- 3. real token from .env.local anywhere
const real = realTokenFromEnvLocal();
if (real) {
  const hits = filesContaining(all, real);
  if (hits.length) failures.push(`.env.local token value found in build output: ${hits.join(", ")}`);
  else console.log("Checked the .env.local token value too: not present");
}

// ---- 4. variable name in client assets
const nameHits = filesContaining(client, "DEMO_RESET_TOKEN");
if (nameHits.length) failures.push(`DEMO_RESET_TOKEN referenced in client assets: ${nameHits.join(", ")}`);

// ---- 5. NEXT_PUBLIC_ variants in source
const sources = ["app", "components", "lib"].flatMap((d) => walk(join(root, d)));
for (const bad of ["NEXT_PUBLIC_DEMO_RESET_TOKEN", "NEXT_PUBLIC_BACKEND_URL"]) {
  const hits = filesContaining(sources, bad);
  if (hits.length) failures.push(`${bad} used in source: ${hits.join(", ")}`);
}

if (failures.length) {
  for (const f of failures) console.error("FAIL: " + f);
  process.exit(1);
}
console.log("PASS: DEMO_RESET_TOKEN value is not in any file under .next; name absent from client assets");
