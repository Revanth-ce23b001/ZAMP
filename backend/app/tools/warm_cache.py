"""Warm the extraction cache for the sample PDFs and export the snapshot (DEC-014).

    python -m app.tools.warm_cache                 # extract uncached samples, then export
    python -m app.tools.warm_cache --only T15,T04 --force   # re-extract specific files
    python -m app.tools.warm_cache --export-only

Runs the same ingest + extraction chain as the worker, with the cache enabled: a sample whose
hash is already cached is skipped (no provider call) unless --force. Calls are paced to stay
under per-minute quotas. Reads credentials from backend/.env.
"""
import argparse
import hashlib
import sys
import time

from dotenv import load_dotenv

from app.config import BACKEND_DIR


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated file-name prefixes, e.g. T15,T04")
    ap.add_argument("--force", action="store_true", help="re-extract even if cached")
    ap.add_argument("--export-only", action="store_true")
    ap.add_argument("--interval", type=float, default=13.0, help="seconds between provider calls")
    args = ap.parse_args()

    load_dotenv(BACKEND_DIR / ".env")
    from app.config import load_settings
    from app.db.cache_snapshot import export_snapshot
    from app.db.connection import connect
    from app.db.migrate import migrate
    from app.policy import load_policy
    from app.services.extraction import adapter_factory
    from app.services.extraction import cache
    from app.services.extraction.chain import run_chain
    from app.services.ingest import ingest
    from app.utils.timeutil import now_iso

    settings = load_settings(require_reset_token=False)
    settings.validate_chains()
    policy = load_policy()
    conn = connect(settings.database_path)
    migrate(conn)
    snapshot = settings.cache_snapshot or BACKEND_DIR / "seed" / "extraction_cache.json"

    if not args.export_only:
        adapter_for = adapter_factory(settings)
        files = sorted(settings.samples_dir.glob("*.pdf"))
        if args.only:
            prefixes = tuple(p.strip() for p in args.only.split(","))
            files = [f for f in files if f.name.startswith(prefixes)]
        last = 0.0
        for f in files:
            h = hashlib.sha256(f.read_bytes()).hexdigest()
            if cache.get(conn, h) and not args.force:
                print(f"{f.name}: cached, skipped")
                continue
            pages = ingest(f, policy.assumption("A-24"),
                           policy.data["other_assumptions"]["render_dpi"]["value"]).pages
            chain = settings.vision_chain if pages.method == "vision" else settings.text_chain
            wait = args.interval - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
            last = time.monotonic()

            def report(att, fell_to):
                if not att.ok:
                    print(f"   {att.entry}: {att.error_kind} - {str(att.error)[:120]}"
                          + (f" -> falling back to {fell_to}" if fell_to else ""))

            try:
                res = run_chain(chain, adapter_for, pages, report)
            except Exception as exc:  # noqa: BLE001
                print(f"{f.name}: FAILED {str(exc)[:200]}")
                continue
            conn.execute("BEGIN IMMEDIATE")
            cache.put(conn, h, res.provider, res.model, pages.method, res.extraction, now_iso())
            conn.execute("COMMIT")
            u = res.usage
            print(f"{f.name}: {pages.method} via {res.provider}:{res.model} "
                  f"(in {u.input_tokens}, out {u.output_tokens}, calls {u.calls})")

    n = export_snapshot(conn, snapshot, settings.samples_dir)
    print(f"exported {n} rows to {snapshot}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
