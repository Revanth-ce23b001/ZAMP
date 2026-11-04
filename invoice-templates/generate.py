"""Render the sample invoices in manifest.json to backend/samples/*.pdf.

    python invoice-templates/generate.py            # generate all, write SHA256SUMS
    python invoice-templates/generate.py --only T-01,T-05
    python invoice-templates/generate.py --check    # regenerate to a temp dir; fail if any hash differs
    python invoice-templates/generate.py --manifest invoice-templates/extra/manifest.json
                                                    # the interviewer kit (not cached, not in backend/samples)

HTML (Jinja2 layout) -> PDF via Playwright/Chromium -> rewritten by PyMuPDF with fixed metadata
and no new /ID, so the same manifest gives byte-identical PDFs on the same machine and
Chromium build. Entries with a "scan" block are rasterised and degraded with seeded parameters
into an image-only PDF (no text layer).
"""
import argparse
import hashlib
import io
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

import numpy as np
import pymupdf
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from PIL import Image

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "backend"))
from app.utils.money import format_inr, rupees_to_paise  # noqa: E402

MANIFEST = HERE / "manifest.json"
BUILD = HERE / "build"
TAX_ORDER = (("cgst", "CGST"), ("sgst", "SGST"), ("igst", "IGST"))


def load_manifest(path: Path = MANIFEST) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- rendering

def inr(amount: str | None) -> str:
    """'113850.00' -> '1,13,850.00' (Indian grouping, no currency symbol)."""
    if amount is None:
        return ""
    return format_inr(rupees_to_paise(amount)).replace("₹", "")


def fmt_date(iso: str, pattern: str) -> str:
    return date.fromisoformat(iso).strftime(pattern)


def fmt_rate(rate) -> str:
    return str(int(rate)) if float(rate).is_integer() else str(rate)


def template_env() -> Environment:
    env = Environment(loader=FileSystemLoader(HERE / "layouts"), autoescape=True, undefined=StrictUndefined)
    env.filters["inr"] = inr
    env.filters["date"] = fmt_date
    return env


def context(inv: dict, buyer: str) -> dict:
    taxes = [
        {"label": label, "rate": fmt_rate(inv["tax"][key]["rate"]), "amount": inv["tax"][key]["amount"]}
        for key, label in TAX_ORDER if inv["tax"].get(key)
    ]
    tax_total = sum(rupees_to_paise(t["amount"]) for t in taxes)
    return {
        "inv": inv,
        "vendor": inv["vendor"],
        "buyer": buyer,
        "bank": inv.get("bank"),
        "taxes": taxes,
        "tax_total": f"{tax_total // 100}.{tax_total % 100:02d}",
    }


def render_html(env: Environment, inv: dict, buyer: str) -> str:
    return env.get_template(f"{inv['layout']}.html").render(**context(inv, buyer))


# ---------------------------------------------------------------- PDF post-processing

def _metadata(inv: dict, fixed_date: str) -> dict:
    return {
        "title": inv["invoice_number"],
        "author": inv["vendor"]["legal_name"],
        "subject": f"ClearMatch AP sample invoice {inv['test_id']} (synthetic)",
        "keywords": "",
        "creator": "ClearMatch AP invoice-templates",
        "producer": "ClearMatch AP invoice-templates",
        "creationDate": fixed_date,
        "modDate": fixed_date,
    }


def normalise_pdf(pdf: bytes, inv: dict, fixed_date: str) -> bytes:
    """Fixed metadata, no XMP, no new /ID, garbage-collected: byte-stable output."""
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    doc.set_metadata(_metadata(inv, fixed_date))
    doc.del_xml_metadata()
    out = doc.tobytes(garbage=4, deflate=True, no_new_id=True)
    doc.close()
    return out


def degrade_to_scan(clean_pdf: bytes, inv: dict, fixed_date: str) -> bytes:
    """Rasterise, rotate, greyscale, add seeded gaussian noise, JPEG-compress, and wrap as an
    image-only PDF. Every parameter comes from the manifest's "scan" block."""
    p = inv["scan"]
    src = pymupdf.open(stream=clean_pdf, filetype="pdf")
    out = pymupdf.open()
    rng = np.random.default_rng(p["seed"])
    for page in src:
        pix = page.get_pixmap(dpi=p["dpi"], alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        if p.get("grayscale"):
            img = img.convert("L")
        fill = 255 if img.mode == "L" else (255, 255, 255)
        img = img.rotate(p["rotate_degrees"], resample=Image.Resampling.BICUBIC, expand=True, fillcolor=fill)
        arr = np.asarray(img, dtype=np.float32)
        arr = arr + rng.normal(0.0, p["noise_sigma"], size=arr.shape).astype(np.float32)
        img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), mode=img.mode)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=p["jpeg_quality"], optimize=False, progressive=False)
        w_pt, h_pt = img.width * 72 / p["dpi"], img.height * 72 / p["dpi"]
        new_page = out.new_page(width=w_pt, height=h_pt)
        new_page.insert_image(new_page.rect, stream=buf.getvalue())
    src.close()
    out.set_metadata(_metadata(inv, fixed_date))
    data = out.tobytes(garbage=4, deflate=True, no_new_id=True)
    out.close()
    return data


# ---------------------------------------------------------------- driver

def generate(selected: list[dict], out_dir: Path, meta: dict, keep_html: bool = True) -> dict[str, str]:
    from playwright.sync_api import sync_playwright

    env = template_env()
    out_dir.mkdir(parents=True, exist_ok=True)
    BUILD.mkdir(exist_ok=True)
    hashes = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for inv in selected:
                html = render_html(env, inv, meta["buyer"])
                if keep_html:
                    (BUILD / (Path(inv["file"]).stem + ".html")).write_text(html, encoding="utf-8")
                page = browser.new_page()
                page.set_content(html, wait_until="load")
                raw = page.pdf(format="A4", print_background=True, prefer_css_page_size=True)
                page.close()
                pdf = normalise_pdf(raw, inv, meta["pdf_metadata_date"])
                if inv.get("scan"):
                    (BUILD / (Path(inv["file"]).stem + "_clean.pdf")).write_bytes(pdf)
                    pdf = degrade_to_scan(pdf, inv, meta["pdf_metadata_date"])
                (out_dir / inv["file"]).write_bytes(pdf)
                hashes[inv["file"]] = hashlib.sha256(pdf).hexdigest()
                print(f"{inv['test_id']:5} {inv['layout']:8} {inv['file']}  {len(pdf):>8} bytes  {hashes[inv['file']][:12]}")
        finally:
            browser.close()
    return hashes


def write_sums(out_dir: Path) -> None:
    lines = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}" for p in sorted(out_dir.glob("*.pdf"))]
    (out_dir / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="comma-separated test IDs")
    ap.add_argument("--check", action="store_true", help="regenerate to a temp dir and compare hashes")
    ap.add_argument("--manifest", default=str(MANIFEST), help="manifest file (default: the sample set)")
    args = ap.parse_args()

    manifest = load_manifest(args.manifest)
    meta = manifest["_meta"]
    invoices = manifest["invoices"]
    if args.only:
        wanted = {t.strip() for t in args.only.split(",")}
        invoices = [i for i in invoices if i["test_id"] in wanted]
        missing = wanted - {i["test_id"] for i in invoices}
        if missing:
            print(f"unknown test IDs: {sorted(missing)}", file=sys.stderr)
            return 2
    out_dir = REPO / meta["output_dir"]

    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            fresh = generate(invoices, Path(tmp), meta, keep_html=False)
        bad = []
        for name, digest in fresh.items():
            existing = out_dir / name
            if not existing.exists() or hashlib.sha256(existing.read_bytes()).hexdigest() != digest:
                bad.append(name)
        if bad:
            print(f"NOT DETERMINISTIC / OUT OF DATE: {bad}", file=sys.stderr)
            return 1
        print(f"OK: {len(fresh)} PDFs regenerate byte-identical")
        return 0

    generate(invoices, out_dir, meta)
    write_sums(out_dir)
    print(f"wrote {out_dir / 'SHA256SUMS'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
