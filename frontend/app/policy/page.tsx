import { getMeta, type Meta } from "@/lib/api";

// FR-117. Rendered on the server at request time so the version is always live.
export const dynamic = "force-dynamic";

export default async function PolicyPage() {
  let meta: Meta | null = null;
  try {
    meta = await getMeta();
  } catch {
    meta = null;
  }

  return (
    <section className="max-w-3xl">
      <h1 className="text-2xl font-semibold">Assumptions and Policy</h1>
      <p className="mt-2 text-gray-600">
        Every threshold is a prototype assumption [PA] from <code>policy.yaml</code>, not a Zamp policy.
      </p>

      {meta ? (
        <dl className="mt-6 rounded border border-gray-200 bg-white p-4 text-sm">
          <dt className="text-gray-500">Policy version (SHA-256 of policy.yaml)</dt>
          <dd className="mt-1 break-all font-mono" data-testid="policy-version">
            {meta.policy_version}
          </dd>
        </dl>
      ) : (
        <div role="alert" className="mt-6 rounded border border-red-200 bg-red-50 p-4 text-sm text-red-800">
          Policy could not be loaded. Check that the backend is running.
        </div>
      )}

      <p className="mt-6 text-sm text-gray-500">
        The full policy table, reason codes and the &ldquo;does not verify&rdquo; list arrive with the backend
        policy endpoint on a later day.
      </p>
    </section>
  );
}
